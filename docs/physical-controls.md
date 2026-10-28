# Physical button survey

The first hardware survey was recorded on 2026-10-05 against checkout `8e2b01d`.
The current-session shortcut was implemented on 2026-10-06. The behavior below
reflects the source in this release; historical measurements do not
verify a newly built firmware image.

## Implemented shortcut: use the open T3 conversation

The companion runs on the computer connected to the USB display. A version-checked
helper in the T3 web or desktop client reports its focused conversation to that
companion. Its native T3 command callback sends through the client's existing
connection to the selected environment. The project can live in another connected
environment and need not appear in the companion host's local catalogue. Existing
conversations and project drafts are supported.

1. Install the helper matching the exact T3 client build, then refresh the web
   page or restart the desktop client.
2. Connect the environment running the companion in T3. Open and focus the
   conversation or project draft you want to use.
3. Short-press **BOOT**. One active conversation selects immediately. With several
   active windows, use the display arrows and **Use session** to choose one.
4. When the title and **Hold center to talk** appear, hold the center of the
   touchscreen for about 0.8 seconds until the listening ring appears, speak,
   and release. Release follows the configured send/review setting. The header,
   arrows, and bottom buttons cannot start a recording.

Press BOOT again after switching conversations. Selection is explicit: navigation
in T3 does not continuously change the device destination. BOOT cannot retarget
recording, transcription, review, or a send. It enables voice when a new selection
succeeds; touch **Pause/Resume** controls capture afterwards. A display resting in
its idle animation consumes the first touch or BOOT press to wake, including that
press's release; use a fresh press once the session screen returns.

Reports older than five seconds are excluded before counting active windows.
Duplicate client leases use the newest focus report. An error shows **Retry** and
**Projects** with capture disabled. Browser disconnects disable new recordings
after a 15-second polling grace period without redirecting an active recording.
After a browser reload, BOOT or Retry binds the new client connection.

The helper creates a random secret per window and publishes only its SHA-256 hash
in T3's authenticated activity reports. The relay checks that live proof before
accepting the secret; T3 credentials are never copied into the relay. Selection
captures the environment, thread, model, permission and interaction modes, and
worktree settings. A transcript travels only when the configured send action
runs. Jobs are claimed once, require acknowledgements, and are never automatically
retried after an uncertain send. Protocol-2 desktop draft acknowledgements must
name the pinned native thread, even after the client navigates elsewhere. Drafts
use T3's first-turn bootstrap and promote the same draft after acknowledgement.

### Web-client installation

The default endpoint is the same-origin `/shark-voice/api/voice-client` route.
Configure the T3 host's reverse proxy to forward that route to the companion's
`/api/voice-client`, preserving Host, Origin and Authorization. The T3 page needs a
secure context for Web Crypto; HTTPS and a local loopback origin provide that.
The helper alone does not create a proxy route.

```sh
python3 integrations/install_t3_focus.py --client /path/to/t3/client --check
python3 integrations/install_t3_focus.py --client /path/to/t3/client
# Restore the exact original bundles, then refresh the browser:
python3 integrations/install_t3_focus.py --client /path/to/t3/client --remove
```

For a companion on another computer, supply its actual HTTPS endpoint with
`--relay-url https://display.example.test/shark-voice/api/voice-client` to both
check and install. `display.example.test` is a placeholder. A local companion can
use `--relay-url http://127.0.0.1:8874/api/voice-client`. URLs cannot contain
credentials, queries, or fragments; HTTP is accepted only on loopback. The web
installer also accepts another root-relative path.

The verified web build is T3 0.0.45 with `runtime-CGAQ9uAu.js` and
`_chat-DWO1wYmt.js`. Complete original hashes and unique hook locations are checked
before either asset changes. Original bundles and `installed.json` are kept in
`shark-current-session-backup` beside the client directory, outside the served
client. The receipt records installed hashes so a later endpoint change or removal
can recognize that installation. Known older helpers are recognized by hashes;
unknown edits and mismatched backups are refused. A write failure rolls back
changed assets. Removal does not require the previous endpoint value.

### Current desktop client

The desktop client bundles its UI in `resources/app.asar`. Its helper also handles
`#/environment/thread` and `#/draft/id` routes. Keep the `integrations` directory
together: the desktop installer reuses the web installer's URL validation.

The supported build contains `runtime-BVExcDcv.js` and `_chat-BdvROAFK.js` with the
complete hashes pinned in `install_t3_desktop.py`. Version labels alone do not
establish compatibility. The installer updates ASAR offsets and integrity metadata
and keeps an exact `.shark-voice-original` backup plus a
`.shark-voice-installed.json` receipt beside the package. Unexpected changes to
the package are refused. A known previous helper is accepted only when recreating
it from the backup matches the entire package.

Desktop installation and checking require an explicit HTTPS or loopback HTTP
`--relay-url`; a same-origin path would refer to `t3code://app` rather than the
companion. For a companion on the same computer:

```sh
python3 integrations/install_t3_desktop.py --archive '/path/to/T3/resources/app.asar' --relay-url http://127.0.0.1:8874/api/voice-client --check
# Prepare a copy for inspection without changing T3 or its backup:
python3 integrations/install_t3_desktop.py --archive '/path/to/T3/resources/app.asar' --relay-url http://127.0.0.1:8874/api/voice-client --output /tmp/t3-voice.asar
# Quit T3 before an installation; package-directory write access is required:
python3 integrations/install_t3_desktop.py --archive '/path/to/T3/resources/app.asar' --relay-url http://127.0.0.1:8874/api/voice-client
# Removal uses the backup and receipt; the old relay URL is unnecessary:
python3 integrations/install_t3_desktop.py --archive '/path/to/T3/resources/app.asar' --remove
```

Restart T3 after installation. A T3 upgrade replaces its bundles and needs a new
compatibility review. An absent or incompatible helper leaves selection unarmed.

### Relay configuration

Set these variables on the companion process (or its user service). The origin
is where T3 runs; the Host value is what the proxy sends to the companion:

```sh
export SHARK_VOICE_ORIGINS=https://t3.example.test,t3code://app
export SHARK_VOICE_HOSTS=display.example.test
```

Replace the example domains with the exact origins and proxy hosts in your setup,
including non-default ports. Origins have no path or trailing slash. The defaults
allow only `http://127.0.0.1:3773` and `http://localhost:3773`; desktop access needs
`t3code://app` explicitly. The bridge's loopback Host values are always accepted
for the voice route. `SHARK_VOICE_HOSTS` adds hosts for that route only; neither
setting exposes the other companion endpoints. Invalid allowlists grant no
access. Every voice exchange still requires the live per-window proof.

These exports apply to a manually started companion. For an installed user
service, put equivalent `Environment=` entries in its systemd service override;
shell exports do not change the environment of a running service.

If using a Tailscale HTTPS service on the companion host, the optional route is:

```sh
tailscale serve --bg --https=443 --set-path=/shark-voice http://127.0.0.1:8874
# Remove only this route:
tailscale serve --https=443 --set-path=/shark-voice off
```

Use the resulting host in the helper endpoint and Host allowlist. For a different
T3 origin, configure its origin too. Multiple-model drafts require one model to
be chosen before voice selection. Helper changes need a page refresh or desktop
restart; companion environment changes apply when its process is next started.

## Current physical behavior and hardware evidence

The manufacturer's [1.75C documentation](https://docs.waveshare.com/ESP32-S3-Touch-AMOLED-1.75C)
identifies BOOT and PWR. On the
[1.75C schematic, sheet 1](https://files.waveshare.com/wiki/ESP32-S3-Touch-AMOLED-1.75C/ESP32-S3-Touch-AMOLED-1.75C-schematic.pdf),
BOOT/Key1 pulls GPIO0 low. PWR/Key2 drives AXP2101 PWRON and a transistor whose
SYS_OUT signal goes to GPIO3. The schematic indicates high when PWR is pressed;
its polarity and timing remain unmeasured on the test unit. The non-C board has
a different pin map.

| Control | Current source behavior | Unimplemented behavior |
| --- | --- | --- |
| BOOT short press | GPIO0 is polled every 20 ms. A 20–700 ms press emits gesture 5 on release. Supported navigation, result, visual, and recovery screens select the open T3 conversation. In idle, the first press only wakes. | No action during recording, transcription, review, confirmation, or sending. |
| BOOT hold | Does not start microphone capture or emit the short-press action. | Physical hold-to-talk. |
| PWR | Hardware power behavior remains independent. GPIO3 is not polled by this firmware. | Software Back/Cancel/Stop actions. |
| Touch | Project/session browsing, New/Cancel new, Pause/Resume, center hold-to-talk, review/send, visual dismissal, speech start/stop. In idle, the wake contact is consumed through release. | No on-device cancellation during recording or transcription. |

At the original survey, the service reported the display connected and
`device.ui.buttonReady: true`; the current-boot journal contained no gesture-5
inputs. Readiness proved initialization, not a physical press. At that time BOOT
only toggled Pause/Resume on the session screen. That behavior was replaced by
the current-session shortcut. GPIO0 polling still depends on successful touch
initialization and a live host UI lease. Standalone ambient mode has no BOOT
selection action.

A subsequent failure produced HTTP 409 during BOOT lookup and returned to an
unarmed project picker, with healthy microphone/touch diagnostics. The fix added
fresh-window selection, Retry/Projects recovery, and explicit capture gating.
`tests/browser_voice_smoke.py` now exercises the real helper, HTTP relay, device
UI and capture pump with fake hardware/dispatch: two windows, a stale lease,
remote draft, navigation during transcription, the pinned protocol-2 acknowledgement,
one send across repeated polls, disconnect, and recovery. It does not verify a
physical finger or switch press.

The investigated T3 0.0.45 client uses `/$environmentId/$threadId` for an existing
conversation. Its unmodified `server.getBackgroundPolicy` leases carried
workspace/provider demand, without the selected thread. Neither those scopes nor
recent database activity establish the open conversation. `t3 app` opens a
project/new thread rather than exposing the active conversation; see the
[T3 CLI documentation](https://github.com/pingdotgg/t3code/blob/main/docs/user/install.md#open-a-project-from-a-terminal).

## Proposed physical-button extensions

The current implementation uses BOOT to select and the glass to record. The
following mapping remains a design proposal, pending GPIO3 measurements and
firmware/UI work:

| Context | BOOT short press | BOOT hold | PWR short press |
| --- | --- | --- | --- |
| Project chooser | Select the open T3 conversation | No recording | No action at the top level |
| Session or local draft | Select the open T3 conversation | Record to the displayed destination if enabled | Back to projects, or cancel a local draft |
| Recording/transcription | No extra action | Only the original capture input controls release | Discard the unsent prompt |
| Transcript review | Next page, then destination confirmation | No recording | Discard prompt |
| Destination confirmation | Send the displayed prompt with a separate press | No recording | Back to review |
| Result/error or visual | Select the open T3 conversation | No recording | Dismiss |

Speaker activity would take priority: PWR would cancel queued synthesis, upload,
armed speech, or playback before a later press navigates. BOOT hold-to-talk would
need the same capture gate and destination binding as touch, including Pause.
Preserve BOOT's startup recovery and PWR's hardware power behavior; no application
hold gesture is assigned to PWR. Power-off timing needs physical verification.

Existing boundaries support these extensions: `DeviceUI.event` handles shared UI
actions, `DELETE /api/listen` discards an unsent prompt, and `ListenPump` clears the
microphone. `mic.stop` finishes capture and may send automatically, so it is not a
cancel operation. `/api/speech` and the existing speech stop path can cancel host
work as well as device playback. There is no T3 turn-interrupt or approval-response
button command, and a claimed send cannot be undone.

Physical hold-to-talk needs independent press/hold/release classification and
capture-source tracking in [audio.c](../firmware/main/audio.c), with stale-frame,
lease-expiry, Pause, disconnect, and release tests. GPIO3 needs measured polarity
before software actions are enabled. Use `measure_controls.py` through the running
companion for button timing, then test cold boot, recovery, unplug/replug and
power behavior on hardware. USB and simulator tests do not substitute for actual
switch measurements.
