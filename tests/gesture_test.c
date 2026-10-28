#include <assert.h>
#include <stdio.h>
#include "gesture.h"

int main(void) {
    gesture_filter_t f = {0};
    assert(!gesture_update(&f, 0, 1, 330, 210, 12));
    assert(!gesture_update(&f, 100, 1, 160, 215, 12));
    assert(gesture_update(&f, 200, 0, 0, 0, 12) == GESTURE_LEFT);
    assert(f.last_x == 160 && f.frame == 12);
    gesture_update(&f, 300, 1, 140, 200, 12);
    gesture_update(&f, 400, 1, 310, 200, 12);
    assert(gesture_update(&f, 500, 0, 0, 0, 12) == GESTURE_RIGHT);
    gesture_update(&f, 600, 1, 310, 365, 13);
    assert(gesture_update(&f, 680, 0, 0, 0, 13) == GESTURE_TAP);
    gesture_update(&f, 800, 1, 233, 240, 13);
    assert(!gesture_update(&f, 1800, 0, 0, 0, 13)); // Hold never sends a tap.
    gesture_update(&f, 1900, 1, 310, 365, 13);
    assert(!gesture_update(&f, 2000, 0, 0, 0, 14)); // Changed screen.
    gesture_update(&f, 2100, 1, 310, 365, 14);
    gesture_cancel(&f);
    assert(!gesture_update(&f, 2200, 0, 0, 0, 14)); // Lost report.
    gesture_update(&f, 2300, 2, 310, 365, 14);
    assert(!gesture_update(&f, 2400, 0, 0, 0, 14)); // Multiple fingers.
    gesture_update(&f, 2500, 1, 200, 150, 14);
    gesture_update(&f, 2600, 1, 210, 280, 14);
    assert(!gesture_update(&f, 2700, 0, 0, 0, 14)); // Vertical drag.
    gesture_update(&f, 2800, 1, 200, 150, 0);
    assert(!gesture_update(&f, 2900, 0, 0, 0, 15)); // Upload in progress.
    gesture_update(&f, 3000, 1, 413, 240, 15);
    assert(gesture_update(&f, 4100, 0, 0, 0, 15) == GESTURE_TAP); // Deliberate arrow press.
    gesture_update(&f, 4200, 1, 233, 240, 15);
    assert(!gesture_update(&f, 5300, 0, 0, 0, 15)); // Center hold remains voice-only.
    assert(gesture_control(53, 240) && gesture_control(413, 240));
    assert(gesture_control(135, 369) && gesture_control(331, 369));
    assert(!gesture_control(233, 240));
    // No-report/ACK markers while idle must not poison the next touch. This
    // distinguishes an idle controller from an interrupted active gesture.
    f = (gesture_filter_t){0};
    gesture_interrupt(&f);
    gesture_interrupt(&f);
    gesture_update(&f, 6000, 1, 413, 240, 17);
    assert(gesture_update(&f, 6100, 0, 0, 0, 17) == GESTURE_TAP);
    gesture_interrupt(&f);
    gesture_update(&f, 9000, 1, 331, 369, 17);
    assert(gesture_update(&f, 9100, 0, 0, 0, 17) == GESTURE_TAP);
    gesture_update(&f, 9200, 1, 413, 240, 17);
    gesture_interrupt(&f);
    assert(!gesture_update(&f, 9300, 0, 0, 0, 17));
    // Captured Pause presses before applying this panel's Y-axis calibration.
    assert(gesture_control(353, 465 - 86));
    assert(gesture_control(355, 465 - 76));
    assert(gesture_control(233, 80)); // Project selector cannot start capture.
    assert(gesture_control(178, 435) && gesture_control(288, 435)); // Curved action ends.
    assert(gesture_control(233, 432)); // Curved Open project control.
    assert(gesture_control(233, 450)); // Includes its lower painted edge.
    assert(gesture_control(233, 119)); // Project selector's curved label/chevron.
    assert(!gesture_control(233, 290)); // Hold-to-talk instruction remains voice input.
    gesture_update(&f, 9400, 1, 288, 435, 17);
    assert(gesture_update(&f, 10900, 0, 0, 0, 17) == GESTURE_TAP);
    puts("Gesture checks passed: swipes, tap, hold, stale screen, cancellation, multitouch, drag.");
    return 0;
}
