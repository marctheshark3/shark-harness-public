// Independent T3 display firmware. Hardware pin assignments are documented in
// docs/device-integration.md. The panel implementation is Espressif's driver.
#include <math.h>
#include <ctype.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "driver/spi_master.h"
#include "driver/usb_serial_jtag.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_lcd_co5300.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_psram.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"
#include "touch.h"
#include "audio.h"
#include "frame_rle.h"
#include "voice_ring.h"
#include "esp_rom_crc.h"
#include "miniz.h"

#define WIDTH 466
#define HEIGHT 466
#define FRAME_BYTES (WIDTH * HEIGHT * 2)
#define STRIP 16
#define LINE_BYTES 24576
#define CHUNK_BYTES 16384
#define ZLIB_BYTES 65536
#define BRIGHTNESS_ACTIVE 25
#define BRIGHTNESS_IDLE 6

static const char *TAG = "shark";
static esp_lcd_panel_handle_t panel;
static SemaphoreHandle_t dma_done, image_lock;
static uint8_t *pixels, *front, *back;
static uint8_t *compressed_frame;
static uint8_t *upload_chunk;
static tinfl_decompressor *inflater;
static TaskHandle_t render_task;
static atomic_bool ready;
static atomic_bool has_image;
static atomic_bool uploading;
static size_t upload_offset;
static size_t upload_bytes, decoded_offset;
static bool upload_rle;
static bool upload_zlib;
static uint32_t expected_crc, last_crc;
static uint32_t upload_ui_token, front_ui_token;
static bool upload_voice = true, front_voice = true;
static atomic_uint image_version, presented_version, presented_ms;
static int64_t expires_us, idle_started_us;
static atomic_uint frames;
static char state[16] = "idle";
static atomic_int boot_error;
static atomic_uint ambient_scene;
static atomic_uint brightness = BRIGHTNESS_ACTIVE;
static const char *SCENE_NAMES[] = {"aurora", "orbit", "ember"};

static uint32_t crc32(const uint8_t *data, size_t length) {
    return esp_rom_crc32_le(0, data, length);
}

static bool color_done(esp_lcd_panel_io_handle_t io,
                       esp_lcd_panel_io_event_data_t *event, void *context) {
    (void)io; (void)event; (void)context;
    BaseType_t wake = pdFALSE;
    xSemaphoreGiveFromISR(dma_done, &wake);
    return wake == pdTRUE;
}

static esp_err_t display_init(void) {
    dma_done = xSemaphoreCreateBinary();
    image_lock = xSemaphoreCreateMutex();
    if (!dma_done || !image_lock) return ESP_ERR_NO_MEM;
    pixels = heap_caps_malloc(WIDTH * STRIP * 2, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
    front = heap_caps_malloc(FRAME_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    back = heap_caps_malloc(FRAME_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    compressed_frame = heap_caps_malloc(ZLIB_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    inflater = heap_caps_malloc(sizeof(*inflater), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    upload_chunk = heap_caps_malloc(CHUNK_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!pixels || !front || !back || !compressed_frame || !inflater || !upload_chunk) return ESP_ERR_NO_MEM;

    const spi_bus_config_t bus = CO5300_PANEL_BUS_QSPI_CONFIG(38, 4, 5, 6, 7, WIDTH * STRIP * 2);
    ESP_RETURN_ON_ERROR(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO), TAG, "SPI init");
    esp_lcd_panel_io_spi_config_t io_config = CO5300_PANEL_IO_QSPI_CONFIG(12, color_done, NULL);
    io_config.trans_queue_depth = 1;
    esp_lcd_panel_io_handle_t io;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_spi(SPI2_HOST, &io_config, &io), TAG, "Panel IO");
    // Use the official driver's register sequence, with this panel's reset/gap.
    co5300_vendor_config_t vendor = {.flags.use_qspi_interface = 1};
    const esp_lcd_panel_dev_config_t config = {
        .reset_gpio_num = 39, .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .bits_per_pixel = 16, .vendor_config = &vendor,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_co5300(io, &config, &panel), TAG, "CO5300 init");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_reset(panel), TAG, "Panel reset");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(panel), TAG, "Panel init");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_set_gap(panel, 6, 0), TAG, "Panel gap");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_co5300_set_brightness(panel, BRIGHTNESS_ACTIVE), TAG, "Brightness");
    return esp_lcd_panel_disp_on_off(panel, true);
}

static void rgb(uint8_t *out, unsigned r, unsigned g, unsigned b) {
    uint16_t value = ((r & 248U) << 8) | ((g & 252U) << 3) | (b >> 3);
    out[0] = value >> 8; out[1] = value & 255; // Panel and wire are RGB565 big-endian.
}

static bool logo(int x, int y) {
    // Tiny original 5x7 T3 wordmark. No font assets are required for bring-up.
    static const uint8_t glyphs[2][7] = {{31,4,4,4,4,4,4}, {30,1,1,14,1,1,30}};
    int px = x - 211, py = y - 58;
    if (px < 0 || py < 0 || py >= 28 || px >= 44) return false;
    int letter = px / 24, column = (px % 24) / 4;
    return letter < 2 && column < 5 && (glyphs[letter][py / 4] & (1 << (4 - column)));
}

static void render(void *unused) {
    (void)unused;
    unsigned handled_taps = 0;
    int64_t tapped_us = -1000000;
    int tap_x = 233, tap_y = 233;
    bool ui_was_active = false;
    float voice_level = 0;
    voice_ring_t voice_ring;
    while (true) {
        int64_t now_us = esp_timer_get_time();
        float seconds = (float)now_us / 1000000.0f;
        int wave[WIDTH];
        for (int x = 0; x < WIDTH; x++)
            wave[x] = 233 + (int)(54 * sinf(x * 0.012f + seconds * 0.2f)
                                  + 22 * sinf(x * 0.022f - seconds * 0.3f));
        xSemaphoreTake(image_lock, portMAX_DELAY);
        int64_t display_now_us = esp_timer_get_time();
        touch_status_t touch = touch_status();
        if (!touch.idle && idle_started_us) {
            // Preserve the remaining image lifetime across arbitrarily long
            // idle periods; the five-second host lease still expires normally.
            expires_us += display_now_us - idle_started_us;
            idle_started_us = 0;
        }
        if (!touch.idle && has_image && expires_us && display_now_us >= expires_us) has_image = false;
        if (ui_was_active && !touch.ui) { has_image = false; touch_frame(0); }
        ui_was_active = touch.ui;
        // Freeze the source for this entire DMA frame. A touch can wake during
        // drawing, but this ambient frame must never count as restored controls.
        unsigned target_brightness = touch.idle ? BRIGHTNESS_IDLE : BRIGHTNESS_ACTIVE;
        if (brightness != target_brightness) {
            esp_err_t error = esp_lcd_panel_co5300_set_brightness(panel, target_brightness);
            if (error != ESP_OK) {
                ready = false; boot_error = error;
                xSemaphoreGive(image_lock); vTaskDelete(NULL); return;
            }
            brightness = target_brightness;
        }
        unsigned new_taps = touch.taps - handled_taps;
        handled_taps = touch.taps;
        if (new_taps && !touch.ui) {
            if (audio_tap()) new_taps--;
            // Dismiss only the visible image; the host does not resend an
            // unchanged scene. Further taps cycle the native ambient renderer.
            if (new_taps && has_image) { has_image = false; new_taps--; }
            ambient_scene = (ambient_scene + new_taps % 3) % 3;
            tapped_us = now_us; tap_x = touch.x; tap_y = touch.y;
        }
        bool show_image = has_image && !touch.idle;
        unsigned style = ambient_scene;
        bool speaking = audio_status().playing;
        mic_status_t microphone = mic_status();
        voice_level = microphone.recording ? voice_level * 0.65f + microphone.level * 0.35f : 0;
        if (speaking || microphone.recording)
            voice_ring_prepare(&voice_ring, seconds, (unsigned)voice_level, microphone.recording);
        int comet_x[3], comet_y[3];
        const int radii[3] = {65, 116, 169};
        for (int i = 0; i < 3; i++) {
            float angle = seconds * (0.22f + i * 0.08f) + i * 2.1f;
            comet_x[i] = 233 + (int)(radii[i] * cosf(angle));
            comet_y[i] = 233 + (int)(radii[i] * sinf(angle));
        }
        int64_t age_ms = (now_us - tapped_us) / 1000;
        bool ripple = age_ms >= 0 && age_ms < 600;
        int ripple_age = ripple ? (int)age_ms : 600;
        int ripple_radius = 6 + ripple_age * 80 / 600;
        for (int y = 0; y < HEIGHT; y += STRIP) {
            int rows = y + STRIP > HEIGHT ? HEIGHT - y : STRIP;
            if (show_image) memcpy(pixels, front + y * WIDTH * 2, rows * WIDTH * 2);
            else for (int row = 0; row < rows; row++) for (int x = 0; x < WIDTH; x++) {
                int yy = y + row, dx = x - 233, dy = yy - 233;
                int distance = dx * dx + dy * dy;
                int light = 70 - abs(yy - wave[x]);
                if (light < 0) light = 0;
                bool ribbon = light > 0 && ((yy - wave[x] + 72) % 5 < 2);
                unsigned r = 3 + light / 3, g = 11 + light, b = 18 + light;
                if (ribbon) { r += 14; g += 24; b += 22; }
                if (style == 1) {
                    r = 4; g = 12; b = 22;
                    for (int i = 0; i < 3; i++) {
                        int radius = radii[i];
                        if (distance > (radius - 1) * (radius - 1) && distance < (radius + 1) * (radius + 1))
                            { r = 25; g = 48; b = 65; }
                        int cx = x - comet_x[i], cy = yy - comet_y[i];
                        int glow = 160 - (cx * cx + cy * cy) * 2;
                        if (glow > 0) { r = 30 + glow / 2; g = 55 + glow; b = 75 + glow; }
                    }
                } else if (style == 2) {
                    r = 14 + light * 2; g = 5 + light / 2; b = 21 + light;
                    if (ribbon) { r += 32; g += 18; b += 20; }
                }
                if (!touch.idle && distance > 216 * 216 && distance < 218 * 218) {
                    bool waiting = strcmp(state, "waiting") == 0;
                    r = waiting ? 140 : 30; g = waiting ? 104 : 102; b = waiting ? 50 : 96;
                }
                // Avoid fixed bright UI/wordmark elements during AMOLED idle.
                if (!touch.idle && logo(x, yy)) { r = 170; g = 214; b = 217; }
                if (!touch.idle && yy >= 396 && yy <= 404) for (unsigned i = 0; i < 3; i++) {
                    int px = x - (220 + (int)i * 13), py = yy - 400;
                    if (px * px + py * py <= 9) {
                        r = i == style ? 160 : 30; g = i == style ? 211 : 53; b = i == style ? 216 : 62;
                    }
                }
                if (ripple) {
                    int rx = x - tap_x, ry = yy - tap_y, rd = rx * rx + ry * ry;
                    if (rd > (ripple_radius - 2) * (ripple_radius - 2)
                        && rd < (ripple_radius + 2) * (ripple_radius + 2)) {
                        unsigned alpha = (600 - ripple_age) * 180 / 600;
                        r = (r * (255 - alpha) + 180 * alpha) / 255;
                        g = (g * (255 - alpha) + 245 * alpha) / 255;
                        b = (b * (255 - alpha) + 250 * alpha) / 255;
                    }
                }
                if (distance > 231 * 231) r = g = b = 0;
                rgb(pixels + (row * WIDTH + x) * 2, r, g, b);
            }
            // Exactly one native voice visual, including over host images. The
            // microphone drives the circular waveform; playback uses a mint pulse.
            if (speaking || microphone.recording)
                voice_ring_draw(&voice_ring, pixels, WIDTH, y, rows);
            esp_err_t error = esp_lcd_panel_draw_bitmap(panel, 0, y, WIDTH, y + rows, pixels);
            if (error != ESP_OK || !xSemaphoreTake(dma_done, pdMS_TO_TICKS(1000))) {
                ready = false; boot_error = error == ESP_OK ? ESP_ERR_TIMEOUT : error;
                xSemaphoreGive(image_lock); vTaskDelete(NULL); return;
            }
        }
        frames++;
        if (show_image && presented_version != image_version) {
            presented_ms = (uint64_t)esp_timer_get_time() / 1000;
            presented_version = image_version;
        }
        // Uploading a replacement does not change the controls on the visible
        // image. Home cards share a control token; other screens still cancel.
        if (touch.ui && show_image && !uploading) touch_idle_presented(image_version);
        if (!uploading) touch_frame(touch.ui && show_image ? front_ui_token : 0);
        touch_voice_ready(touch.ui && show_image && !uploading && front_voice);
        xSemaphoreGive(image_lock);
        // A committed image wakes the panel immediately instead of waiting for
        // the next ambient-animation interval.
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(80));
    }
}

static cJSON *response_extra;
static void reply(int id, bool ok, const char *error) {
    // The USB interface uses our own newline-delimited JSON identity, not the
    // vendor's cable framing. Only this task writes protocol responses.
    touch_status_t touch = touch_status();
    audio_status_t audio = audio_status();
    char line[1024];
    int length = snprintf(line, sizeof(line),
        "{\"product\":\"shark-display\",\"protocol\":1,\"id\":%d,\"ok\":%s,"
        "\"ready\":%s,\"imageActive\":%s,\"width\":466,\"height\":466,\"frames\":%lu,"
        "\"offset\":%u,\"crc32\":\"%08lx\",\"error\":\"%s\",\"bootError\":%d,"
        "\"ambientScene\":\"%s\",\"touch\":{\"ready\":%s,\"pressed\":%s,"
        "\"taps\":%u,\"x\":%u,\"y\":%u,\"error\":%d},"
        "\"audio\":{\"ready\":%s,\"uploading\":%s,\"armed\":%s,\"playing\":%s,\"stopped\":%s,"
        "\"offset\":%u,\"bytes\":%u,\"played\":%u,\"volume\":%u,\"crc32\":\"%08lx\",\"error\":%d}}\n",
        id, ok ? "true" : "false", ready ? "true" : "false", has_image ? "true" : "false", (unsigned long)frames,
        (unsigned)upload_offset, (unsigned long)last_crc, error ? error : "", boot_error,
        SCENE_NAMES[ambient_scene], touch.ready ? "true" : "false", touch.pressed ? "true" : "false",
        touch.taps, touch.x, touch.y, touch.error,
        audio.ready ? "true" : "false", audio.uploading ? "true" : "false",
        audio.armed ? "true" : "false",
        audio.playing ? "true" : "false", audio.stopped ? "true" : "false",
        audio.offset, audio.bytes, audio.played, audio.volume, (unsigned long)audio.crc, audio.error);
    if (length > 0 && length < (int)sizeof(line)) {
        cJSON *response = cJSON_Parse(line);
        if (!response) return;
        mic_json(response);
        cJSON_AddBoolToObject(response, "frameRle", true);
        cJSON_AddNumberToObject(response, "frameZlibMaxBytes", ZLIB_BYTES);
        cJSON_AddNumberToObject(response, "frameChunkBytes", CHUNK_BYTES);
        cJSON *ui = cJSON_AddObjectToObject(response, "ui");
        cJSON_AddBoolToObject(ui, "active", touch.ui);
        cJSON_AddBoolToObject(ui, "buttonReady", touch.button_ready);
        cJSON_AddBoolToObject(ui, "buttonPressed", touch.button_pressed);
        cJSON_AddNumberToObject(ui, "event", touch.gestures);
        cJSON_AddNumberToObject(ui, "gesture", touch.gesture);
        cJSON_AddNumberToObject(ui, "x", touch.gesture_x);
        cJSON_AddNumberToObject(ui, "y", touch.gesture_y);
        char checksum[9]; snprintf(checksum, sizeof(checksum), "%08lx", (unsigned long)touch.gesture_frame);
        cJSON_AddStringToObject(ui, "eventFrame", checksum);
        snprintf(checksum, sizeof(checksum), "%08lx", (unsigned long)touch.frame);
        cJSON_AddStringToObject(ui, "frame", checksum);
        cJSON_AddBoolToObject(ui, "eventQueue", true);
        cJSON_AddBoolToObject(ui, "voiceGate", true);
        cJSON_AddNumberToObject(ui, "layoutVersion", 2);
        cJSON_AddBoolToObject(ui, "voiceRing", true);
        cJSON_AddBoolToObject(ui, "idleMode", true);
        cJSON_AddBoolToObject(ui, "idle", touch.idle);
        cJSON_AddNumberToObject(ui, "wake", touch.wake);
        cJSON_AddBoolToObject(ui, "wakePending", touch.wake_pending);
        cJSON_AddNumberToObject(ui, "brightness", brightness);
        cJSON_AddBoolToObject(ui, "holdToTalk", front_voice && !touch.idle && !touch.wake_pending);
        cJSON_AddNumberToObject(ui, "dropped", touch.dropped);
        cJSON_AddNumberToObject(ui, "contacts", touch.contacts);
        cJSON_AddNumberToObject(ui, "releases", touch.releases);
        cJSON_AddNumberToObject(ui, "tapDrops", touch.tap_drops);
        cJSON_AddNumberToObject(ui, "nowMs", (uint32_t)((uint64_t)esp_timer_get_time() / 1000));
        cJSON_AddNumberToObject(ui, "imageVersion", image_version);
        cJSON_AddNumberToObject(ui, "presentedVersion", presented_version);
        cJSON_AddNumberToObject(ui, "presentedMs", presented_ms);
        cJSON *events = cJSON_AddArrayToObject(ui, "events");
        for (unsigned i = 0; i < touch.event_count; i++) {
            touch_event_t item = touch.events[i];
            cJSON *event = cJSON_CreateObject();
            cJSON_AddNumberToObject(event, "event", item.id);
            cJSON_AddNumberToObject(event, "gesture", item.gesture);
            cJSON_AddNumberToObject(event, "x", item.x);
            cJSON_AddNumberToObject(event, "y", item.y);
            cJSON_AddNumberToObject(event, "startedMs", item.started_ms);
            cJSON_AddNumberToObject(event, "releasedMs", item.released_ms);
            snprintf(checksum, sizeof(checksum), "%08lx", (unsigned long)item.frame);
            cJSON_AddStringToObject(event, "eventFrame", checksum);
            cJSON_AddItemToArray(events, event);
        }
        if (response_extra) {
            cJSON *item;
            cJSON_ArrayForEach(item, response_extra)
                cJSON_AddItemToObject(response, item->string, cJSON_Duplicate(item, true));
        }
        char *encoded = cJSON_PrintUnformatted(response);
        if (encoded) {
            usb_serial_jtag_write_bytes(encoded, strlen(encoded), pdMS_TO_TICKS(1000));
            usb_serial_jtag_write_bytes("\n", 1, pdMS_TO_TICKS(1000));
            free(encoded);
        }
        cJSON_Delete(response);
    }
}

static bool integer(cJSON *value, int low, int high) {
    return cJSON_IsNumber(value) && value->valuedouble >= low && value->valuedouble <= high
        && value->valuedouble == value->valueint;
}

static bool unpack_frame(void) {
    if (!upload_zlib) return decoded_offset == FRAME_BYTES;
    tinfl_init(inflater);
    size_t input = upload_bytes, output = FRAME_BYTES;
    tinfl_status status = tinfl_decompress(inflater, compressed_frame, &input, back, back, &output,
        TINFL_FLAG_PARSE_ZLIB_HEADER | TINFL_FLAG_USING_NON_WRAPPING_OUTPUT_BUF);
    return status == TINFL_STATUS_DONE && input == upload_bytes && output == FRAME_BYTES;
}

static void command(const char *line) {
    cJSON *root = cJSON_ParseWithOpts(line, NULL, true);
    if (!root) { reply(0, false, "invalid_json"); return; }
    cJSON *request = cJSON_GetObjectItemCaseSensitive(root, "id");
    cJSON *op = cJSON_GetObjectItemCaseSensitive(root, "op");
    int id = integer(request, 1, 1000000) ? request->valueint : 0;
    const char *error = NULL;
    response_extra = cJSON_CreateObject();
    if (!id || !cJSON_IsString(op)) error = "invalid_request";
    else if (!strcmp(op->valuestring, "hello") || !strcmp(op->valuestring, "status")) { /* reply below */ }
    else if (!strncmp(op->valuestring, "audio.", 6)) error = audio_command(op->valuestring, root);
    else if (!strncmp(op->valuestring, "mic.", 4)) {
        touch_status_t touch = touch_status();
        if (!strcmp(op->valuestring, "mic.start") && (touch.idle || touch.wake_pending)) error = "ui_idle_busy";
        else error = mic_command(op->valuestring, root, response_extra);
    }
    else if (!strcmp(op->valuestring, "ui.configure")) {
        cJSON *enabled = cJSON_GetObjectItemCaseSensitive(root, "enabled");
        if (!cJSON_IsBool(enabled)) error = "invalid_ui_config";
        else {
            // A new lease must not revive controls from an expired host. Frame
            // replacement and native voice gating resume only after repaint.
            if (ready && cJSON_IsTrue(enabled)) {
                xSemaphoreTake(image_lock, portMAX_DELAY);
                if (!touch_ui_configure(true)) has_image = false;
                xSemaphoreGive(image_lock);
            } else touch_ui_configure(cJSON_IsTrue(enabled));
        }
    } else if (!strcmp(op->valuestring, "ui.ack")) {
        cJSON *event = cJSON_GetObjectItemCaseSensitive(root, "event");
        if (!integer(event, 1, 1000000)) error = "invalid_ui_event";
        else touch_ack(event->valueint);
    }
    else if (!ready) error = "display_unavailable";
    else if (!strcmp(op->valuestring, "ui.idle")) {
        cJSON *enabled = cJSON_GetObjectItemCaseSensitive(root, "enabled");
        if (!cJSON_IsBool(enabled)) error = "invalid_ui_idle";
        else if (cJSON_IsTrue(enabled) && !touch_ui_enabled()) error = "ui_inactive";
        else {
            xSemaphoreTake(image_lock, portMAX_DELAY);
            audio_status_t audio = audio_status();
            mic_status_t microphone = mic_status();
            bool busy = uploading || audio.playing || audio.uploading || audio.armed
                || microphone.recording || microphone.available;
            if (cJSON_IsTrue(enabled) && busy) error = "ui_idle_busy";
            else {
                bool was_idle = touch_status().idle;
                if (!touch_idle_configure(cJSON_IsTrue(enabled))) error = "ui_idle_busy";
                else if (cJSON_IsTrue(enabled) && !was_idle) idle_started_us = esp_timer_get_time();
            }
            xSemaphoreGive(image_lock);
            if (!error) xTaskNotifyGive(render_task);
        }
    }
    else if (!strcmp(op->valuestring, "clear")) {
        touch_frame(0); touch_voice_ready(false);
        xSemaphoreTake(image_lock, portMAX_DELAY); has_image = false;
        xSemaphoreGive(image_lock); uploading = false; upload_offset = 0;
    } else if (!strcmp(op->valuestring, "state")) {
        cJSON *value = cJSON_GetObjectItemCaseSensitive(root, "state");
        if (!cJSON_IsString(value) || strlen(value->valuestring) >= sizeof(state)) error = "invalid_state";
        else { xSemaphoreTake(image_lock, portMAX_DELAY); strcpy(state, value->valuestring); xSemaphoreGive(image_lock); }
    } else if (!strcmp(op->valuestring, "frame.begin")) {
        cJSON *size = cJSON_GetObjectItemCaseSensitive(root, "bytes");
        cJSON *crc = cJSON_GetObjectItemCaseSensitive(root, "crc32");
        cJSON *token = cJSON_GetObjectItemCaseSensitive(root, "uiToken");
        cJSON *voice = cJSON_GetObjectItemCaseSensitive(root, "holdToTalk");
        cJSON *encoding = cJSON_GetObjectItemCaseSensitive(root, "encoding");
        bool rle = cJSON_IsString(encoding) && !strcmp(encoding->valuestring, "rle565");
        bool zlib = cJSON_IsString(encoding) && !strcmp(encoding->valuestring, "zlib565");
        char *end = NULL;
        unsigned long parsed = cJSON_IsString(crc) ? strtoul(crc->valuestring, &end, 16) : 0;
        bool hex = cJSON_IsString(crc) && strlen(crc->valuestring) == 8;
        for (unsigned i = 0; hex && i < 8; i++) hex = isxdigit((unsigned char)crc->valuestring[i]);
        if ((encoding && !rle && !zlib) || (token && !integer(token, 1, 2147483647))
            || (voice && !cJSON_IsBool(voice))
            || !integer(size, zlib ? 1 : rle ? 4 : FRAME_BYTES, zlib ? ZLIB_BYTES : FRAME_BYTES)
            || (rle && size->valueint % 4) || !cJSON_IsString(crc)
            || !hex || !end || *end) error = "invalid_frame";
        else { uploading = true; upload_offset = 0; expected_crc = (uint32_t)parsed;
            upload_bytes = size->valueint; decoded_offset = 0; upload_rle = rle; upload_zlib = zlib;
            upload_ui_token = token ? token->valueint : 0;
            upload_voice = !voice || cJSON_IsTrue(voice);
            touch_voice_ready(false);
            if (!upload_ui_token || upload_ui_token != front_ui_token) touch_frame(0); }
    } else if (!strcmp(op->valuestring, "frame.chunk")) {
        cJSON *offset = cJSON_GetObjectItemCaseSensitive(root, "offset");
        cJSON *data = cJSON_GetObjectItemCaseSensitive(root, "data");
        uint8_t *decoded = upload_chunk; size_t count = 0;
        if (!uploading || !integer(offset, 0, FRAME_BYTES) || (size_t)offset->valueint != upload_offset)
            error = "invalid_offset";
        else if (!cJSON_IsString(data) || mbedtls_base64_decode(decoded, CHUNK_BYTES, &count,
                    (const unsigned char *)data->valuestring, strlen(data->valuestring)) != 0
                    || count == 0 || upload_offset + count > upload_bytes) error = "invalid_chunk";
        else if (upload_rle && !frame_rle_decode(back, FRAME_BYTES, &decoded_offset, decoded, count)) {
            error = "invalid_rle"; uploading = false;
        } else {
            if (upload_zlib) memcpy(compressed_frame + upload_offset, decoded, count);
            else if (!upload_rle) { memcpy(back + upload_offset, decoded, count); decoded_offset += count; }
            upload_offset += count;
        }
    } else if (!strcmp(op->valuestring, "frame.end")) {
        cJSON *duration = cJSON_GetObjectItemCaseSensitive(root, "seconds");
        if (!uploading || upload_offset != upload_bytes
            || !integer(duration, 1, 600)) error = "incomplete_frame";
        else if (!unpack_frame()) { error = upload_zlib ? "invalid_zlib" : "incomplete_frame"; uploading = false; }
        else if (crc32(back, FRAME_BYTES) != expected_crc) { error = "crc_mismatch"; uploading = false; }
        else {
            xSemaphoreTake(image_lock, portMAX_DELAY);
            uint8_t *previous = front; front = back; back = previous;
            last_crc = expected_crc; has_image = true; uploading = false;
            front_ui_token = upload_ui_token;
            front_voice = upload_voice;
            image_version++;
            touch_image_committed(image_version);
            expires_us = esp_timer_get_time() + (int64_t)duration->valueint * 1000000;
            idle_started_us = touch_status().idle ? esp_timer_get_time() : 0;
            xSemaphoreGive(image_lock);
            xTaskNotifyGive(render_task);
        }
    } else error = "unknown_operation";
    reply(id, !error, error);
    cJSON_Delete(response_extra); response_extra = NULL;
    cJSON_Delete(root);
}

void app_main(void) {
    usb_serial_jtag_driver_config_t config = {.tx_buffer_size = 4096, .rx_buffer_size = 8192};
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&config));
    boot_error = display_init();
    // Touch and speaker share the board bus; neither gates display/USB readiness.
    i2c_master_bus_handle_t bus = NULL;
    const i2c_master_bus_config_t bus_config = {
        .i2c_port = I2C_NUM_0, .sda_io_num = 15, .scl_io_num = 14,
        .clk_source = I2C_CLK_SRC_DEFAULT, .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    if (i2c_new_master_bus(&bus_config, &bus) != ESP_OK) bus = NULL;
    touch_start(bus);
    audio_start(bus);
    ready = boot_error == ESP_OK;
    if (ready && xTaskCreate(render, "display", 8192, NULL, 4, &render_task) != pdPASS) {
        ready = false; boot_error = ESP_ERR_NO_MEM;
    }
    static char line[LINE_BYTES];
    size_t used = 0; bool overflow = false;
    reply(0, true, NULL);
    while (true) {
        uint8_t bytes[256];
        int count = usb_serial_jtag_read_bytes(bytes, sizeof(bytes), pdMS_TO_TICKS(250));
        for (int i = 0; i < count; i++) {
            if (bytes[i] == '\n') {
                line[used] = 0;
                if (overflow) reply(0, false, "line_too_long"); else if (used) command(line);
                used = 0; overflow = false;
            } else if (!overflow && bytes[i] != '\r') {
                if (used + 1 < sizeof(line)) line[used++] = bytes[i]; else overflow = true;
            }
        }
    }
}
