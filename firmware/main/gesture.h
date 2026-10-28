#pragma once
#include <stdbool.h>
#include <stdint.h>

enum { GESTURE_NONE, GESTURE_TAP, GESTURE_LEFT, GESTURE_RIGHT };
typedef struct {
    bool active, blocked, moved;
    uint16_t x, y, last_x, last_y;
    uint64_t started_ms;
    uint32_t frame;
} gesture_filter_t;
void gesture_cancel(gesture_filter_t *filter);
void gesture_interrupt(gesture_filter_t *filter);
bool gesture_control(uint16_t x, uint16_t y);
int gesture_update(gesture_filter_t *filter, uint64_t now, unsigned points,
                   uint16_t x, uint16_t y, uint32_t frame);
