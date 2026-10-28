idf_component_register(SRCS "main.c" "touch.c" "input.c" "tap.c" "gesture.c" "idle.c" "frame_rle.c" "audio.c" "mic_buffer.c" "voice_ring.c" INCLUDE_DIRS "."
    REQUIRES driver esp_driver_i2c esp_lcd esp_timer esp_psram json mbedtls)
target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Werror)
