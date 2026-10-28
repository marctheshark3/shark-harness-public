#include <assert.h>
#include <stdio.h>
#include "idle.h"
#include "gesture.h"
#include "tap.h"

typedef struct {
    idle_state_t idle;
    tap_filter_t tap;
    gesture_filter_t gesture;
    bool holdable;
    unsigned taps, actions;
} fixture_t;

// Exercise the same consumption/filter order as the touch task, including a
// long center hold and the release that would otherwise become a navigation.
static void report(fixture_t *f, uint64_t now, unsigned points, uint16_t x, uint16_t y) {
    bool consumed = idle_touch(&f->idle, points, x, y);
    if (consumed) { tap_cancel(&f->tap); gesture_cancel(&f->gesture); }
    if (tap_update(&f->tap, now, points, x, y)) f->taps++;
    if (gesture_update(&f->gesture, now, points, x, y, consumed ? 0 : 42)) f->actions++;
    f->holdable = !consumed && idle_controls_ready(&f->idle)
        && points == 1 && f->tap.active && !f->tap.blocked;
}

static idle_state_t sleeping(void) {
    idle_state_t s = {0};
    idle_image_committed(&s, 17);
    assert(idle_enter(&s));
    assert(s.enabled && !idle_pending(&s) && !idle_controls_ready(&s));
    return s;
}

static void present_fresh(idle_state_t *s) {
    idle_image_committed(s, s->image_version + 1);
    idle_presented(s, s->image_version);
}

int main(void) {
    fixture_t f = {.idle = sleeping()};
    assert(idle_enter(&f.idle)); // Retry preserves the same idle state.
    report(&f, 0, 1, 233, 240);
    assert(!f.idle.enabled && f.idle.wake == 1 && idle_pending(&f.idle));
    assert(!f.holdable && !f.taps && !f.actions);
    idle_leave(&f.idle); // Host wake acknowledgement is idempotent.
    idle_presented(&f.idle, 17); // A retained/old frame is not a fresh repaint.
    assert(f.idle.restoring);
    idle_image_committed(&f.idle, 18);
    assert(f.idle.restoring); // Accepted bytes are not a completed panel draw.
    idle_presented(&f.idle, 17);
    assert(f.idle.restoring); // Old DMA completion cannot open the new controls.
    idle_presented(&f.idle, 18);
    assert(!f.idle.restoring && idle_pending(&f.idle)); // Finger still down.
    for (unsigned now = 20; now <= 2400; now += 20) {
        report(&f, now, 1, 233, 240);
        assert(!f.holdable && !f.taps && !f.actions && f.idle.wake == 1);
    }
    report(&f, 2420, 0, 0, 0);
    assert(idle_controls_ready(&f.idle) && !f.taps && !f.actions);
    report(&f, 2600, 1, 413, 240);
    report(&f, 2700, 0, 0, 0);
    assert(f.taps == 1 && f.actions == 1); // A separate fresh tap works.
    report(&f, 3000, 1, 233, 240);
    assert(f.holdable); // New holds are allowed only after full restoration.
    assert(!idle_enter(&f.idle)); // Host inactivity race with a held finger.
    report(&f, 4000, 0, 0, 0);
    assert(idle_enter(&f.idle));

    idle_state_t s = sleeping();
    assert(idle_touch(&s, 1, 465, 465)); // Last actual display pixel is valid.
    assert(s.wake == 1 && idle_pending(&s));
    assert(idle_touch(&s, 0, 0, 0));
    assert(idle_pending(&s)); // Early release still waits for the panel.
    assert(idle_touch(&s, 1, 50, 200)); // Second press during upload is consumed.
    present_fresh(&s);
    assert(idle_pending(&s));
    assert(idle_touch(&s, 0, 0, 0));
    assert(idle_controls_ready(&s));

    s = sleeping();
    assert(idle_button(&s, true)); // BOOT wakes instead of selecting a thread.
    assert(s.wake == 1 && !s.enabled && idle_pending(&s));
    assert(idle_touch(&s, 1, 233, 240)); // Overlapping inputs are also consumed.
    present_fresh(&s);
    assert(idle_button(&s, false));
    assert(idle_pending(&s));
    assert(idle_touch(&s, 0, 0, 0));
    assert(idle_controls_ready(&s));
    assert(!idle_button(&s, true));
    assert(!idle_enter(&s)); // BOOT already held on an attempted idle entry.
    assert(!idle_button(&s, false));
    assert(idle_enter(&s));
    assert(idle_touch(&s, 1, 0, 0)); // Origin is also a valid wake coordinate.
    assert(s.wake == 2);

    const unsigned invalid[][3] = {{1,466,0}, {1,0,466}, {1,65535,65535}, {2,233,233}};
    for (unsigned i = 0; i < sizeof(invalid) / sizeof(invalid[0]); i++) {
        s = sleeping();
        assert(idle_touch(&s, invalid[i][0], invalid[i][1], invalid[i][2]));
        assert(s.enabled && s.wake == 0);
        assert(idle_touch(&s, 1, 233, 233)); // Bad contact must really release.
        assert(s.enabled && s.wake == 0);
        assert(idle_touch(&s, 0, 0, 0));
        assert(idle_touch(&s, 1, 233, 233));
        assert(!s.enabled && s.wake == 1);
    }

    // A report gap never calls idle_touch with zero points. Interrupting the
    // legacy gesture filters must not clear the independent wake latch.
    f = (fixture_t){.idle = sleeping()};
    report(&f, 0, 1, 233, 240);
    tap_cancel(&f.tap); gesture_interrupt(&f.gesture);
    present_fresh(&f.idle);
    assert(idle_pending(&f.idle));
    report(&f, 5000, 1, 233, 240);
    assert(!f.holdable && !f.actions);
    report(&f, 5020, 0, 0, 0);
    assert(idle_controls_ready(&f.idle) && !f.actions);

    s = sleeping();
    idle_leave(&s); // Explicit host wake has no physical-wake counter change.
    assert(!s.enabled && s.wake == 0 && idle_pending(&s));
    idle_leave(&s);
    assert(s.restore_after == 17);
    present_fresh(&s);
    assert(idle_controls_ready(&s));

    s = sleeping();
    idle_disconnect(&s); // Lease expiry needs no future host frame.
    assert(!s.enabled && s.wake == 0 && idle_controls_ready(&s));
    s = sleeping();
    assert(idle_button(&s, true));
    idle_disconnect(&s);
    assert(idle_pending(&s)); // Expiry still consumes a held wake gesture.
    assert(idle_button(&s, true));
    assert(idle_button(&s, false));
    assert(idle_controls_ready(&s));

    s = (idle_state_t){.image_version = UINT32_MAX, .wake = 1000000};
    assert(idle_enter(&s));
    assert(idle_touch(&s, 1, 233, 233));
    assert(s.wake == 1);
    assert(idle_touch(&s, 0, 0, 0));
    idle_presented(&s, UINT32_MAX);
    assert(idle_pending(&s));
    idle_image_committed(&s, 0);
    idle_presented(&s, 0);
    assert(idle_controls_ready(&s)); // Image generation wrap remains safe.
    puts("Idle checks passed: wake-only input, fresh-frame gate, overlap, bounds, expiry, wrap.");
    return 0;
}
