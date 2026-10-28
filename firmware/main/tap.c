#include "tap.h"

void tap_cancel(tap_filter_t *filter) {
    filter->active = false;
    filter->blocked = true;
}

bool tap_update(tap_filter_t *filter, uint64_t now_ms, unsigned points,
                uint16_t x, uint16_t y) {
    if (!points) {
        uint64_t duration = now_ms - filter->started_ms;
        bool accepted = filter->active && !filter->blocked && duration >= 20 && duration <= 700
            && (!filter->have_tap || now_ms - filter->last_tap_ms >= 180);
        filter->active = false;
        filter->blocked = false;
        if (accepted) { filter->have_tap = true; filter->last_tap_ms = now_ms; }
        return accepted;
    }
    if (points != 1 || x >= 466 || y >= 466) { tap_cancel(filter); return false; }
    if (filter->blocked) return false;
    if (!filter->active) {
        filter->active = true;
        filter->started_ms = now_ms;
        filter->x = x; filter->y = y;
    } else {
        int dx = (int)x - filter->x, dy = (int)y - filter->y;
        if (dx * dx + dy * dy > 32 * 32) tap_cancel(filter);
    }
    return false;
}
