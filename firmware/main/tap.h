#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef struct {
    bool active, blocked, have_tap;
    uint16_t x, y;
    uint64_t started_ms, last_tap_ms;
} tap_filter_t;

// Accept a short, stationary single-finger contact only after its release.
bool tap_update(tap_filter_t *filter, uint64_t now_ms, unsigned points,
                uint16_t x, uint16_t y);
// A lost report is never a synthetic release. Wait for a real release to rearm.
void tap_cancel(tap_filter_t *filter);
