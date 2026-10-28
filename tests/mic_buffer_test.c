#include "mic_buffer.h"
#include <assert.h>
#include <string.h>

int main(void) {
    uint8_t storage[64], input[32], output[64];
    mic_buffer_t buffer;
    mic_buffer_reset(&buffer, storage, sizeof(storage));
    // Many recording buffers' worth of audio, with USB retries and wraparound.
    for (unsigned block = 0; block < 100000; block++) {
        for (unsigned i = 0; i < sizeof(input); i++) input[i] = (uint8_t)(block + i);
        unsigned offset = buffer.end;
        assert(mic_buffer_write(&buffer, input, sizeof(input)));
        assert(mic_buffer_read(&buffer, offset, output, sizeof(input)));
        assert(memcmp(input, output, sizeof(input)) == 0);
        // A lost USB response must leave the same bytes readable.
        assert(mic_buffer_read(&buffer, offset, output, sizeof(input)));
        assert(memcmp(input, output, sizeof(input)) == 0);
        assert(mic_buffer_ack(&buffer, offset + sizeof(input)));
    }
    assert(buffer.end == 3200000);

    mic_buffer_reset(&buffer, storage, sizeof(storage));
    assert(mic_buffer_write(&buffer, input, sizeof(input)));
    assert(mic_buffer_write(&buffer, input, sizeof(input)));
    assert(!mic_buffer_write(&buffer, input, 1)); // Never overwrite unsent audio.
    assert(!mic_buffer_ack(&buffer, 65));
    assert(mic_buffer_ack(&buffer, 20));
    assert(!mic_buffer_ack(&buffer, 19));
    assert(!mic_buffer_read(&buffer, 0, output, 1));
    assert(!mic_buffer_read(&buffer, 64, output, 1));
    assert(mic_buffer_write(&buffer, input, 20));
    assert(mic_buffer_read(&buffer, 48, output, 36)); // Cross the physical end.
    assert(memcmp(output, input + 16, 16) == 0);
    assert(memcmp(output + 16, input, 20) == 0);

    buffer.start = buffer.end = UINT32_MAX - 1;
    assert(!mic_buffer_write(&buffer, input, 2)); // Counter overflow is an error.
    mic_buffer_reset(&buffer, storage, sizeof(storage));
    for (unsigned i = 0; i < sizeof(storage); i++) assert(storage[i] == 0);
    return 0;
}
