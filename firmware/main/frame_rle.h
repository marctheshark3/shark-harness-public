#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

bool frame_rle_decode(uint8_t *out, size_t capacity, size_t *offset,
                      const uint8_t *data, size_t bytes);
