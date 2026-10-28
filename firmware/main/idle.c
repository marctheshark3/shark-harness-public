#include "idle.h"

bool idle_pending(const idle_state_t *s) {
    return s->restoring || s->touch_blocked || s->button_blocked;
}

bool idle_controls_ready(const idle_state_t *s) {
    return !s->enabled && !idle_pending(s);
}

bool idle_enter(idle_state_t *s) {
    if (s->enabled) return true;
    if (idle_pending(s) || s->touch_down || s->button_down) return false;
    s->enabled = true;
    return true;
}

void idle_leave(idle_state_t *s) {
    if (s->enabled) {
        s->enabled = false;
        s->restoring = true;
        s->restore_after = s->image_version;
    }
}

void idle_disconnect(idle_state_t *s) {
    // With no host UI, no replacement image is coming. Keep contact latches so
    // the same press cannot turn into a native tap or microphone hold.
    s->enabled = s->restoring = false;
}

void idle_image_committed(idle_state_t *s, uint32_t version) {
    s->image_version = version;
}

void idle_presented(idle_state_t *s, uint32_t version) {
    // A retained pre-idle image gives immediate wake feedback, but controls
    // wait for the host to repaint its current destination after this wake.
    if (!s->enabled && version == s->image_version && version != s->restore_after)
        s->restoring = false;
}

static void wake(idle_state_t *s) {
    idle_leave(s);
    s->wake = s->wake % 1000000U + 1U;
}

bool idle_touch(idle_state_t *s, unsigned points, uint16_t x, uint16_t y) {
    bool consume = !idle_controls_ready(s);
    s->touch_down = points != 0;
    if (!points) {
        s->touch_blocked = false;
        return consume;
    }
    // Invalid/multiple contacts are blocked through a real release. They do
    // not wake the display and cannot become a valid wake by losing a finger.
    bool valid = points == 1 && x < 466 && y < 466;
    if (!valid) s->touch_blocked = true;
    if (consume && valid && s->enabled && !s->touch_blocked) wake(s);
    if (consume) s->touch_blocked = true;
    return consume || !valid;
}

bool idle_button(idle_state_t *s, bool pressed) {
    bool consume = !idle_controls_ready(s);
    s->button_down = pressed;
    if (!pressed) {
        s->button_blocked = false;
        return consume;
    }
    if (consume && s->enabled && !s->button_blocked) wake(s);
    if (consume) s->button_blocked = true;
    return consume;
}
