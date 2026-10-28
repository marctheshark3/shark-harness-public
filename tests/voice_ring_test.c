#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "voice_ring.h"

#define SIZE (466 * 466 * 2)
static uint8_t frame[SIZE], striped[SIZE];

static unsigned count_pixels(void) {
    unsigned count = 0;
    for (int y = 0; y < 466; y++) for (int x = 0; x < 466; x++) {
        int i = (y * 466 + x) * 2;
        if (!frame[i] && !frame[i + 1]) continue;
        int dx = x - 233, dy = y - 233, radius = dx * dx + dy * dy;
        assert(radius >= 193 * 193 && radius <= 225 * 225);
        count++;
    }
    return count;
}

int main(void) {
    voice_ring_t ring;
    voice_ring_prepare(&ring, 1.0f, 0, true);
    voice_ring_draw(&ring, frame, 466, 0, 466);
    unsigned quiet = count_pixels();
    assert(quiet > 0);
    memset(frame, 0, SIZE);
    voice_ring_prepare(&ring, 1.0f, 10000, true);
    voice_ring_draw(&ring, frame, 466, 0, 466);
    assert(count_pixels() > quiet * 2); // Actual microphone level changes the shape.
    for (int y = 0; y < 466; y += 16)
        voice_ring_draw(&ring, striped + y * 466 * 2, 466, y, y + 16 <= 466 ? 16 : 466 - y);
    assert(!memcmp(frame, striped, SIZE)); // No seams at DMA strip boundaries.
    memset(striped, 0, SIZE);
    voice_ring_prepare(&ring, 2.0f, 10000, true);
    voice_ring_draw(&ring, striped, 466, 0, 466);
    assert(memcmp(frame, striped, SIZE)); // Animation advances without host frames.
    uint8_t high = ring.high, low = ring.low;
    voice_ring_prepare(&ring, 2.0f, 0, false);
    assert(high != ring.high || low != ring.low); // Playback differs from recording.
    puts("Voice ring checks passed: amplitude, safe annulus, strip seams, animation, colors.");
}
