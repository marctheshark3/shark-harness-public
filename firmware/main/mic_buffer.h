#pragma once
#include <stdbool.h>
#include <stdint.h>

// Absolute offsets keep retransmitted USB reads separate from new audio.
// Acknowledged bytes can be reused without imposing a recording duration.
typedef struct {
    uint8_t *data;
    uint32_t capacity, start, end;
} mic_buffer_t;

void mic_buffer_reset(mic_buffer_t *buffer, uint8_t *data, uint32_t capacity);
bool mic_buffer_write(mic_buffer_t *buffer, const uint8_t *data, uint32_t count);
bool mic_buffer_ack(mic_buffer_t *buffer, uint32_t offset);
bool mic_buffer_read(const mic_buffer_t *buffer, uint32_t offset, uint8_t *data, uint32_t count);
