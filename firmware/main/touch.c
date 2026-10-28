#include "touch.h"
#include "audio.h"
#include <stdatomic.h>
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_check.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_touch_cst92xx.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static esp_lcd_touch_handle_t controller;
static atomic_bool initialized, button_ready;
static atomic_int last_error;
static portMUX_TYPE gesture_lock = portMUX_INITIALIZER_UNLOCKED;
static input_state_t input;

static void publish_gesture(input_event_t event) {
    portENTER_CRITICAL(&gesture_lock);
    input_publish(&input, event);
    portEXIT_CRITICAL(&gesture_lock);
}

void touch_ack(unsigned id) {
    portENTER_CRITICAL(&gesture_lock);
    input_ack(&input, id);
    portEXIT_CRITICAL(&gesture_lock);
}

bool touch_ui_enabled(void) {
    uint32_t now = (uint64_t)esp_timer_get_time() / 1000;
    portENTER_CRITICAL(&gesture_lock);
    bool enabled = input_ui_enabled(&input, now);
    portEXIT_CRITICAL(&gesture_lock);
    return enabled;
}

bool touch_ui_configure(bool enabled) {
    uint32_t now = (uint64_t)esp_timer_get_time() / 1000;
    portENTER_CRITICAL(&gesture_lock);
    bool was_enabled = input_configure(&input, enabled, now);
    portEXIT_CRITICAL(&gesture_lock);
    return was_enabled;
}

void touch_frame(uint32_t token) {
    portENTER_CRITICAL(&gesture_lock);
    input_frame(&input, token);
    portEXIT_CRITICAL(&gesture_lock);
}

void touch_voice_ready(bool ready) {
    portENTER_CRITICAL(&gesture_lock);
    input_voice_ready(&input, ready);
    portEXIT_CRITICAL(&gesture_lock);
}

bool touch_idle_configure(bool enabled) {
    portENTER_CRITICAL(&gesture_lock);
    bool accepted = input_idle(&input, enabled);
    portEXIT_CRITICAL(&gesture_lock);
    return accepted;
}

void touch_image_committed(uint32_t version) {
    portENTER_CRITICAL(&gesture_lock);
    idle_image_committed(&input.idle, version);
    portEXIT_CRITICAL(&gesture_lock);
}

void touch_idle_presented(uint32_t version) {
    portENTER_CRITICAL(&gesture_lock);
    idle_presented(&input.idle, version);
    portEXIT_CRITICAL(&gesture_lock);
}

static void poll_touch(void *unused) {
    (void)unused;
    while (true) {
        uint64_t now_ms = (uint64_t)esp_timer_get_time() / 1000;
        // GPIO0 remains a polled input; ROM boot behavior is unchanged.
        bool button_pressed = button_ready && gpio_get_level(GPIO_NUM_0) == 0;
        portENTER_CRITICAL(&gesture_lock);
        input_event_t event = input_button(&input, now_ms, button_pressed);
        portEXIT_CRITICAL(&gesture_lock);
        if (event.gesture) publish_gesture(event);

        esp_err_t error = esp_lcd_touch_read_data(controller);
        if (error == ESP_OK) {
            uint16_t raw_x[2] = {0}, raw_y[2] = {0}; uint8_t count = 0;
            esp_lcd_touch_get_coordinates(controller, raw_x, raw_y, NULL, &count, 2);
            uint16_t x = 0, y = 0;
            if (count) input_coordinates(raw_x[0], raw_y[0], &x, &y);
            last_error = ESP_OK;
            portENTER_CRITICAL(&gesture_lock);
            event = input_touch(&input, now_ms, count, x, y);
            portEXIT_CRITICAL(&gesture_lock);
            // Audio consumption remains outside the input lock. Publishing
            // rechecks the visible token and idle gate after this operation.
            if (event.gesture == GESTURE_TAP && audio_tap()) event.gesture = INPUT_AUDIO;
            if (event.gesture) publish_gesture(event);
        } else {
            bool transport_error = error != ESP_ERR_INVALID_RESPONSE;
            if (transport_error) last_error = error;
            portENTER_CRITICAL(&gesture_lock);
            input_missing(&input, now_ms, transport_error);
            portEXIT_CRITICAL(&gesture_lock);
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

esp_err_t touch_start(i2c_master_bus_handle_t bus) {
    esp_lcd_panel_io_handle_t io = NULL;
    esp_err_t error = ESP_ERR_INVALID_STATE;
    if (!bus) goto fail;
    esp_lcd_panel_io_i2c_config_t io_config = ESP_LCD_TOUCH_IO_I2C_CST92XX_CONFIG();
    error = esp_lcd_new_panel_io_i2c(bus, &io_config, &io);
    if (error != ESP_OK) goto fail;
    const esp_lcd_touch_config_t config = {
        // Read raw reports; input_coordinates is the single tested board
        // transform shared with the simulator (no second driver mirroring).
        .x_max = 466, .y_max = 466, .rst_gpio_num = 40,
        .int_gpio_num = GPIO_NUM_NC,
        .levels = {.reset = 0, .interrupt = 0},
    };
    error = esp_lcd_touch_new_i2c_cst92xx(io, &config, &controller);
    if (error != ESP_OK) goto fail;
    const gpio_config_t button_config = {
        .pin_bit_mask = 1ULL << GPIO_NUM_0, .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE, .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    button_ready = gpio_config(&button_config) == ESP_OK;
    if (xTaskCreate(poll_touch, "touch", 4096, NULL, 5, NULL) != pdPASS) {
        error = ESP_ERR_NO_MEM;
        esp_lcd_touch_del(controller);
        goto fail;
    }
    initialized = true;
    return ESP_OK;
fail:
    if (io) esp_lcd_panel_io_del(io);
    last_error = error;
    return error;
}

touch_status_t touch_status(void) {
    uint64_t now = (uint64_t)esp_timer_get_time() / 1000;
    portENTER_CRITICAL(&gesture_lock);
    input_tick(&input, now);
    touch_status_t result = {.ready = initialized, .pressed = input.pressed,
        .holdable = input.holdable && idle_controls_ready(&input.idle),
        .button_ready = button_ready, .button_pressed = input.button_pressed,
        .ui = input_ui_enabled(&input, (uint32_t)now), .frame = input.frame,
        .error = last_error, .taps = input.taps, .x = input.x, .y = input.y,
        .contacts = input.contacts, .releases = input.releases,
        .tap_drops = input.tap_drops, .idle = input.idle.enabled,
        .wake = input.idle.wake, .wake_pending = idle_pending(&input.idle),
        .gestures = input.gestures, .gesture = input.gesture,
        .gesture_x = input.gesture_x, .gesture_y = input.gesture_y,
        .gesture_frame = input.gesture_frame, .event_count = input.event_count,
        .dropped = input.dropped};
    for (unsigned i = 0; i < input.event_count; i++) result.events[i] = input.events[i];
    portEXIT_CRITICAL(&gesture_lock);
    return result;
}
