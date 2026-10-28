#include <assert.h>
#include <stdio.h>
#include "input.h"

static input_state_t awake(uint64_t now) {
    input_state_t s = {0};
    assert(!input_configure(&s, true, (uint32_t)now));
    idle_image_committed(&s.idle, 1);
    idle_presented(&s.idle, 1);
    input_frame(&s, 42);
    input_voice_ready(&s, true);
    return s;
}

static void report(input_state_t *s, uint64_t now, unsigned points, uint16_t x, uint16_t y) {
    input_event_t event = input_touch(s, now, points, x, y);
    input_publish(s, event);
}

static void raw_report(input_state_t *s, uint64_t now, unsigned points, uint16_t raw_x, uint16_t raw_y) {
    uint16_t x = 0, y = 0;
    if (points) input_coordinates(raw_x, raw_y, &x, &y);
    report(s, now, points, x, y);
}

static void fresh_frame(input_state_t *s, uint32_t version) {
    idle_image_committed(&s->idle, version);
    idle_presented(&s->idle, version);
    input_frame(s, 42);
    input_voice_ready(s, true);
}

int main(void) {
    uint16_t x, y;
    assert(input_coordinates(0, 0, &x, &y) && x == 0 && y == 465);
    assert(input_coordinates(465, 0, &x, &y) && x == 465 && y == 465);
    assert(input_coordinates(0, 465, &x, &y) && x == 0 && y == 0);
    assert(input_coordinates(465, 465, &x, &y) && x == 465 && y == 0);
    assert(!input_coordinates(466, 0, &x, &y) && x == 466 && y == 466);
    assert(!input_coordinates(0, 466, &x, &y));
    assert(!input_coordinates(65535, 65535, &x, &y));

    // Physical calibration representatives: the operator tapped LEFT at
    // (35,247) and RIGHT at (433,254). Raw Y is inverted; raw X is not.
    input_state_t s = awake(0);
    raw_report(&s, 100, 1, 35, 465 - 247);
    assert(s.x == 35 && s.y == 247 && !s.holdable);
    raw_report(&s, 180, 0, 0, 0);
    assert(s.event_count == 1 && s.events[0].gesture == GESTURE_TAP);
    assert(s.events[0].x == 35 && s.events[0].y == 247 && s.events[0].frame == 42);
    raw_report(&s, 400, 1, 433, 465 - 254);
    raw_report(&s, 480, 0, 0, 0);
    assert(s.event_count == 2 && s.events[1].x == 433 && s.events[1].y == 254);
    raw_report(&s, 600, 1, 340, 240);
    raw_report(&s, 700, 1, 120, 240);
    raw_report(&s, 800, 0, 0, 0);
    assert(s.events[2].gesture == GESTURE_LEFT);
    raw_report(&s, 900, 1, 120, 240);
    raw_report(&s, 1000, 1, 340, 240);
    raw_report(&s, 1100, 0, 0, 0);
    assert(s.events[3].gesture == GESTURE_RIGHT);

    const unsigned controls[][2] = {{35,247}, {433,254}, {233,45}, {233,126},
        {46,327}, {178,435}, {288,435}, {233,452}, {420,452}};
    for (unsigned i = 0; i < sizeof(controls) / sizeof(controls[0]); i++) {
        s = awake(0);
        report(&s, 20, 1, controls[i][0], controls[i][1]);
        report(&s, 1220, 1, controls[i][0], controls[i][1]);
        assert(!s.holdable);
        report(&s, 1240, 0, 0, 0);
        assert(s.event_count == 1 && s.events[0].gesture == GESTURE_TAP);
    }
    s = awake(0);
    report(&s, 20, 1, 233, 240);
    assert(s.holdable);
    report(&s, 1200, 1, 233, 240);
    assert(s.holdable);
    report(&s, 1220, 0, 0, 0);
    assert(!s.holdable && !s.event_count); // Center hold never becomes a tap.
    input_voice_ready(&s, false);
    report(&s, 1500, 1, 233, 240);
    assert(!s.holdable); // Chooser/review/upload gate also protects the center.
    report(&s, 2500, 0, 0, 0);
    report(&s, 2700, 1, 433, 254);
    report(&s, 2800, 0, 0, 0);
    assert(s.event_count == 1); // Same-context navigation survives that gate.

    s = awake(0);
    report(&s, 20, 1, 433, 254);
    input_frame(&s, 43);
    report(&s, 100, 0, 0, 0);
    assert(!s.event_count); // Control context changed while held.
    report(&s, 200, 1, 433, 254);
    input_event_t candidate = input_touch(&s, 280, 0, 0, 0);
    assert(candidate.gesture == GESTURE_TAP);
    assert(input_idle(&s, true));
    input_publish(&s, candidate);
    assert(!s.event_count); // Idle raced between release and event publication.

    s = awake(0);
    assert(input_idle(&s, true));
    report(&s, 20, 1, 233, 240);
    assert(!s.idle.enabled && s.idle.wake == 1 && idle_pending(&s.idle));
    assert(!s.holdable && !s.event_count && !s.taps && !s.frame);
    input_idle(&s, false);
    fresh_frame(&s, 2);
    assert(idle_pending(&s.idle) && !s.frame);
    input_missing(&s, 1000, false);
    assert(idle_pending(&s.idle)); // Missing report never releases the wake latch.
    report(&s, 1200, 1, 233, 240);
    assert(!s.holdable && !s.event_count && !s.taps);
    input_publish(&s, input_button(&s, 1220, true));
    report(&s, 1240, 0, 0, 0);
    assert(idle_pending(&s.idle)); // Overlapping BOOT is consumed too.
    input_publish(&s, input_button(&s, 1300, false));
    assert(idle_controls_ready(&s.idle) && !s.event_count && !s.taps);
    fresh_frame(&s, 2);
    report(&s, 1500, 1, 35, 247);
    report(&s, 1580, 0, 0, 0);
    assert(s.event_count == 1 && s.events[0].x == 35);

    s = awake(0);
    assert(input_idle(&s, true));
    raw_report(&s, 20, 1, 466, 0);
    raw_report(&s, 40, 1, 233, 233);
    assert(s.idle.enabled && !s.idle.wake);
    raw_report(&s, 60, 0, 0, 0);
    raw_report(&s, 80, 2, 233, 233);
    raw_report(&s, 100, 1, 233, 233);
    assert(s.idle.enabled && !s.idle.wake);
    raw_report(&s, 120, 0, 0, 0);
    input_publish(&s, input_button(&s, 140, true));
    input_publish(&s, input_button(&s, 220, false));
    assert(s.idle.wake == 1 && idle_pending(&s.idle) && !s.event_count);
    idle_presented(&s.idle, 1);
    assert(idle_pending(&s.idle)); // Retained pixels are not a fresh draw.
    fresh_frame(&s, 2);
    assert(idle_controls_ready(&s.idle));
    input_publish(&s, input_button(&s, 400, true));
    input_publish(&s, input_button(&s, 500, false));
    assert(s.event_count == 1 && s.events[0].gesture == INPUT_BUTTON);

    s = awake(0);
    input_missing(&s, 1000, false);
    report(&s, 1500, 1, 35, 247);
    report(&s, 1580, 0, 0, 0);
    assert(s.event_count == 1); // Isolated tap after idle ACK gaps still works.
    report(&s, 1800, 1, 35, 247);
    input_missing(&s, 1850, true);
    report(&s, 1880, 0, 0, 0);
    assert(s.event_count == 1); // Transport interruption cancels an active tap.

    s = awake(0);
    for (unsigned i = 0; i < INPUT_EVENTS + 2; i++) {
        report(&s, i * 200 + 20, 1, 35, 247);
        report(&s, i * 200 + 100, 0, 0, 0);
    }
    assert(s.event_count == INPUT_EVENTS && s.dropped == 2 && s.gestures == 18);
    input_ack(&s, 1000);
    assert(s.event_count == INPUT_EVENTS);
    input_ack(&s, 8);
    assert(s.event_count == 8 && s.events[0].id == 9 && s.events[7].id == 16);
    input_ack(&s, 18);
    assert(s.event_count == 8); // Unknown/dropped IDs cannot discard queued input.
    input_tick(&s, 5000);
    assert(!input_ui_enabled(&s, 5000) && !s.event_count && !s.frame && !s.voice_ready);
    assert(!input_configure(&s, true, 5100));
    report(&s, 5200, 1, 35, 247);
    report(&s, 5280, 0, 0, 0);
    assert(!s.event_count); // Renewal does not restore stale controls.
    fresh_frame(&s, 2);
    report(&s, 5500, 1, 35, 247);
    report(&s, 5580, 0, 0, 0);
    assert(s.event_count == 1);

    s = awake(0);
    report(&s, 20, 1, 35, 247);
    input_configure(&s, false, 40);
    input_configure(&s, true, 60);
    fresh_frame(&s, 2); // Reusing the token does not adopt an old press.
    report(&s, 100, 0, 0, 0);
    assert(!s.event_count);
    input_publish(&s, input_button(&s, 200, true));
    input_configure(&s, false, 220);
    input_configure(&s, true, 240);
    fresh_frame(&s, 3);
    input_publish(&s, input_button(&s, 300, false));
    assert(!s.event_count);
    input_publish(&s, input_button(&s, 400, true));
    input_publish(&s, input_button(&s, 480, false));
    assert(s.event_count == 1 && s.events[0].gesture == INPUT_BUTTON);

    s = awake(0);
    report(&s, 20, 1, 233, 240);
    input_tick(&s, 5000);
    input_configure(&s, true, 5100);
    fresh_frame(&s, 2);
    report(&s, 5200, 1, 233, 240);
    assert(!s.holdable); // Lease expiry also waits for this old hold to end.
    report(&s, 5300, 0, 0, 0);
    report(&s, 5400, 1, 233, 240);
    assert(s.holdable);

    s = awake(0);
    assert(input_idle(&s, true));
    input_tick(&s, 5000);
    assert(!s.idle.enabled && !idle_pending(&s.idle) && !s.frame);
    const uint64_t near_wrap = (uint64_t)UINT32_MAX - 100;
    s = awake(near_wrap);
    input_tick(&s, near_wrap + 200);
    assert(input_ui_enabled(&s, (uint32_t)(near_wrap + 200)));
    input_tick(&s, near_wrap + 5000);
    assert(!input_ui_enabled(&s, (uint32_t)(near_wrap + 5000)));
    input_tick(&s, near_wrap + 5000 + 0x80000000ULL);
    assert(!input_ui_enabled(&s, (uint32_t)(near_wrap + 5000 + 0x80000000ULL)));
    puts("Input checks passed: raw orientation, controls, queue, wake latches, voice gate, heartbeat.");
    return 0;
}
