#include <assert.h>
#include <stdio.h>
#include "tap.h"

int main(void) {
    tap_filter_t filter = {0};
    assert(!tap_update(&filter, 0, 0, 0, 0));
    assert(!tap_update(&filter, 100, 1, 230, 231));
    assert(!tap_update(&filter, 140, 1, 233, 234));
    assert(tap_update(&filter, 200, 0, 0, 0));
    assert(!tap_update(&filter, 220, 0, 0, 0)); // No repeated action after release.
    assert(!tap_update(&filter, 230, 1, 233, 234));
    assert(!tap_update(&filter, 270, 0, 0, 0)); // Debounce.

    assert(!tap_update(&filter, 500, 1, 233, 234));
    assert(!tap_update(&filter, 650, 1, 310, 234)); // Drag cancels until release.
    assert(!tap_update(&filter, 670, 1, 233, 234));
    assert(!tap_update(&filter, 700, 0, 0, 0));
    assert(!tap_update(&filter, 800, 1, 233, 234));
    assert(!tap_update(&filter, 1800, 1, 233, 234)); // A held finger never repeats.
    assert(!tap_update(&filter, 1850, 0, 0, 0));

    assert(!tap_update(&filter, 2000, 2, 233, 234)); // Multiple fingers are not a tap.
    assert(!tap_update(&filter, 2020, 1, 233, 234));
    assert(!tap_update(&filter, 2100, 0, 0, 0));
    assert(!tap_update(&filter, 2200, 1, 466, 234)); // Outside panel bounds.
    assert(!tap_update(&filter, 2250, 0, 0, 0));

    assert(!tap_update(&filter, 2300, 1, 233, 234));
    tap_cancel(&filter); // I2C interruption cannot synthesize a release action.
    assert(!tap_update(&filter, 2400, 0, 0, 0));
    assert(!tap_update(&filter, 2600, 1, 233, 234));
    assert(tap_update(&filter, 2670, 0, 0, 0)); // Recovered after a real release.

    filter = (tap_filter_t){0};
    assert(!tap_update(&filter, 3000, 1, 230, 231));
    assert(!tap_update(&filter, 3005, 0, 0, 0)); // Glitch, shorter than a sample period.
    puts("Tap checks passed: release, debounce, drag, hold, multitouch, bounds, recovery.");
    return 0;
}
