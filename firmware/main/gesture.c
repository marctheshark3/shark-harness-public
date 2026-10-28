#include "gesture.h"
#include <stdlib.h>

void gesture_cancel(gesture_filter_t *f) { f->active = false; f->blocked = true; }
void gesture_interrupt(gesture_filter_t *f) { if (f->active) gesture_cancel(f); }

bool gesture_control(uint16_t x, uint16_t y) {
    return ((x <= 96 || x >= 370) && y >= 130 && y <= 315)
        || (x >= 46 && x <= 420 && y >= 327 && y <= 452)
        || (x >= 90 && x <= 376 && y >= 45 && y <= 126);
}

int gesture_update(gesture_filter_t *f, uint64_t now, unsigned points,
                   uint16_t x, uint16_t y, uint32_t frame) {
    if (!points) {
        int result = GESTURE_NONE;
        uint64_t duration = now - f->started_ms;
        int dx = (int)f->last_x - f->x, dy = (int)f->last_y - f->y;
        if (f->active && !f->blocked && frame && frame == f->frame && duration >= 20) {
            if (!f->moved && duration <= (gesture_control(f->x, f->y) ? 2000U : 700U)) result = GESTURE_TAP;
            else if (duration <= 1200 && abs(dx) >= 64 && abs(dx) > 2 * abs(dy))
                result = dx < 0 ? GESTURE_LEFT : GESTURE_RIGHT;
        }
        f->active = f->blocked = f->moved = false;
        return result;
    }
    if (points != 1 || x >= 466 || y >= 466 || !frame) { gesture_cancel(f); return GESTURE_NONE; }
    if (f->blocked) return GESTURE_NONE;
    if (!f->active) {
        f->active = true; f->started_ms = now; f->x = x; f->y = y; f->frame = frame;
    }
    if (frame != f->frame) { gesture_cancel(f); return GESTURE_NONE; }
    f->last_x = x; f->last_y = y;
    int dx = (int)x - f->x, dy = (int)y - f->y;
    if (dx * dx + dy * dy > 32 * 32) f->moved = true;
    return GESTURE_NONE;
}
