#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "gesture.h"
#include "idle.h"
#include "tap.h"

#define INPUT_EVENTS 16
enum { INPUT_AUDIO = 4, INPUT_BUTTON = 5 };

typedef struct {
    unsigned id, gesture, x, y;
    uint32_t frame, started_ms, released_ms;
} input_event_t;

// Platform-independent input path shared by the device and desktop simulator.
// The caller serializes all access. There are no allocations or platform calls.
typedef struct {
    tap_filter_t tap;
    gesture_filter_t swipe, button;
    idle_state_t idle;
    bool pressed, holdable, button_pressed, voice_ready, ui_was_active;
    uint16_t x, y;
    uint32_t frame, ui_deadline;
    uint64_t last_report_ms;
    unsigned taps, contacts, releases, tap_drops;
    unsigned gestures, gesture, gesture_x, gesture_y;
    uint32_t gesture_frame;
    input_event_t events[INPUT_EVENTS];
    unsigned event_count, dropped;
} input_state_t;

// CST9217 raw coordinates to the CO5300's displayed pixel coordinates. Bounds
// are checked before subtraction. This board inverts Y, while X is unchanged.
bool input_coordinates(uint16_t raw_x, uint16_t raw_y, uint16_t *x, uint16_t *y);
bool input_ui_enabled(const input_state_t *state, uint32_t now_ms);
bool input_configure(input_state_t *state, bool enabled, uint32_t now_ms);
void input_tick(input_state_t *state, uint64_t now_ms);
void input_frame(input_state_t *state, uint32_t frame);
void input_voice_ready(input_state_t *state, bool ready);
bool input_idle(input_state_t *state, bool enabled);

// Reports return a candidate event. Firmware can let speaker playback consume
// a tap before publishing. Publishing rechecks idle/frame state after that gap.
input_event_t input_touch(input_state_t *state, uint64_t now_ms,
                          unsigned points, uint16_t x, uint16_t y);
input_event_t input_button(input_state_t *state, uint64_t now_ms, bool pressed);
void input_missing(input_state_t *state, uint64_t now_ms, bool transport_error);
void input_publish(input_state_t *state, input_event_t event);
void input_ack(input_state_t *state, unsigned id);
