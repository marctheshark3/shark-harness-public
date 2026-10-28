#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "driver/i2c_master.h"
#include "input.h"

#define TOUCH_EVENTS INPUT_EVENTS
typedef input_event_t touch_event_t;

typedef struct {
    bool ready, pressed, holdable, ui, button_ready, button_pressed;
    bool idle, wake_pending;
    uint32_t wake;
    esp_err_t error;
    unsigned taps;
    uint16_t x, y;
    unsigned gestures, gesture, gesture_x, gesture_y;
    uint32_t gesture_frame, frame;
    unsigned event_count, dropped;
    unsigned contacts, releases, tap_drops;
    touch_event_t events[TOUCH_EVENTS];
} touch_status_t;

// Touch failure is independent of display/USB readiness.
esp_err_t touch_start(i2c_master_bus_handle_t bus);
touch_status_t touch_status(void);
// Return whether the previous lease was still active at renewal.
bool touch_ui_configure(bool enabled);
bool touch_ui_enabled(void);
void touch_frame(uint32_t crc);
void touch_voice_ready(bool ready);
// Idle entry rejects held/uncertain inputs. A wake consumes the whole gesture.
bool touch_idle_configure(bool enabled);
void touch_image_committed(uint32_t version);
void touch_idle_presented(uint32_t version);
void touch_ack(unsigned id);
