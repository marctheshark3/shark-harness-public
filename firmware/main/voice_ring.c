#include "voice_ring.h"
#include <math.h>
#include <stdlib.h>

void voice_ring_prepare(voice_ring_t *ring, float seconds, unsigned level, bool recording) {
    const float tau = 6.28318530718f;
    float energy = recording ? sqrtf(fminf(level / 10000.0f, 1.0f))
                             : 0.45f + 0.25f * sinf(seconds * 3.0f);
    unsigned red = recording ? 255 : 140;
    unsigned green = recording ? 115 : 232;
    unsigned blue = recording ? 154 : 204;
    uint16_t color = ((red & 248U) << 8) | ((green & 252U) << 3) | (blue >> 3);
    ring->high = color >> 8;
    ring->low = color & 255;
    for (int i = 0; i < VOICE_RING_BARS; i++) {
        float angle = tau * i / VOICE_RING_BARS;
        float envelope = 0.65f + 0.35f * sinf(angle * 3 - seconds * 2.4f);
        float inner = 217 - energy * 21 * envelope;
        float c = cosf(angle), s = sinf(angle);
        ring->bars[i] = (voice_ring_bar_t){
            .x0 = lroundf(233 + inner * c), .y0 = lroundf(233 + inner * s),
            .x1 = lroundf(233 + 222 * c), .y1 = lroundf(233 + 222 * s),
        };
    }
}

void voice_ring_draw(const voice_ring_t *ring, uint8_t *pixels, int width, int y, int rows) {
    for (int i = 0; i < VOICE_RING_BARS; i++) {
        voice_ring_bar_t bar = ring->bars[i];
        int top = bar.y0 < bar.y1 ? bar.y0 : bar.y1;
        int bottom = bar.y0 > bar.y1 ? bar.y0 : bar.y1;
        if (bottom + 1 < y || top - 1 >= y + rows) continue;
        int x0 = bar.x0, y0 = bar.y0;
        int dx = abs(bar.x1 - x0), sx = x0 < bar.x1 ? 1 : -1;
        int dy = -abs(bar.y1 - y0), sy = y0 < bar.y1 ? 1 : -1;
        int error = dx + dy;
        while (true) {
            // Round, three-pixel strokes with clipping at strip boundaries.
            for (int oy = -1; oy <= 1; oy++) for (int ox = -1; ox <= 1; ox++) {
                int x = x0 + ox, row = y0 + oy - y;
                if (abs(ox) + abs(oy) > 1 || x < 0 || x >= width || row < 0 || row >= rows) continue;
                int offset = (row * width + x) * 2;
                pixels[offset] = ring->high;
                pixels[offset + 1] = ring->low;
            }
            if (x0 == bar.x1 && y0 == bar.y1) break;
            int twice = 2 * error;
            if (twice >= dy) { error += dy; x0 += sx; }
            if (twice <= dx) { error += dx; y0 += sy; }
        }
    }
}
