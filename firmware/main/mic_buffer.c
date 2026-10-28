#include "mic_buffer.h"
#include <string.h>

void mic_buffer_reset(mic_buffer_t *buffer, uint8_t *data, uint32_t capacity) {
    *buffer = (mic_buffer_t){.data = data, .capacity = capacity};
    memset(data, 0, capacity);
}

bool mic_buffer_write(mic_buffer_t *buffer, const uint8_t *data, uint32_t count) {
    if (!buffer->capacity || count > buffer->capacity - (buffer->end - buffer->start)
        || count > UINT32_MAX - buffer->end) return false;
    uint32_t index = buffer->end % buffer->capacity;
    uint32_t first = count < buffer->capacity - index ? count : buffer->capacity - index;
    memcpy(buffer->data + index, data, first);
    memcpy(buffer->data, data + first, count - first);
    buffer->end += count;
    return true;
}

bool mic_buffer_ack(mic_buffer_t *buffer, uint32_t offset) {
    if (offset < buffer->start || offset > buffer->end) return false;
    buffer->start = offset;
    return true;
}

bool mic_buffer_read(const mic_buffer_t *buffer, uint32_t offset, uint8_t *data, uint32_t count) {
    if (!buffer->capacity || offset < buffer->start || offset > buffer->end
        || count > buffer->end - offset) return false;
    uint32_t index = offset % buffer->capacity;
    uint32_t first = count < buffer->capacity - index ? count : buffer->capacity - index;
    memcpy(data, buffer->data + index, first);
    memcpy(data + first, buffer->data, count - first);
    return true;
}
