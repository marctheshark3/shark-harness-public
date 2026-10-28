#include "input.h"

bool input_coordinates(uint16_t raw_x, uint16_t raw_y, uint16_t *x, uint16_t *y) {
    if (raw_x >= 466 || raw_y >= 466) {
        *x = *y = 466;
        return false;
    }
    *x = raw_x;
    *y = 465 - raw_y;
    return true;
}

bool input_ui_enabled(const input_state_t *s, uint32_t now) {
    return s->ui_was_active && (int32_t)(s->ui_deadline - now) > 0;
}

static void disconnect(input_state_t *s) {
    s->frame = 0; s->voice_ready = false; s->holdable = false;
    // Reusing a token after reconnect cannot make an older held contact belong
    // to the restored UI. Only active filters need interruption; idle report
    // gaps must leave the next fresh press usable.
    if (s->tap.active) tap_cancel(&s->tap);
    gesture_interrupt(&s->swipe);
    gesture_interrupt(&s->button);
    idle_disconnect(&s->idle);
    s->event_count = 0;
}

bool input_configure(input_state_t *s, bool enabled, uint32_t now) {
    bool was_enabled = input_ui_enabled(s, now);
    s->ui_deadline = now + (enabled ? 5000U : 0U);
    if (!enabled || !was_enabled) disconnect(s);
    s->ui_was_active = enabled;
    return was_enabled;
}

void input_tick(input_state_t *s, uint64_t now) {
    bool ui = input_ui_enabled(s, (uint32_t)now);
    if (s->ui_was_active && !ui) disconnect(s);
    s->ui_was_active = ui;
}

void input_frame(input_state_t *s, uint32_t frame) {
    s->frame = idle_controls_ready(&s->idle) ? frame : 0;
}

void input_voice_ready(input_state_t *s, bool ready) {
    s->voice_ready = ready && idle_controls_ready(&s->idle);
    if (!s->voice_ready) s->holdable = false;
}

bool input_idle(input_state_t *s, bool enabled) {
    if (enabled && !idle_enter(&s->idle)) return false;
    if (!enabled) idle_leave(&s->idle);
    // Repeated wake commands never release a held finger or key.
    if (enabled || idle_pending(&s->idle)) {
        s->frame = 0; s->voice_ready = false; s->holdable = false;
        s->event_count = 0;
    }
    return true;
}

input_event_t input_touch(input_state_t *s, uint64_t now, unsigned points,
                          uint16_t x, uint16_t y) {
    input_tick(s, now);
    s->last_report_ms = now;
    bool was_pressed = s->pressed;
    s->pressed = points == 1 && x < 466 && y < 466;
    if (s->pressed && !was_pressed) s->contacts++;
    if (!s->pressed && was_pressed) s->releases++;
    if (s->pressed) { s->x = x; s->y = y; }
    bool consumed = idle_touch(&s->idle, points, x, y);
    if (consumed) { tap_cancel(&s->tap); gesture_cancel(&s->swipe); }
    bool tapped = tap_update(&s->tap, now, points, x, y);
    if (tapped) s->taps++;
    bool ui = input_ui_enabled(s, (uint32_t)now);
    // A press that starts on a control remains that control, never a voice hold.
    s->holdable = !consumed && idle_controls_ready(&s->idle)
        && s->pressed && s->tap.active && !s->tap.blocked
        && (!ui || (s->voice_ready && s->frame
            && !gesture_control(s->tap.x, s->tap.y)));
    int event = gesture_update(&s->swipe, now, points, x, y,
                               ui && !consumed ? s->frame : 0);
    if (tapped && ui && !event) s->tap_drops++;
    return (input_event_t){.gesture = event, .x = s->swipe.last_x,
        .y = s->swipe.last_y, .frame = s->swipe.frame,
        .started_ms = s->swipe.started_ms, .released_ms = now};
}

input_event_t input_button(input_state_t *s, uint64_t now, bool pressed) {
    input_tick(s, now);
    s->button_pressed = pressed;
    bool consumed = idle_button(&s->idle, pressed);
    if (consumed) gesture_cancel(&s->button);
    int event = gesture_update(&s->button, now, pressed ? 1 : 0, 0, 0,
        input_ui_enabled(s, (uint32_t)now) && !consumed ? s->frame : 0);
    return (input_event_t){.gesture = event == GESTURE_TAP ? INPUT_BUTTON : 0,
        .frame = s->button.frame, .started_ms = s->button.started_ms,
        .released_ms = now};
}

void input_missing(input_state_t *s, uint64_t now, bool transport_error) {
    input_tick(s, now);
    // ACK/no-report markers while no contact is active must not poison the
    // next press. An interrupted contact must wait for a real release.
    if (transport_error || now - s->last_report_ms > 200) {
        if (s->tap.active) tap_cancel(&s->tap);
        gesture_interrupt(&s->swipe);
        s->holdable = false;
    }
    if (now - s->last_report_ms > 200) s->pressed = false;
    // Missing reports never release the independent idle wake latch.
}

void input_publish(input_state_t *s, input_event_t event) {
    if (!event.gesture || !idle_controls_ready(&s->idle) || event.frame != s->frame) return;
    s->gestures = s->gestures % 1000000U + 1U;
    s->gesture = event.gesture; s->gesture_x = event.x;
    s->gesture_y = event.y; s->gesture_frame = event.frame;
    event.id = s->gestures;
    if (s->event_count < INPUT_EVENTS) s->events[s->event_count++] = event;
    else s->dropped++;
}

void input_ack(input_state_t *s, unsigned id) {
    for (unsigned i = 0; i < s->event_count; i++) if (s->events[i].id == id) {
        unsigned consumed = i + 1;
        for (unsigned j = consumed; j < s->event_count; j++) s->events[j - consumed] = s->events[j];
        s->event_count -= consumed;
        break;
    }
}
