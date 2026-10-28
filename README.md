<img src="assets/icon.svg" width="64" height="64" alt="">

# shark-harness

A round canvas for [T3 Code](https://t3.codes): custom images, charts, messages,
and ambient animations, with a small T3 status indicator at the edge.

The bridge, browser renderer, USB sender, and ESP32 firmware run independently of
the Autonomous Harness runtime. The firmware uses ESP-IDF and Espressif's CO5300
panel driver. Optional local speech runs through the ES8311 speaker, with an
animated waveform, volume/mute, and tap-to-stop.
The device has its own session controls: swipe or tap an arrow to select a T3
session, hold the glass to talk, and release to transcribe locally and send.
The preview is optional. A background companion starts at login and reconnects
when USB becomes available.

The selected session has an original animated sea creature: **Reef shark**,
**Mochi manta**, or **Dumpling octopus**. Cooking means work is running;
attention and error states have explicit labels. Characters preserve the session
title, voice controls, and idle wake guard. See the [character guide](docs/characters.md)
for the offline preview, custom data-only artwork, and motion settings. The
[research notes](docs/harness-research.md) identify the original Autonomous
OpenHarness reference and distinguish its concepts from copied code or art.

## Start without T3 or hardware

Use **Python 3.10 or newer**:

```bash
git clone https://github.com/marctheshark3/shark-harness-public.git
cd shark-harness-public
python3 bridge.py --demo --port 8875
```

Open <http://127.0.0.1:8875>. Demo mode labels its sample sessions and disables
voice actions. It uses only Python's standard library: no packages, downloads, T3,
credentials, microphone, or display are needed. It cannot submit prompts to T3.
Stop with Ctrl+C. Port 8875 keeps development separate from the normal companion
on port 8874; use `--port 8876` if it is busy.

For a terminal-only check, run `python3 bridge.py --demo --snapshot`.
While the demo is running, another terminal can send it a message with
`python3 bridge.py --port 8875 scene "A first contribution"`.
See [CONTRIBUTING.md](CONTRIBUTING.md) for a first contribution and fuller checks.

To try the actual round-device interaction without hardware, install
`requirements-device.txt` and run `.venv/bin/python -m simulator --port 8876`.
It shares production controls and rendering with a synthetic T3 adapter. To
compare the character artwork and states, run `.venv/bin/python character_preview.py`
and open `artifacts/characters/index.html`. See the [simulator guide](docs/simulator.md)
for its limits and keyboard controls.

## Use a connected display

After the [optional device setup](#connect-the-circular-display), the companion
provides these controls. An installed Linux companion uses
`shark-companion.service`; `t3.json` provides **Device: start**, **Device: status**,
and **Device: stop** in T3's project Run menu.

- Choose a project with the edge arrows or swipes, then tap its name or **Open sessions**.
  The chooser lists every non-deleted project registered in this T3 installation,
  including projects with no active sessions. Its counter and folder path help
  distinguish projects with similar names.
- Browse sessions within that project. Tap the curved project name at the top to switch
  projects; each project remembers its last selected session.
- Hold the center until the pink circular waveform appears, speak,
  then release. Arrow and button presses do not start voice capture.
  The ring responds to the microphone level. The words go straight to that session;
  no confirmation or preview is required.
- Tap **New session** in the project chooser or on a session screen to prepare a
  session in that project. Its first
  spoken prompt creates it and supplies its title. **Cancel new** returns without
  creating anything. Swiping a draft does not cancel it. Project model/environment
  defaults apply, retaining the source session's permission and interaction modes.
  Without an active session, the project can use archived session defaults; a
  completely empty project needs a default model set in T3 and starts with approval
  required. Errors stay on the device and explain what needs to be changed.
- Tap **Pause** to disable voice input and **Resume** to enable it again.
- Short-press **BOOT** (GPIO0) to select the conversation open in your focused T3
  window, including a conversation in a remote environment. If several windows
  are active, use the device arrows and **Use session** to choose one. Once its
  title appears, hold the **center of the touchscreen**
  until the listening ring appears, speak, and release. BOOT itself is a selection
  shortcut; holding the side button does not record. Press BOOT again after switching
  conversations. Existing sessions and new project drafts work across T3 environments;
  the device shows the selected machine and project. This requires the
  [current-session helper](docs/physical-controls.md#implemented-shortcut-use-the-open-t3-conversation)
  matching your T3 client build. After installing or updating it, restart the
  desktop app or refresh the web page. Connection errors offer
  **Retry** on the device; recording is enabled after a destination is selected.
  The [desktop installer](docs/physical-controls.md#current-desktop-client)
  checks the supported build and keeps an exact backup.
- Tap a custom visual to return to sessions. Without the companion, taps cycle
  the native ambient scenes instead.

The selected session ID and the last session per project are remembered locally. Audio and transcripts are not saved
by the companion. A restart discards unfinished recordings; it never resends them.

Hold-to-talk sends to the session **shown on the round device**. The destination
is fixed when recording starts, even if you later navigate elsewhere in T3.
The companion does not infer the computer's visible tab from background activity.

To measure live controls while the companion runs:
`python3 measure_controls.py --seconds 120`. Make at least 20 arrow/swipe/button
inputs, including idle gaps and a quick burst. The local report records response
times, ignored touches, and dropped inputs against the targets described in the
[firmware guide](firmware/README.md). It does not capture speech or transcripts.

## Read your local T3 sessions

```bash
python3 bridge.py
```

Open <http://127.0.0.1:8874> on the same computer. This also needs only Python's
standard library. If a companion already owns that port, use
`python3 bridge.py --port 8876` and open <http://127.0.0.1:8876>.
For a browser on another computer, forward the loopback port with SSH; the bridge
binds to `127.0.0.1` and checks Host and Origin headers.

The bridge reads `~/.t3/userdata/statev2.sqlite` in read-only mode, falling back to
`state.sqlite` for legacy installations. `T3CODE_HOME` selects another T3 data
directory. Use `--database PATH` or `--port NUMBER` before the subcommand to
override. State and style buttons affect that browser only; thread cards show the
selected database. The API's `deviceConnected: null` means this process does not
monitor USB.

## Send visuals from T3

With the bridge running, a T3 agent can execute:

```bash
python3 bridge.py scene "Ready for a look." --detail "Your preview is ready" --seconds 30
python3 bridge.py scene "The new design" --image /path/to/design.png --seconds 60
python3 bridge.py scene "Build time" --values 92,88,74,63,45,38 --detail "Seconds per run"
python3 bridge.py clear
python3 bridge.py --snapshot
```

Scenes appear in the preview and, when the USB sender below is running, on the
device. Images must be PNG, JPEG, or WebP under 256 KiB; 466 × 466 is ideal. Charts
accept 2–48 caller-supplied values. Content expires after 20 seconds by default,
up to 600 seconds. The equivalent API is `POST /api/scene`; `GET /api/status`
reports live state and the current visual; `DELETE /api/scene` clears it.
Content stays local and is not persisted by the bridge.

## Connect the circular display

The device must already have [our firmware](firmware/README.md). Stop any other
process owning its serial port first. To install the optional host dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-device.txt
```

Use the device's stable path from `ls /dev/serial/by-id/`:

```bash
DISPLAY_PORT=/dev/serial/by-id/YOUR_DEVICE
.venv/bin/python device.py --port "$DISPLAY_PORT" status
```

The legacy `watch` mode additionally needs Playwright and Chromium:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python device.py --port "$DISPLAY_PORT" watch
```

It opens a headless copy of the canvas and transfers each new scene once.
Images, charts, and messages share the browser design; they are static snapshots
on hardware. The device runs its own ambient animation between scenes, including
when the host is absent. It also expires images locally. Browser animation frame
rates are not promised on the ESP32. The sender reports acknowledged checksums,
checks USB health, and exits on disconnection. For automatic reconnection, session
controls, and no Chromium dependency, install the companion service below.

For a direct image without a browser, the device dependencies above are sufficient:

```bash
.venv/bin/python device.py --port "$DISPLAY_PORT" send /path/to/image.png --seconds 30
.venv/bin/python device.py --port "$DISPLAY_PORT" clear
```

Only one USB client can run at a time. Run `python3 companion.py stop` and stop any
manual `watch` before direct USB commands or flashing. Restart with
`python3 companion.py start`. `BROWSER_EXECUTABLE` selects Chromium for legacy mode.

## Use the touchscreen

Without the companion interface, a short tap and release cycles the native
**aurora → orbit → ember** backgrounds.
Three small dots indicate the selected scene, and a ripple acknowledges the tap.
While a T3 visual is showing, a tap dismisses it back to ambient. Dismissal is
local to the device; the browser preview retains its scene until it expires or
is cleared. These interactions work without the USB sender running.

Holding, dragging, or using multiple fingers does not repeatedly change scenes.
With the companion and current-session helper, short-pressing BOOT selects the
open T3 conversation. Touch Pause/Resume controls voice input.
The other physical button has not been remapped. Scene selection lasts until the
device restarts. `device.py status` includes `ambientScene` and touch readiness,
last coordinates, accepted tap count, and driver error for troubleshooting.

## Speak from T3

Install the optional local voice runtime in the same environment as `device.py`
(tested with Python 3.10 and CI Python 3.12), then download the default voice:

```bash
.venv/bin/pip install -r requirements-speech.txt
.venv/bin/python -m piper.download_voices --download-dir "$HOME/.local/share/shark-harness/voices" en_US-lessac-medium
```

Run `bridge.py` and `device.py ... watch` as above with the current firmware.
T3 can then speak without opening a second USB connection:

```bash
python3 bridge.py say "Your preview is ready." --wait
python3 bridge.py say "T three is ready." --on-tap  # Wait for one short tap
python3 bridge.py volume 60
python3 bridge.py volume 0   # Mute
python3 bridge.py stop
python3 bridge.py speech-status
```

The local preview also has **Speak**, **Stop**, and volume controls. A short tap
stops an active clip or upload before applying any scene action. While speech is
playing, a mint circular waveform appears around the native background or custom image.
A second tap after speech stops can dismiss the image or change the background.
The sender also cancels speech if you tap during synthesis.

Speech is explicit: observing a T3 thread never reads its messages aloud.
[Piper](https://github.com/OHF-Voice/piper1-gpl) synthesizes on the host computer; text
and audio are not sent to a voice service or saved by the bridge. Piper is an
optional GPL-3.0 host dependency. Voice weights are downloaded separately, are
not included in releases, and have their own
[model card](https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/lessac/medium/MODEL_CARD).
`watch --voice /path/to/model.onnx` selects another 22050 Hz Piper model with its
adjacent `.onnx.json` config. There is no network access during synthesis.

Messages accept 1–400 characters and at most **20 seconds** of generated speech.
The entire clip is checked before playback, so allow a few seconds for synthesis
and USB upload. A newer message interrupts the old one; Stop also cancels synthesis
and upload. Volume is 0–80, initially 60, and applies during playback. These
settings and requests are transient. Pending requests expire after 90 seconds;
claimed requests are never replayed on a sender restart. The sender must be
running; `--wait` reports completion, cancellation, or errors.

`--on-tap` generates and uploads the clip first, and returns when the device is
armed. The next short touchscreen tap starts the preloaded clip locally, once,
without waiting for host generation/upload. Armed clips wait for up to an hour;
Stop or a new message replaces them. That first tap leaves the current visual
in place; later taps stop speech. The sender must be running to prepare the clip
and report progress.

The loopback API is `POST /api/speech {"text":"..."}`, optionally with `volume`
and `onTap: true`;
`DELETE /api/speech` stops, `POST /api/speech/volume {"volume":60}` adjusts volume,
and `GET /api/speech` reports armed/queued/synthesizing/uploading/playing/done/stopped/error.
Terminal requests discard their text. The sender uses `/api/speech/claim` and
`/api/speech/progress` to claim once and report progress.

## Talk to a T3 session

Install the optional local STT runtime and pinned English model:

```bash
.venv/bin/pip install -r requirements-listen.txt
.venv/bin/python setup_stt.py
```

Give the companion its own local T3 credential using T3's auth CLI. The token is
stored outside this repository and never exposed to the browser:

```bash
umask 077
mkdir -p "$HOME/.local/share/shark-harness"
t3 auth session issue --ttl 30d --label 'Shark voice companion' --subject shark-voice --token-only > "$HOME/.local/share/shark-harness/t3-token"
```

For the independent on-device interface, stop any manually started bridge/sender
and install the service after installing the device, speech, and STT dependencies:

```bash
.venv/bin/python companion.py install --device "$DISPLAY_PORT"
.venv/bin/python companion.py status
```

This Linux user service starts at login and also follows the legacy
`t3code.service` when present; desktop T3 does not require that service.
Installation copies the runtime
to a versioned directory under `~/.local/share/shark-harness/installs/`, so closing
or removing this worktree does not stop the device. It reconnects USB automatically.
Running `install` replaces the active host runtime, so use it only for an
intentional deployment from the version you want to run. Develop against the
separate synthetic preview port when an installed runtime is newer than this
checkout. Firmware updates remain explicit and separate. Inspect logs with
`journalctl --user -u shark-companion.service`.

For a manual launch without a service or browser:

```bash
.venv/bin/python bridge.py
# In another terminal:
.venv/bin/python device.py --port "$DISPLAY_PORT" watch --device-ui
```

The device starts with a project chooser. Hold-to-talk stays disabled until you
open a project and its session screen. Hold for about one second until the ring turns
pink, speak while holding, and release. Recording continues until release, including
pauses while you gather your thoughts. The companion drains audio over USB as you
speak, so the device's buffer does not limit the recording length. This requires
the streaming microphone firmware (`mic.streaming: true`); older firmware keeps
its original 30-second limit until updated. The destination
is captured when recording starts. Release automatically transcribes and sends;
silence sends nothing. Session controls use Pillow directly. Lossless zlib/RGB565
compression, batched USB transfers, and immediate panel updates keep navigation
responsive. Standalone navigation requires firmware with the per-screen voice gate;
the sender rejects older firmware so choosing a project cannot record into a hidden destination.

Optional `--confirm-prompts` requires paginated on-device transcript review and a
destination confirmation. The legacy `watch --stt` mode instead starts unarmed;
choose a session and enable input in <http://127.0.0.1:8874>, then review/edit the
transcript and choose **Confirm & send to T3** there.

The preview also has Record/Finish controls. Short taps retain their scene and
speaker controls. Holding to talk stops the speaker before starting the microphone.
Listening and playback are half-duplex, so the device does not transcribe its own
speaker. One native pink circular waveform remains visible even over custom visuals;
the host never draws a second waveform.

[faster-whisper](https://github.com/SYSTRAN/faster-whisper) runs the pinned
`Systran/faster-whisper-base.en` model locally on CPU. Setup downloads weights;
transcription itself uses local files only. No recording is saved to disk or sent
to a transcription service. Only the resulting text goes through T3's existing model
provider, retaining the selected session's model and permission/interaction modes.
Transcripts awaiting review expire after ten minutes. A sender restart clears
device recordings, and loss of its heartbeat stops capture within five seconds.

The adapter reads legacy and current T3 projections and uses its authenticated
WebSocket command interface (`orchestration.dispatchCommand`, `thread.turn.start`).
T3's internal schema and API may change across versions. Duplicate submissions
are rejected. If a send loses its acknowledgement, check T3: it is marked uncertain
and never automatically resent. The 30-day credential can be renewed with the
command above or revoked using `t3 auth session list` / `revoke`.

The sender's `--stt-model PATH` accepts another compatible local model directory.
Bridge options `--t3-port`, `--t3-token`, and `--database` select the local T3
installation. For the legacy preview workflow, bridge options `--thread ID --stt`
explicitly arm a destination at startup.

## Development and release preparation

The circular device layout requires firmware advertising `ui.layoutVersion: 2`
and `ui.voiceRing: true`. The host checks these before enabling its controls.
The native renderer owns the animated voice ring; Pillow draws the labels,
controls, and optional character frames. To preview the ring without USB or microphone access,
run `.venv/bin/python tests/device_ui_preview.py`. This compiles the actual ring
renderer and writes a gallery and animated GIF with demo levels to
`artifacts/circular-ui/`.

Use [CONTRIBUTING.md](CONTRIBUTING.md) for setup and checks. The
[CI workflow](.github/workflows/ci.yml) defines the Python, JavaScript, native C,
browser, optional local-speech, and firmware checks. Browser checks use synthetic
T3 state; speech checks download models, and firmware builds need Docker. CI
does not access or flash a device.

The existing `v*` tag workflow packages tracked source and firmware with SHA-256
checksums, then creates a GitHub release. Original project code and artwork use
[Apache-2.0](LICENSE), with [attribution](NOTICE) and a
[dependency/model inventory](docs/dependencies.md). Release verification and distribution boundaries are recorded in
[docs/release-readiness.md](docs/release-readiness.md). Report vulnerabilities
using [SECURITY.md](SECURITY.md).

T3's database schema is internal and may need adapter updates as T3 changes.
See [hardware findings and remaining work](docs/device-integration.md) and
[firmware build, protocol, and recovery instructions](firmware/README.md).
