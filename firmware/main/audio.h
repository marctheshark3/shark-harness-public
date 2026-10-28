#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "cJSON.h"
#include "driver/i2c_master.h"

#define AUDIO_RATE 22050
#define AUDIO_MAX_BYTES (AUDIO_RATE * 2 * 20)

typedef struct {
    bool ready, uploading, armed, playing, stopped;
    unsigned offset, bytes, played, volume;
    uint32_t crc;
    int error;
} audio_status_t;

void audio_start(i2c_master_bus_handle_t bus);
audio_status_t audio_status(void);
void audio_stop(void);
bool audio_tap(void);
const char *audio_command(const char *op, cJSON *root);

#define MIC_RATE 16000
// Unacknowledged audio only; the host drains this ring while recording.
#define MIC_MAX_BYTES (MIC_RATE * 2 * 30)
typedef struct {
    bool ready, enabled, recording, available;
    unsigned id, bytes, level;
    uint32_t crc;
    int error;
} mic_status_t;
mic_status_t mic_status(void);
// Adds bounded capture data to the normal USB response for mic.read.
const char *mic_command(const char *op, cJSON *root, cJSON *extra);
void mic_json(cJSON *root);
