#include "frame_rle.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    uint8_t pixels[10] = {0}; size_t offset = 0;
    uint8_t first[] = {0, 3, 0xf8, 0};
    uint8_t second[] = {0, 2, 0x07, 0xe0};
    assert(frame_rle_decode(pixels, sizeof(pixels), &offset, first, sizeof(first)));
    assert(offset == 6);
    assert(frame_rle_decode(pixels, sizeof(pixels), &offset, second, sizeof(second)));
    const uint8_t expected[] = {0xf8,0,0xf8,0,0xf8,0,0x07,0xe0,0x07,0xe0};
    assert(offset == 10 && !memcmp(pixels, expected, 10));
    assert(!frame_rle_decode(pixels, sizeof(pixels), &offset, first, 4));
    offset = 0;
    assert(!frame_rle_decode(pixels, sizeof(pixels), &offset, first, 3));
    const uint8_t zero[] = {0,0,0,0}, huge[] = {255,255,0,0};
    assert(!frame_rle_decode(pixels, sizeof(pixels), &offset, zero, 4));
    assert(!frame_rle_decode(pixels, sizeof(pixels), &offset, huge, 4));
    puts("RGB565 RLE bounds and chunk reconstruction passed.");
}
