// Desktop adapter only. All input decisions run in the production input.c.
#include "input.h"
#include "voice_ring.h"
#include <stdlib.h>

typedef struct {
    input_state_t input;
    uint64_t now;
    uint32_t token, image_version, presented_version, presented_ms, error;
    bool has_image, front_voice, uploading;
} simulator_t;

typedef struct {
    uint32_t active, pressed, holdable, taps, x, y, button_pressed;
    uint32_t event, gesture, gesture_x, gesture_y, event_frame, frame;
    uint32_t idle, wake, wake_pending, voice_ready, front_voice, contacts, releases;
    uint32_t tap_drops, dropped, event_count;
    uint32_t image_version, presented_version, presented_ms, now_ms, image_active, error;
    input_event_t events[INPUT_EVENTS];
} simulator_status_t;

void *shark_sim_create(void) { return calloc(1, sizeof(simulator_t)); }
void shark_sim_destroy(void *handle) { free(handle); }

static void clear(simulator_t *s) {
    s->has_image = false;
    s->uploading = false;
    input_frame(&s->input, 0);
    input_voice_ready(&s->input, false);
}

static void advance(simulator_t *s, uint64_t now) {
    bool had_lease = s->input.ui_was_active;
    s->now = now;
    input_tick(&s->input, now);
    if (had_lease && !s->input.ui_was_active) clear(s);
}

// The desktop has no panel DMA; present() is its explicit completed draw.
// Release after a completed fresh draw still rearms through the same idle gate.
static void controls(simulator_t *s) {
    if (s->has_image && !s->uploading && input_ui_enabled(&s->input, (uint32_t)s->now)
            && s->presented_version == s->image_version) {
        idle_presented(&s->input.idle, s->presented_version);
        input_frame(&s->input, s->token);
        input_voice_ready(&s->input, s->front_voice);
    }
}

void shark_sim_configure(void *handle, bool enabled, uint64_t now) {
    simulator_t *s = handle;
    advance(s, now);
    bool previous = input_configure(&s->input, enabled, (uint32_t)now);
    if (!enabled || !previous) clear(s);
}

void shark_sim_clear(void *handle, uint64_t now) {
    simulator_t *s = handle;
    advance(s, now);
    clear(s);
}

void shark_sim_begin_frame(void *handle, uint32_t token, uint64_t now) {
    simulator_t *s = handle;
    advance(s, now);
    s->uploading = true;
    input_voice_ready(&s->input, false);
    if (!token || token != s->token) input_frame(&s->input, 0);
}

void shark_sim_frame(void *handle, uint32_t token, bool voice, uint32_t version, uint64_t now) {
    simulator_t *s = handle;
    shark_sim_begin_frame(handle, token, now);
    s->token = token; s->front_voice = voice; s->has_image = true;
    s->image_version = version;
    s->uploading = false;
    idle_image_committed(&s->input.idle, version);
}

void shark_sim_present(void *handle, uint32_t version, uint64_t now) {
    simulator_t *s = handle;
    advance(s, now);
    s->presented_version = version; s->presented_ms = (uint32_t)now;
    controls(s);
}

bool shark_sim_idle(void *handle, bool enabled) {
    simulator_t *s = handle;
    if (enabled && (!input_ui_enabled(&s->input, (uint32_t)s->now) || s->uploading)) return false;
    bool accepted = input_idle(&s->input, enabled);
    controls(s);
    return accepted;
}

void shark_sim_touch(void *handle, uint64_t now, unsigned points, uint16_t x, uint16_t y, bool raw) {
    simulator_t *s = handle;
    advance(s, now);
    if (raw && points) input_coordinates(x, y, &x, &y);
    s->error = 0;
    input_event_t event = input_touch(&s->input, now, points, x, y);
    input_publish(&s->input, event);
    controls(s);
}

void shark_sim_button(void *handle, uint64_t now, bool pressed) {
    simulator_t *s = handle;
    advance(s, now);
    input_event_t event = input_button(&s->input, now, pressed);
    input_publish(&s->input, event);
    controls(s);
}

void shark_sim_missing(void *handle, uint64_t now, bool error) {
    simulator_t *s = handle;
    advance(s, now);
    if (error) s->error = 1;
    input_missing(&s->input, now, error);
}

void shark_sim_ack(void *handle, unsigned event) {
    simulator_t *s = handle;
    input_ack(&s->input, event);
}

void shark_sim_status(void *handle, uint64_t now, simulator_status_t *result) {
    simulator_t *s = handle;
    advance(s, now);
    controls(s);
    input_state_t *i = &s->input;
    *result = (simulator_status_t){
        .active = input_ui_enabled(i, (uint32_t)now), .pressed = i->pressed,
        .holdable = i->holdable && idle_controls_ready(&i->idle),
        .taps = i->taps, .x = i->x, .y = i->y, .button_pressed = i->button_pressed,
        .event = i->gestures, .gesture = i->gesture, .gesture_x = i->gesture_x,
        .gesture_y = i->gesture_y, .event_frame = i->gesture_frame, .frame = i->frame,
        .idle = i->idle.enabled, .wake = i->idle.wake, .wake_pending = idle_pending(&i->idle),
        .voice_ready = i->voice_ready, .front_voice = s->front_voice,
        .contacts = i->contacts, .releases = i->releases,
        .tap_drops = i->tap_drops, .dropped = i->dropped, .event_count = i->event_count,
        .image_version = s->image_version, .presented_version = s->presented_version,
        .presented_ms = s->presented_ms, .now_ms = (uint32_t)now,
        .image_active = s->has_image, .error = s->error};
    for (unsigned j = 0; j < i->event_count; j++) result->events[j] = i->events[j];
}

bool shark_sim_control(uint16_t x, uint16_t y) {
    return x < 466 && y < 466 && gesture_control(x, y);
}
bool shark_sim_coordinates(uint16_t raw_x, uint16_t raw_y, uint16_t *x, uint16_t *y) {
    return input_coordinates(raw_x, raw_y, x, y);
}

void shark_sim_ring(uint8_t *pixels, float seconds, unsigned level, bool recording) {
    voice_ring_t ring;
    voice_ring_prepare(&ring, seconds, level, recording);
    voice_ring_draw(&ring, pixels, 466, 0, 466);
}
