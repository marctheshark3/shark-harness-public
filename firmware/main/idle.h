#pragma once
#include <stdbool.h>
#include <stdint.h>

// No timers, allocation, or platform calls: the host owns the idle timeout.
// Callers serialize this state with the touch/event lock.
typedef struct {
    bool enabled, restoring;
    bool touch_down, button_down, touch_blocked, button_blocked;
    uint32_t wake;
    uint32_t image_version, restore_after;
} idle_state_t;

bool idle_pending(const idle_state_t *state);
bool idle_controls_ready(const idle_state_t *state);
bool idle_enter(idle_state_t *state);
void idle_leave(idle_state_t *state);
void idle_disconnect(idle_state_t *state);
void idle_image_committed(idle_state_t *state, uint32_t version);
void idle_presented(idle_state_t *state, uint32_t version);

// True consumes this report, including the release that rearms future input.
// Missing/failed touch reports must not be passed here as synthetic releases.
bool idle_touch(idle_state_t *state, unsigned points, uint16_t x, uint16_t y);
bool idle_button(idle_state_t *state, bool pressed);
