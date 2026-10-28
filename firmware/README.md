# Independent display firmware

This first firmware targets the **ESP32-S3 / CST9217 + AXP2101 board** with 32 MB
flash, 8 MB octal PSRAM, and CO5300 466 × 466 QSPI AMOLED. Other
revisions may use different reset/power pins; identify the board before flashing.

It uses ESP-IDF 5.5.1 and the official Espressif CO5300 2.2.0 driver. The application
implements its own ambient animation, double-buffered images in PSRAM, and USB
protocol. It contains no vendor host, agent framework, display UI, or firmware
source. CST92xx touch uses the pinned, MIT-licensed
[MicroPixel driver](https://components.espressif.com/components/78/esp_lcd_touch_cst92xx/versions/0.1.0/readme)
through Espressif's touch API. Speaker output uses the Apache-2.0
[Espressif esp_codec_dev 1.6.1 driver](https://components.espressif.com/components/espressif/esp_codec_dev/versions/1.6.1/readme).
Microphone input uses the ES7210 driver from the same component. Network and OTA
are not implemented.

## Touch behavior

Without the companion interface, tap and release to cycle aurora, orbit, and ember ambient scenes. A tap while an
image is visible dismisses it, and the existing host sender does not resend the
same unchanged scene. Native interactions work without the host. The selected
ambient scene stays in RAM until the next boot.

The controller is at I²C address `0x5A`, SDA 15 / SCL 14, reset GPIO 40. Reports
are polled every 20 ms and acknowledged by the driver. Single-finger contacts
lasting 20–700 ms and moving at most 32 pixels count once on release, with a
180 ms debounce. Holds, drags, multitouch, out-of-bounds coordinates, and interrupted
reports do not generate taps. The renderer applies actions at frame boundaries
and shows a short ripple. A touch-driver failure leaves display/USB operational.

The host can enable session controls with `ui.configure`, renewed at least every
five seconds. Horizontal swipes cover at least 64 pixels, stay within a 2:1
horizontal/vertical ratio, and finish within 1200 ms. A tap or swipe is reported
against the visible controls' `uiToken`. Normal home cards within one project share a control token
so relative navigation survives label/image uploads. New-session, voice, review,
and confirmation screens use different tokens and reject old input. The host
drains a bounded 16-event queue with `ui.ack`; bursts are applied in order before
painting the resulting card. Queue overflow increments `ui.dropped`.
Idle ACK/no-report periods must not cancel the next fresh touch: only an active
gesture is interrupted by missing reports. A regression test covers taps after
an idle gap, the failure that previously swallowed most isolated button presses.
This CST9217/CO5300 board also requires inverted touch Y: `y = 465 - raw_y`.
Live Pause presses at raw (353,86) and (355,76) confirmed the orientation; without
this correction, bottom buttons mapped to empty space at the top of the display.
X remains unchanged. A subsequent physical left-arrow press reported (35,247)
and selected the previous project; right-arrow presses around (433,254)
selected the next project. The row-major panel image is not mirrored. The board
transform now lives in `input_coordinates()` and is applied exactly once, before
any gesture, idle wake, or voice-control classification. Out-of-range raw
coordinates are rejected before subtraction.
Project-selector, arrow, and bottom-button contacts up to two seconds activate on release; these
areas cannot start microphone capture. Center holds retain hold-to-talk behavior.
New holds wait until image replacement finishes. Short taps consumed by speaker
start/stop are marked separately.
`frame.begin` accepts a boolean `holdToTalk` (default true for legacy frames).
The gate commits with the displayed frame; project selection and confirmation
screens disable hold initiation. Status advertises `ui.voiceGate` and reports
`ui.holdToTalk`. That field records the committed image's preference; actual
hold initiation also waits for an active lease, completed upload, a valid voice
region, and the idle wake gate. The standalone host requires this capability.
The circular layout additionally advertises `ui.layoutVersion: 2` and
`ui.voiceRing: true`. Its upper project control covers y=45–126; the curved
bottom controls cover y=327–452. These regions remain excluded from voice holds.
The host requires both capabilities so new controls cannot run against old
touch regions or the old horizontal audio waveform.
Loss of the UI heartbeat returns to native ambient visuals.

Status includes `ambientScene` and `touch: {ready, pressed, taps, x, y, error}`.
`ready` records successful controller initialization; `error` is the last transport
error, cleared by a valid report. Coordinates describe the last valid single
contact, and `taps` counts accepted releases. `ui` adds `active`, `event`, `gesture`
(1 tap, 2 swipe left, 3 swipe right, 4 consumed by audio, 5 user key), `x`, `y`, `eventFrame`,
and `frame`. The latter two contain control tokens as eight-digit hex strings,
not image checksums. `ui.eventQueue` advertises `events`, with event IDs,
coordinates, `startedMs` and `releasedMs` in firmware uptime. `contacts`,
`releases`, `tapDrops` and `dropped` support live input measurements.
`imageVersion`, `presentedVersion`, `presentedMs` and `nowMs` distinguish an
accepted image from a completed panel draw, even when its controls share a token.
The physical user/BOOT key at GPIO0 is polled as an input
with a pull-up. A short press emits gesture 5; the companion maps it to selecting
the open T3 conversation using its client helper. Long holds,
frame changes, and presses without a host UI do nothing. ROM boot behavior is
unchanged. `buttonReady` and `buttonPressed` in `ui` report its input state. The
other physical button has not been remapped. Pure C gesture tests run in CI;
physical touch and key validation still need a person.

## Hardware-free input validation

The hardware-free [device simulator](../docs/simulator.md) uses the same pure C
input path as `touch.c`: raw-coordinate calibration, tap/swipe/key filters,
control regions, voice gating, the 16-event queue, heartbeat expiry, and wake
latches. Its completed-frame step stands in for the panel draw. It does not
exercise I²C sensing, real USB timing, microphone hardware, speaker acoustics,
or panel DMA. The Python wrapper compiles the shared library into a private
temporary directory; it does not open a serial port or contact the live bridge.
The simulator also exposes `ui.holdReady` for inspecting the effective input
gate; its `touch.holdable` comes directly from the production press classifier.

The combined native input regression runs without ESP-IDF or a device:

```bash
cc -std=c11 -Wall -Wextra -Werror -Ifirmware/main tests/input_test.c firmware/main/input.c firmware/main/gesture.c firmware/main/idle.c firmware/main/tap.c -o /tmp/input-test
/tmp/input-test
python -m unittest discover -s tests -p test_native_input.py -v
```

## Idle animation and wake

The companion defaults to a **60-second idle timeout**. It sends `ui.idle` with
`enabled: true` only on a resting home screen, with no recording, transcript
review, pending voice job, speech, upload, or held input. The display replaces
the card with its native ambient animation at **6% panel brightness**, compared
with the normal **25%**. Fixed wordmark, scene dots, and outer status ring are
omitted while idle. The host timeout is configurable with `device.py watch
--idle-seconds N` / `SHARK_IDLE_SECONDS`; zero disables it. This is a dim animated
screen, not deep sleep: USB, the touch controller, and host heartbeat stay alive.

The first valid single-finger contact or BOOT press wakes the panel. That entire
contact, including a long hold, drag, release, or overlapping key/touch press,
is consumed: it cannot navigate, start/stop speaker audio, or begin recording.
Invalid coordinates and multitouch do not wake it. Input rearms only after a
real release **and** a newly committed host image has completed its panel draw.
The retained pre-idle card may appear immediately at normal brightness, but its
controls remain disabled until that fresh repaint. Missing touch reports never
count as a release. An explicit `ui.idle` with `enabled: false` follows the same
fresh-frame gate, without incrementing the physical wake counter.

This is an additive protocol-1 capability; geometry and `ui.layoutVersion: 2`
are unchanged. New hosts enable idle only when `ui.idleMode` is true. Status adds:

| `ui` field | Meaning |
| --- | --- |
| `idleMode` | `true` advertises `ui.idle` support |
| `idle` | Native dim ambient mode is selected |
| `wake` | Physical wake counter; starts at 0, then cycles 1–1,000,000 |
| `wakePending` | Wake contact/key release or a fresh completed host frame is still required |
| `brightness` | Last applied panel brightness percentage: 6 idle, 25 awake |

While idle/restoring, `ui.frame` is zero and `ui.holdToTalk` is false. The host
keeps renewing `ui.configure`, observes a `wake` change or `idle: false`, sends
`ui.idle` false, discards old queued input, then sends its current card again
even when the pixels are unchanged. `imageActive` records the retained image
and remains true during idle; its expiry countdown is paused while sleeping.
Loss of the five-second UI lease still exits idle, discards the host image,
and returns to native visuals at normal brightness. Renewing an expired lease
never restores the old controls; the host must repaint. A contact or BOOT press
already held when a lease expires or is disabled must release before it can act
on the restored UI, even if that UI reuses its previous token.

Wrong `enabled` types return `invalid_ui_idle`; entry without a live UI lease
returns `ui_inactive`. Entry during frame upload, speaker upload/armed playback,
recording or a retained microphone clip, held input, or incomplete restoration
returns `ui_idle_busy`. No active job is canceled to enter idle. `mic.start`
also returns `ui_idle_busy` until the display has woken and its controls are ready.

The pure native state/filter regression check runs without hardware:

```bash
cc -std=c11 -Wall -Wextra -Werror -Ifirmware/main tests/idle_test.c firmware/main/idle.c firmware/main/gesture.c firmware/main/tap.c -o /tmp/idle-test
/tmp/idle-test
```

With the companion stopped, the opt-in hardware protocol check uses synthetic
images and disables the microphone; it does not play audio:

```bash
python tests/hardware_idle_smoke.py --port "$DISPLAY_PORT"
```

It checks dimming, animation/USB progress, busy guards, the fresh-frame gate,
and heartbeat recovery. Physical tap/BOOT wake and the appearance/brightness of
the animation still require a person to verify on the display.

## Speaker behavior

The ES8311 at I²C `0x18` shares SDA 15 / SCL 14 with touch. I²S uses
MCLK 16, BCLK 9, WS 45, output 8, input 10, and amplifier enable 46.
These directions follow the ES8311 DSDIN / ES7210 SDOUT
nets in the [board schematic](https://files.waveshare.com/wiki/ESP32-S3-Touch-AMOLED-1.75C/ESP32-S3-Touch-AMOLED-1.75C-schematic.pdf),
rather than the reversed direction comments in the original harness pin header.
Speaker clips are mono PCM16 little-endian at **22050 Hz**, at most
**882000 bytes / 20 seconds**. One PSRAM buffer holds a complete clip. The firmware
checks size, ordered chunks, and CRC32 before starting an independent playback
task; it duplicates samples to stereo I²S slots. Display and touch keep running.

The amplifier stays off while idle/uploading, when muted, and after Stop. Volume
ranges from 0 to 80, default 60. A tap stops an active clip/upload before any scene
action. Animated bars indicate playback, including over custom visuals. At clip
completion, trailing silence drains DMA before closing the codec. Playback ends
locally even if the host disconnects; incomplete uploads expire after 30 seconds.
`audio.end` with `onTap: true` instead holds the verified clip for up to an hour.
The next touchscreen tap starts it locally without a host round trip; subsequent
taps stop playback. Stop also disarms a waiting clip.

`audio` status contains `ready`, `uploading`, `armed`, `playing`, `stopped`, `offset`, `bytes`,
`played`, `volume`, `crc32`, and `error`. `ready` means codec/driver initialization
succeeded; `error` is the latest initialization or playback error. `played` counts
mono PCM bytes accepted by the I²S driver, with playback marked finished after
the final DMA drain. Stop turns off the amplifier immediately; `playing` becomes
false when the playback task finishes cleanup. A new upload must wait for that.
Audio commands remain available if display initialization fails.

Opt-in local hardware checks (stop the companion service and any `watch` first):

```bash
python tests/hardware_audio_smoke.py --port "$DISPLAY_PORT"
```

This test mutes playback, exercises validation, repeated clips, stop, and display/
touch progress, then restores the previous volume. It does not verify acoustic
quality. On the development unit it passed, and a live bridge speech request completed.
After the GPIO8 speaker-data correction, the user confirmed clear, working sound
from the preloaded tap-triggered message at volume 70; bridge status also showed
successful completion. Physical tap-to-stop remains a separate manual check.

## Microphone behavior

The ES7210 at I²C `0x40` enables MIC1/MIC2 in standard stereo I²S, 16-bit at
**16000 Hz**, with 30 dB input gain. The first microphone slot is retained as mono.
The initial 48 ms are discarded for ADC settling. Playback and capture share a
data interface and clocks; they are mutually exclusive. A microphone initialization
failure leaves the speaker and display usable.

Capture starts only after an explicit host `mic.start` or a single-finger hold of
800 ms while the host has enabled microphone input. Release ends a held capture.
Holding to talk first cancels speaker playback and waits for its codec to close.
Recording shows one pink circular waveform over all visuals. Its radial strokes
respond to the smoothed microphone peak; the same renderer uses mint for playback.
There is no separate horizontal waveform or host-drawn recording graphic.
Recording continues until release or an explicit `mic.stop`. The companion reads
audio while recording, and its next read acknowledges the previous bytes so the
**960000-byte** PSRAM ring can be reused. There is no recording duration timeout.
Loss of the five-second host heartbeat discards the recording. Buffer exhaustion
also reports an error and discards the recording rather than submitting partial
speech. Completed captures expire after two minutes, and `mic.clear` zeroes the
buffer once recording has stopped.

`mic` status includes `ready`, `enabled`, `recording`, `available`, `id`, `bytes`,
`sampleRate`, `level` (latest peak), `crc32`, `streaming: true`, and `error`. Capture
chunks can be read during recording or after release using the capture id,
absolute byte offset, and an optional even `count` of 2–2048 bytes. Each response
includes `captureCrc32` for that chunk. Only bytes already returned may be
acknowledged by advancing the read offset; repeating the last read is safe.
The host verifies each chunk and the full CRC before transcription. USB responses
can be up to 8192 bytes including newline.

Opt-in `python tests/hardware_listen_smoke.py --port "$DISPLAY_PORT"` records briefly
in RAM, checks transfer/CRC, clear, heartbeat expiry and subsequent muted playback.
It never saves audio, runs transcription, or sends a T3 prompt. Stop `watch` first.
Add `--stream-seconds 35` to verify a capture beyond the old limit, with live USB
draining and a complete checksum after stopping.

## Build

```bash
bash firmware/build.sh
```

Docker must be available. The script pins the official `espressif/idf` 5.5.1 image
by digest; `dependencies.lock` pins the panel component and its dependencies.
The container mounts only this firmware directory, with no USB access. Output in
`firmware/build/release/` includes bootloader, partition table, app, flash arguments,
project license/notices, dependency pins, collected upstream notices under
`licenses/`, and SHA256SUMS covering the release contents. The notice manifest
records the pinned container image, ESP-IDF revision, and notice file hashes;
packaging fails if a managed component has no standalone notice file. CI builds
and archives the same files; it never flashes hardware. Review the
[dependency inventory](../docs/dependencies.md) before distributing a build.

## Back up before the first flash

Stop the companion (`python3 companion.py stop`), any original vendor daemon,
and any running `device.py watch`. Use the device's persistent `/dev/serial/by-id/` path. Install
`esptool==5.1.0` in a local virtual environment. Keep backups private and outside
the repo: they can contain Wi-Fi credentials and other device configuration.

```bash
DISPLAY_PORT=/dev/serial/by-id/YOUR_DEVICE
esptool --chip esp32s3 --port "$DISPLAY_PORT" --no-stub get-security-info
# Stop here if encryption/secure boot is enabled; this guide assumes both off.
umask 077
mkdir -p /private/path/device-backup
esptool --chip esp32s3 --port "$DISPLAY_PORT" --baud 460800 read-flash 0 ALL /private/path/device-backup/original-flash.bin
esptool --chip esp32s3 --port "$DISPLAY_PORT" verify-flash 0 /private/path/device-backup/original-flash.bin
sha256sum /private/path/device-backup/original-flash.bin > /private/path/device-backup/SHA256SUMS
```

Read `ALL`, not a guessed flash size. On this board a complete read takes around
16 minutes. Verify the entire backup against the device before proceeding. USB
bootloader entry and a verified 32 MB backup were completed on the development
unit. Record your own backup location and checksum outside this repository.
The original restore procedure has not been tested.

## Flash locally

After a verified backup and successful build:

```bash
cd firmware/build/release
sha256sum -c SHA256SUMS
esptool --chip esp32s3 --port "$DISPLAY_PORT" --baud 460800 write-flash @flash_args
```

Writing verifies the flashed segments. This replaces the bootloader, partition
table, and app; it does not erase the whole flash or write eFuses. The first app
partition is at `0x10000`. Keep the original daemon stopped so it does not compete
for USB or try to manage the device.

From the repo root, `python device.py --port "$DISPLAY_PORT" status` should report
`product: shark-display`, `protocol: 1`, `ready: true`, and an increasing `frames`
counter. These verify firmware/transport progress; also inspect the physical
screen for correct colors and alignment. The initial ambient screen on the development unit
was confirmed visible by its operator.

If recovery is needed, enter the ROM bootloader over USB (or hold BOOT while
resetting if automatic entry fails), verify the private backup's local SHA-256,
then restore the full image:

```bash
esptool --chip esp32s3 --port "$DISPLAY_PORT" --baud 460800 write-flash 0 /private/path/device-backup/original-flash.bin
esptool --chip esp32s3 --port "$DISPLAY_PORT" verify-flash 0 /private/path/device-backup/original-flash.bin
```

Never use `--force` or modify eFuses for this workflow. Keep backups out of commits,
PRs, build artifacts, and releases. See the official
[esptool command reference](https://docs.espressif.com/projects/esptool/en/latest/esp32s3/esptool/basic-commands.html).

## USB protocol 1

USB Serial/JTAG carries one JSON request per line (maximum 24575 bytes excluding
newline in current firmware; legacy firmware accepts 4095). Larger frame chunks
are used only after capability negotiation. `id` is an integer 1–1,000,000, echoed
in every response. Open USB with
DTR high / RTS low to avoid resetting the chip on Linux; `device.py` handles this.
The sender must check `product: shark-display`, `protocol: 1`, and 466 × 466
dimensions before sending content. It never recognizes stock Harness framing.

| Operation | Fields / effect |
| --- | --- |
| `hello`, `status` | Product, protocol, readiness, completed frame count, imageActive, CRC32, upload offset, bootError, ambientScene, touch state |
| `state` | `state`: short string; `waiting` uses an amber ambient ring |
| `clear` | Cancel upload, discard the visible image, return to ambient |
| `ui.configure` | `enabled`: boolean; renew five-second session-control lease |
| `ui.idle` | `enabled`: boolean; select dim native ambient or wake; requires `ui.idleMode`, a live lease to enter, and a fresh completed image before wake controls rearm |
| `ui.ack` | `event`: acknowledge queued events through this ID; repeated acknowledgements are harmless |
| `frame.begin` | Raw `bytes: 434312`, `crc32`: eight hex characters; optional `uiToken` 1–2147483647. `encoding: rle565` accepts 4–434312 encoded bytes divisible by four; `encoding: zlib565` accepts 1–65536 encoded bytes |
| `frame.chunk` | Exact current `offset`, `data`: base64 encoding of at most `frameChunkBytes` (16384) bytes; older peers default to 2048 |
| `frame.end` | `seconds`: integer 1–600; verify byte count and CRC32, swap buffers, start local expiry |
| `audio.begin` | `bytes`: even integer 2–882000, `sampleRate: 22050`, `crc32`: eight hex characters; reject while playing |
| `audio.chunk` | Exact audio `offset`, `data`: base64 of at most 2048 bytes |
| `audio.end` | Verify clip and CRC32, then play; optional `onTap: true` arms it for one local tap instead |
| `audio.stop` | Cancel partial upload and mute playback immediately; does not clear the display |
| `audio.volume` | `volume`: integer 0–80; zero mutes, also applies during playback |
| `mic.configure` | `enabled`: boolean; refresh five-second heartbeat, disabling cancels active capture |
| `mic.start` | Start one explicit capture, only when enabled and speaker is idle |
| `mic.stop` | Finish capture; retain a complete clip for download |
| `mic.clear` | Cancel capture and discard buffered audio |
| `mic.read` | `captureId`, absolute even `offset`, optional even `count` (2–2048); returns `captureId`, `captureOffset`, base64 `captureData`, and `captureCrc32` |

Pixels are 466 × 466, row-major **RGB565 big-endian**. A `frameRle: true` capability
allows lossless `rle565`: each four-byte record is a big-endian 16-bit nonzero run
length followed by one big-endian RGB565 pixel. Each chunk contains complete
records; runs may cross rows. Decoded output must be exactly 434312 bytes. The
decoder rejects output overflow and incomplete frames.

`frameZlibMaxBytes: 65536` advertises `zlib565`: a zlib stream of the same raw RGB565
pixels. Its encoded input is capped at 64 KiB and the ROM decoder's output is
bounded to exactly 434312 bytes. Truncation, trailing data, incorrect decoded
size, and Adler/CRC failures are rejected without publishing the frame. Decoder
state and chunk storage live in allocated memory, not large task-stack arrays.
The host prefers zlib when it fits, otherwise RLE or raw pixels. Older firmware
continues to use its advertised capabilities. CRC32
is the standard IEEE checksum used by Python `zlib.crc32`. Every chunk is
acknowledged with the next offset. Invalid sizes, offsets, CRCs, or expiry values
are rejected without publishing the incomplete image. Never stream a continuous
video feed through this protocol; occasional scenes are its intended use.
Serial reads can split a JSON response at a timeout; the client accumulates bytes
until newline, enforcing the response size limit before decoding. It reads available
bytes in batches. Firmware uses the ROM CRC routine and wakes the renderer when a
new frame commits, avoiding the ambient-animation timer delay.

Opt-in `python tests/hardware_latency_smoke.py --port "$DISPLAY_PORT"` measures
rendering through the panel's completed frame token and checks malformed streams,
size limits, and CRC rejection. Stop the service and other USB clients first.
On the development unit, six frames measured 0.342 s initially and 0.235–0.244 s thereafter;
this excludes the user's physical gesture and host input-polling delay.

`python tests/hardware_controls_smoke.py --port "$DISPLAY_PORT"` checks that
home controls stay live during replacement uploads, different control contexts
invalidate old gestures, and 1,200 poll/configure requests complete without a
timeout. It also acknowledges any physical input received during the test.

With the companion running, `python measure_controls.py --seconds 120` captures
live input and completed-panel timing without opening USB. Make at least 20
arrow/swipe/Pause/Resume inputs, including isolated taps after idle gaps and a
quick burst. The initial goals are zero missed controls, p95 release-to-display
at most 200 ms, maximum 500 ms, and no transport errors. Fewer than 20 completed
responses cannot pass. Reports go to `artifacts/device/controls-measurement.json`.
Timing starts when firmware detects release; physical sensing delay is additional.
Rapid inputs combined into one final card are identified as coalesced. Logs and
reports contain input coordinates/timing, not recorded speech or transcripts.

Audio has separate offset/checksum fields under `audio`; image and audio uploads
may be interleaved. There is no on-device TTS model or stock Harness protocol.
The host must identify our firmware and check `audio.ready` before sending speech.

The panel uses DMA strips from internal RAM; two full frames live in PSRAM.
Front/back buffers swap only after verification, under the renderer lock. Status
remains available if panel initialization fails. The native ambient animation
does not require a connected host; custom images expire locally.
