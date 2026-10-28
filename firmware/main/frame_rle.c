#include "frame_rle.h"

bool frame_rle_decode(uint8_t *out, size_t capacity, size_t *offset,
                      const uint8_t *data, size_t bytes) {
    if (!bytes || bytes % 4 || *offset > capacity || *offset % 2) return false;
    size_t written = *offset;
    for (size_t i = 0; i < bytes; i += 4) {
        unsigned count = ((unsigned)data[i] << 8) | data[i + 1];
        if (!count || count > (capacity - written) / 2) return false;
        for (unsigned p = 0; p < count; p++) {
            out[written++] = data[i + 2]; out[written++] = data[i + 3];
        }
    }
    *offset = written;
    return true;
}
