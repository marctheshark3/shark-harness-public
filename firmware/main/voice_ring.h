#pragma once
#include <stdbool.h>
#include <stdint.h>

#define VOICE_RING_BARS 96

typedef struct {
    int x0, y0, x1, y1;
} voice_ring_bar_t;

typedef struct {
    voice_ring_bar_t bars[VOICE_RING_BARS];
    uint8_t high, low;
} voice_ring_t;

// A single perimeter waveform, outside the host's text and action area.
// Pure C so the exact device animation can also be previewed and tested on host.
void voice_ring_prepare(voice_ring_t *ring, float seconds, unsigned level, bool recording);
void voice_ring_draw(const voice_ring_t *ring, uint8_t *pixels, int width, int y, int rows);
