// Half-duplex ES8311 playback and ES7210 microphone capture.
#include "audio.h"
#include "mic_buffer.h"
#include "touch.h"
#include <ctype.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include "driver/gpio.h"
#include "driver/i2s_std.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "esp_rom_crc.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"

#define PA_PIN 46
static SemaphoreHandle_t lock;
static TaskHandle_t player;
static esp_codec_dev_handle_t codec, microphone;
static i2s_chan_handle_t rx;
static uint8_t *recording;
static mic_buffer_t capture_buffer;
static unsigned mic_delivered;
static mic_status_t mic;
static bool mic_stop_requested, mic_discard, mic_held;
static int64_t mic_lease, mic_expiry;
static uint32_t crc32(const uint8_t *data, size_t length);
static void capture(void *unused);
static uint8_t *pcm;
static audio_status_t status = {.volume = 60};
static uint32_t expected_crc;
static int64_t upload_deadline, armed_deadline;

audio_status_t audio_status(void) {
    if (!lock) return status; // Only possible before initialization completes.
    xSemaphoreTake(lock, portMAX_DELAY);
    if (status.uploading && esp_timer_get_time() > upload_deadline) {
        status.uploading = false; status.stopped = true;
    }
    if (status.armed && esp_timer_get_time() > armed_deadline) {
        status.armed = false; status.stopped = true;
    }
    audio_status_t copy = status;
    xSemaphoreGive(lock);
    return copy;
}

void audio_stop(void) {
    if (!lock) return;
    xSemaphoreTake(lock, portMAX_DELAY);
    status.stopped = true; status.uploading = status.armed = false;
    gpio_set_level(PA_PIN, 0);
    xSemaphoreGive(lock);
}

bool audio_tap(void) {
    if (!lock) return false;
    xSemaphoreTake(lock, portMAX_DELAY);
    bool handled = status.armed || status.playing || status.uploading;
    if (status.armed) {
        status.armed = false; status.playing = true; status.stopped = false;
        xTaskNotifyGive(player);
    } else if (handled) {
        status.stopped = true; status.uploading = false;
        gpio_set_level(PA_PIN, 0);
    }
    xSemaphoreGive(lock);
    return handled;
}

static void playback(void *unused) {
    (void)unused;
    // Stereo I2S avoids mono slot ordering differences; the wire clip is mono.
    int16_t stereo[1024];
    while (true) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        esp_codec_dev_sample_info_t format = {
            .sample_rate = AUDIO_RATE, .channel = 2, .bits_per_sample = 16,
        };
        int error = esp_codec_dev_open(codec, &format);
        unsigned volume = audio_status().volume;
        if (!error) error = esp_codec_dev_set_out_vol(codec, volume);
        xSemaphoreTake(lock, portMAX_DELAY);
        if (!error && !status.stopped) gpio_set_level(PA_PIN, volume != 0);
        unsigned total = status.bytes;
        xSemaphoreGive(lock);
        unsigned offset = 0;
        while (!error && offset < total) {
            audio_status_t current = audio_status();
            if (current.stopped) break;
            if (current.volume != volume) {
                volume = current.volume;
                error = esp_codec_dev_set_out_vol(codec, volume);
                xSemaphoreTake(lock, portMAX_DELAY);
                gpio_set_level(PA_PIN, !error && !status.stopped && volume != 0);
                xSemaphoreGive(lock);
                if (error) break;
            }
            unsigned count = total - offset;
            if (count > 1024) count = 1024;
            for (unsigned i = 0; i < count / 2; i++) {
                int16_t sample = (uint16_t)pcm[offset + i * 2] | ((uint16_t)pcm[offset + i * 2 + 1] << 8);
                stereo[i * 2] = stereo[i * 2 + 1] = sample;
            }
            error = esp_codec_dev_write(codec, stereo, count * 2);
            if (!error) offset += count;
            xSemaphoreTake(lock, portMAX_DELAY); status.played = offset; xSemaphoreGive(lock);
        }
        // Push silence beyond the four 256-frame DMA buffers, so the final
        // syllable drains and old samples cannot recur on the next open.
        memset(stereo, 0, sizeof(stereo));
        for (unsigned i = 0; !error && i < 4 && !audio_status().stopped; i++)
            error = esp_codec_dev_write(codec, stereo, sizeof(stereo));
        gpio_set_level(PA_PIN, 0);
        int close_error = esp_codec_dev_close(codec);
        xSemaphoreTake(lock, portMAX_DELAY);
        status.error = error ? error : close_error;
        status.playing = false;
        xSemaphoreGive(lock);
    }
}

static int initialize(i2c_master_bus_handle_t bus) {
    gpio_config_t pa = {.pin_bit_mask = 1ULL << PA_PIN, .mode = GPIO_MODE_OUTPUT};
    int error = gpio_config(&pa);
    if (error) return error;
    gpio_set_level(PA_PIN, 0);
    if (!bus) return ESP_ERR_INVALID_STATE;
    error = i2c_master_probe(bus, 0x18, 100);
    if (error) return error;
    pcm = heap_caps_malloc(AUDIO_MAX_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!pcm) return ESP_ERR_NO_MEM;
    i2s_chan_handle_t tx = NULL;
    i2s_chan_config_t channels = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    channels.dma_desc_num = 4; channels.dma_frame_num = 256; channels.auto_clear = true;
    error = i2s_new_channel(&channels, &tx, &rx);
    if (error) return error;
    const i2s_std_config_t config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(AUDIO_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        // Board schematic: GPIO8 -> ES8311 DSDIN. GPIO10 is ES7210 capture.
        .gpio_cfg = {.mclk = 16, .bclk = 9, .ws = 45, .dout = 8, .din = 10},
    };
    error = i2s_channel_init_std_mode(tx, &config);
    if (error) return error;
    error = i2s_channel_init_std_mode(rx, &config);
    if (error) return error;
    audio_codec_i2s_cfg_t data_config = {.tx_handle = tx, .rx_handle = rx};
    const audio_codec_data_if_t *data = audio_codec_new_i2s_data(&data_config);
    audio_codec_i2c_cfg_t control_config = {.addr = ES8311_CODEC_DEFAULT_ADDR, .bus_handle = bus};
    const audio_codec_ctrl_if_t *control = audio_codec_new_i2c_ctrl(&control_config);
    const audio_codec_gpio_if_t *gpio = audio_codec_new_gpio();
    if (!data || !control || !gpio) return ESP_ERR_NO_MEM;
    es8311_codec_cfg_t codec_config = {
        .ctrl_if = control, .gpio_if = gpio, .codec_mode = ESP_CODEC_DEV_WORK_MODE_DAC,
        .pa_pin = -1, .use_mclk = true, // We keep PA off until verified playback.
    };
    const audio_codec_if_t *chip = es8311_codec_new(&codec_config);
    if (!chip) return ESP_FAIL;
    esp_codec_dev_cfg_t device_config = {.dev_type = ESP_CODEC_DEV_TYPE_OUT, .codec_if = chip, .data_if = data};
    codec = esp_codec_dev_new(&device_config);
    if (!codec) return ESP_ERR_NO_MEM;
    if (xTaskCreate(playback, "speaker", 6144, NULL, 6, &player) != pdPASS) return ESP_ERR_NO_MEM;
    // Failure to initialize the ADC must not disable the working speaker.
    mic.error = i2c_master_probe(bus, 0x40, 100);
    if (!mic.error) {
        recording = heap_caps_calloc(1, MIC_MAX_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        audio_codec_i2c_cfg_t input_control_config = {.addr = ES7210_CODEC_DEFAULT_ADDR, .bus_handle = bus};
        const audio_codec_ctrl_if_t *input_control = audio_codec_new_i2c_ctrl(&input_control_config);
        es7210_codec_cfg_t input_config = {
            .ctrl_if = input_control, .mic_selected = ES7210_SEL_MIC1 | ES7210_SEL_MIC2,
        };
        const audio_codec_if_t *input_chip = input_control ? es7210_codec_new(&input_config) : NULL;
        esp_codec_dev_cfg_t input_device_config = {.dev_type = ESP_CODEC_DEV_TYPE_IN,
            .codec_if = input_chip, .data_if = data};
        microphone = input_chip ? esp_codec_dev_new(&input_device_config) : NULL;
        mic.error = recording && microphone ? ESP_OK : ESP_ERR_NO_MEM;
        if (!mic.error && xTaskCreate(capture, "microphone", 6144, NULL, 6, NULL) != pdPASS)
            mic.error = ESP_ERR_NO_MEM;
        mic.ready = mic.error == ESP_OK;
    }
    return ESP_OK;
}

void audio_start(i2c_master_bus_handle_t bus) {
    lock = xSemaphoreCreateMutex();
    if (!lock) { status.error = ESP_ERR_NO_MEM; return; }
    status.error = initialize(bus);
    status.ready = status.error == ESP_OK;
}

static bool integer(cJSON *value, int low, int high) {
    return cJSON_IsNumber(value) && value->valuedouble >= low && value->valuedouble <= high
        && value->valuedouble == value->valueint;
}

static uint32_t crc32(const uint8_t *data, size_t length) {
    return esp_rom_crc32_le(0, data, length);
}

const char *audio_command(const char *op, cJSON *root) {
    if (!strcmp(op, "audio.stop")) { audio_stop(); return NULL; }
    if (!audio_status().ready) return "audio_unavailable";
    const char *error = NULL;
    xSemaphoreTake(lock, portMAX_DELAY);
    if (!strcmp(op, "audio.volume")) {
        cJSON *volume = cJSON_GetObjectItemCaseSensitive(root, "volume");
        if (!integer(volume, 0, 80)) error = "invalid_volume";
        else {
            status.volume = volume->valueint;
            if (!status.volume) gpio_set_level(PA_PIN, 0);
        }
    } else if (!strcmp(op, "audio.begin")) {
        cJSON *bytes = cJSON_GetObjectItemCaseSensitive(root, "bytes");
        cJSON *rate = cJSON_GetObjectItemCaseSensitive(root, "sampleRate");
        cJSON *crc = cJSON_GetObjectItemCaseSensitive(root, "crc32");
        bool hex = cJSON_IsString(crc) && strlen(crc->valuestring) == 8;
        for (unsigned i = 0; hex && i < 8; i++) hex = isxdigit((unsigned char)crc->valuestring[i]);
        if (status.playing || mic.recording) error = "audio_busy";
        else if (!integer(bytes, 2, AUDIO_MAX_BYTES) || bytes->valueint % 2
                 || !integer(rate, AUDIO_RATE, AUDIO_RATE) || !hex) error = "invalid_audio";
        else {
            status.bytes = bytes->valueint; status.offset = status.played = 0;
            status.uploading = true; status.armed = false; status.stopped = false; status.error = 0;
            expected_crc = strtoul(crc->valuestring, NULL, 16);
            upload_deadline = esp_timer_get_time() + 30000000;
        }
    } else if (!strcmp(op, "audio.chunk")) {
        cJSON *offset = cJSON_GetObjectItemCaseSensitive(root, "offset");
        cJSON *data = cJSON_GetObjectItemCaseSensitive(root, "data");
        uint8_t decoded[2048]; size_t count = 0;
        if (!status.uploading || !integer(offset, 0, AUDIO_MAX_BYTES) || (unsigned)offset->valueint != status.offset)
            error = "invalid_audio_offset";
        else if (!cJSON_IsString(data) || mbedtls_base64_decode(decoded, sizeof(decoded), &count,
                    (const unsigned char *)data->valuestring, strlen(data->valuestring)) != 0
                    || count == 0 || status.offset + count > status.bytes) error = "invalid_audio_chunk";
        else { memcpy(pcm + status.offset, decoded, count); status.offset += count; }
    } else if (!strcmp(op, "audio.end")) {
        cJSON *on_tap = cJSON_GetObjectItemCaseSensitive(root, "onTap");
        if (on_tap && !cJSON_IsBool(on_tap)) error = "invalid_audio_trigger";
        else if (!status.uploading || status.offset != status.bytes) error = "incomplete_audio";
        else if (crc32(pcm, status.bytes) != expected_crc) { status.uploading = false; error = "audio_crc_mismatch"; }
        else {
            status.crc = expected_crc; status.uploading = false;
            status.armed = cJSON_IsTrue(on_tap); status.playing = !status.armed;
            if (status.armed) armed_deadline = esp_timer_get_time() + 3600000000LL;
            else xTaskNotifyGive(player);
        }
    } else error = "unknown_operation";
    xSemaphoreGive(lock);
    return error;
}

mic_status_t mic_status(void) {
    if (!lock) return mic;
    xSemaphoreTake(lock, portMAX_DELAY);
    mic_status_t copy = mic;
    xSemaphoreGive(lock);
    return copy;
}

// Caller holds lock. A capture owns the I2S bus until the ADC has closed.
static const char *mic_begin(bool held) {
    if (!mic.ready || !mic.enabled) return "microphone_disabled";
    touch_status_t touch = touch_status();
    if (touch.idle || touch.wake_pending) return "ui_idle_busy";
    // Recheck after acquiring the audio lock: an older touch-task snapshot can
    // cross a release/idle transition while waiting for playback cleanup.
    if (held && !touch.holdable) return "microphone_disabled";
    if (mic.recording || mic.available || status.playing || status.uploading || status.armed) return "audio_busy";
    mic_buffer_reset(&capture_buffer, recording, MIC_MAX_BYTES);
    mic_delivered = 0;
    mic.id = mic.id % 1000000 + 1;
    mic.bytes = mic.level = mic.crc = 0; mic.error = 0;
    mic.available = false; mic.recording = true;
    mic_stop_requested = mic_discard = false; mic_held = held;
    return NULL;
}

static void capture(void *unused) {
    (void)unused;
    int16_t stereo[512];
    uint8_t mono[512];
    int64_t pressed_at = 0;
    bool used_hold = false;
    while (true) {
        int64_t now = esp_timer_get_time();
        touch_status_t touch = touch_status();
        xSemaphoreTake(lock, portMAX_DELAY);
        if (now > mic_lease) {
            mic.enabled = false;
            if (mic.recording) { mic_stop_requested = true; mic_discard = true; }
        }
        if (mic.available && now > mic_expiry) {
            mic_buffer_reset(&capture_buffer, recording, MIC_MAX_BYTES);
            mic.available = false; mic.bytes = 0;
        }
        if (!touch.holdable || touch.error) { pressed_at = 0; used_hold = false; }
        else if (!pressed_at) pressed_at = now;
        if (pressed_at && !used_hold && now - pressed_at >= 800000 && mic.enabled && !mic.recording) {
            // Holding to talk interrupts speaker playback, then waits for its
            // codec to close before changing the shared I2S clock.
            status.stopped = true; status.uploading = status.armed = false;
            gpio_set_level(PA_PIN, 0);
            if (!status.playing) { used_hold = true; (void)mic_begin(true); }
        }
        bool start = mic.recording;
        xSemaphoreGive(lock);
        if (!start) { vTaskDelay(pdMS_TO_TICKS(20)); continue; }

        gpio_set_level(PA_PIN, 0);
        esp_codec_dev_sample_info_t format = {.sample_rate = MIC_RATE, .channel = 2, .bits_per_sample = 16};
        int error = esp_codec_dev_open(microphone, &format);
        if (!error) error = esp_codec_dev_set_in_gain(microphone, 30.0f);
        unsigned warmup = 3; // Discard 48 ms of ADC settling.
        while (!error) {
            touch = touch_status();
            xSemaphoreTake(lock, portMAX_DELAY);
            if (esp_timer_get_time() > mic_lease || touch.error) mic_discard = true;
            // Movement prevents a swipe from starting capture, but once a hold
            // is recording, normal finger drift must not submit speech early.
            bool stop = mic_stop_requested || mic_discard || (mic_held && !touch.pressed);
            xSemaphoreGive(lock);
            if (stop) break;
            size_t received = 0;
            error = i2s_channel_read(rx, stereo, sizeof(stereo), &received, 100);
            if (error) break;
            if (warmup) { warmup--; continue; }
            unsigned peak = 0, count = 0;
            for (unsigned i = 0; i + 1 < received / sizeof(int16_t); i += 2) {
                // MIC1 is the first standard-I2S slot; no speaker reference mix.
                int sample = stereo[i];
                unsigned magnitude = abs(sample);
                if (magnitude > peak) peak = magnitude;
                mono[count++] = sample & 255;
                mono[count++] = ((uint16_t)sample) >> 8;
            }
            xSemaphoreTake(lock, portMAX_DELAY);
            if (!mic_buffer_write(&capture_buffer, mono, count)) {
                // Never submit a truncated prompt when the host cannot keep up.
                mic_discard = true; error = ESP_ERR_NO_MEM;
            } else {
                mic.bytes = capture_buffer.end; mic.level = peak;
                mic.crc = esp_rom_crc32_le(mic.crc, mono, count);
            }
            xSemaphoreGive(lock);
        }
        int close_error = esp_codec_dev_close(microphone);
        xSemaphoreTake(lock, portMAX_DELAY);
        mic.error = error ? error : close_error;
        mic.recording = false; mic.level = 0;
        mic.available = !mic_discard && !mic.error && mic.bytes >= MIC_RATE / 2;
        if (mic.available) {
            mic_expiry = esp_timer_get_time() + 120000000;
        } else { mic_buffer_reset(&capture_buffer, recording, MIC_MAX_BYTES); mic.bytes = 0; }
        xSemaphoreGive(lock);
    }
}

void mic_json(cJSON *root) {
    mic_status_t s = mic_status();
    cJSON *value = cJSON_AddObjectToObject(root, "mic");
    cJSON_AddBoolToObject(value, "ready", s.ready);
    cJSON_AddBoolToObject(value, "enabled", s.enabled);
    cJSON_AddBoolToObject(value, "recording", s.recording);
    cJSON_AddBoolToObject(value, "available", s.available);
    cJSON_AddBoolToObject(value, "streaming", true);
    cJSON_AddNumberToObject(value, "id", s.id);
    cJSON_AddNumberToObject(value, "bytes", s.bytes);
    cJSON_AddNumberToObject(value, "sampleRate", MIC_RATE);
    cJSON_AddNumberToObject(value, "level", s.level);
    cJSON_AddNumberToObject(value, "error", s.error);
    char checksum[9]; snprintf(checksum, sizeof(checksum), "%08lx", (unsigned long)s.crc);
    cJSON_AddStringToObject(value, "crc32", checksum);
}

const char *mic_command(const char *op, cJSON *root, cJSON *extra) {
    if (!lock || !mic_status().ready) return "microphone_unavailable";
    const char *error = NULL;
    xSemaphoreTake(lock, portMAX_DELAY);
    if (!strcmp(op, "mic.configure")) {
        cJSON *enabled = cJSON_GetObjectItemCaseSensitive(root, "enabled");
        if (!cJSON_IsBool(enabled)) error = "invalid_microphone_config";
        else {
            mic.enabled = cJSON_IsTrue(enabled); mic_lease = esp_timer_get_time() + 5000000;
            if (!mic.enabled && mic.recording) { mic_stop_requested = true; mic_discard = true; }
        }
    } else if (!strcmp(op, "mic.start")) error = mic_begin(false);
    else if (!strcmp(op, "mic.stop")) mic_stop_requested = true;
    else if (!strcmp(op, "mic.clear")) {
        mic_stop_requested = mic_discard = true;
        mic.available = false;
        if (!mic.recording) { mic_buffer_reset(&capture_buffer, recording, MIC_MAX_BYTES); mic.bytes = 0; }
    } else if (!strcmp(op, "mic.read")) {
        cJSON *id = cJSON_GetObjectItemCaseSensitive(root, "captureId");
        cJSON *offset = cJSON_GetObjectItemCaseSensitive(root, "offset");
        cJSON *requested = cJSON_GetObjectItemCaseSensitive(root, "count");
        unsigned position = 0;
        bool valid_offset = cJSON_IsNumber(offset) && offset->valuedouble >= 0
            && offset->valuedouble < mic.bytes;
        if (valid_offset) {
            position = (unsigned)offset->valuedouble;
            valid_offset = offset->valuedouble == position && position % 2 == 0;
        }
        if ((!mic.available && !mic.recording) || !integer(id, mic.id, mic.id)
            || !valid_offset || position < capture_buffer.start || position > mic_delivered
            || (requested && (!integer(requested, 2, 2048) || requested->valueint % 2))) error = "invalid_capture";
        else {
            unsigned count = requested ? (unsigned)requested->valueint : mic.bytes - position;
            if (count > 2048) count = 2048;
            uint8_t chunk[2048];
            unsigned char encoded[2733]; size_t length = 0;
            if (!mic_buffer_read(&capture_buffer, position, chunk, count)) error = "invalid_capture";
            else if (mbedtls_base64_encode(encoded, sizeof(encoded), &length,
                chunk, count)) error = "capture_encode_failed";
            else {
                // The next read acknowledges the preceding bytes. A lost reply
                // can be read again at the same offset without losing samples.
                mic_buffer_ack(&capture_buffer, position);
                if (position + count > mic_delivered) mic_delivered = position + count;
                encoded[length] = 0;
                cJSON_AddNumberToObject(extra, "captureId", mic.id);
                cJSON_AddNumberToObject(extra, "captureOffset", position);
                cJSON_AddStringToObject(extra, "captureData", (const char *)encoded);
                char checksum[9]; snprintf(checksum, sizeof(checksum), "%08lx", (unsigned long)crc32(chunk, count));
                cJSON_AddStringToObject(extra, "captureCrc32", checksum);
            }
        }
    } else error = "unknown_operation";
    xSemaphoreGive(lock);
    return error;
}
