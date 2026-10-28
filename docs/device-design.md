# Device interface

The 466 × 466 display is a voice instrument: choose a destination, hold to speak,
and see what happens next. The interface uses a quiet charcoal surface, warm white
type, measured dial marks, and signal orange for the selected position and primary
actions. Typography carries the information; color reinforces it.

The renderer lives in [device_ui.py](../device_ui.py). It draws at twice the panel
resolution and reduces the finished frame before RGB565 conversion. Flat surfaces
keep small labels readable and USB transfers compact.

## Visual hierarchy

The upper arc identifies the project on a session screen and the pinned session
during recording, transcription, review, sending, and the result. The center gives
the session title or current voice state the most space. Secondary information uses
short, monospaced labels. The bottom crescents contain actions; their labels name
the action, including **Pause voice** and **Resume voice**.

The upper rim is a position indicator for the project/session chooser or transcript
pages. It does not imply elapsed time or processing progress. The selected session's
activity drives its character and status label; unrelated sessions do not change
the home screen's destination.

The side controls carry larger outward chevrons and explicit **PREV** and **NEXT**
labels. A left-arrow tap moves to the previous item; a right-arrow tap moves to the
next. The numbered label and orange position mark show the resulting selection.
Lists wrap at either end: previous from 01 selects the last item. Swipes move the
card itself, so swiping left reveals the next card and swiping right reveals the
previous one. The touch-coordinate transform and layout version remain unchanged.

| Token | Value | Use |
| --- | --- | --- |
| Surface | `#151617` | Neutral charcoal glass |
| Ink | `#f1f0eb` | Titles and main instructions |
| Muted | `#a8aba8` | Metadata and supporting instructions |
| Accent | `#ff5c35` | Primary actions and selected position |
| Control | `#303230` | Secondary action surfaces |
| Line | `#454745` | Dividers, labels, and dial marks |
| Recording | `#ff739a` | Native recording ring and microphone label |

The firmware owns the live circular voice ring. Its pink recording and mint
playback colors remain separate from the orange controls. Host-rendered screens
must leave that ring clear and must not draw another waveform under it.

## Characters

A small sea creature shares the home screen with the selected session's title and
activity label. Reef, Manta, and Octo use soft aquatic contours inside the dial;
the project arc, arrows, and crescent controls retain their precise input geometry.
The activity comes from the selected destination's actual telemetry. Missing
telemetry stays **State unavailable**, including remote destinations without it.

Characters are declarative JSON packs rendered by [characters.py](../characters.py).
`SHARK_CHARACTER` selects a bundled id (`reef`, `manta`, or `octo`), a local JSON
path, or `none` for the text layout. `SHARK_CHARACTER_MOTION=off` holds the artwork
still. Invalid custom packs fall back to Reef so controls remain available.

Home animation is capped at two frames per second and retains its touch token.
It does not reset the idle clock. Touching the glass, holding BOOT, pending
recording, and active microphone capture stop decorative repaints. Recording and
review use their own screens; dim idle uses the native ambient renderer. Character
artwork never initiates recording, navigation, or prompt submission.

## Voice flow

- Before recording, the home screen says **Release to send** or **Release to
  review**, matching the configured mode. A paused screen explains how to resume.
- While recording, the displayed session stays fixed. The screen repeats the
  release behavior and marks the destination as locked.
- Transcription explains whether sending or review comes next. Review keeps the
  pinned session title visible and numbers every page; long prompts remain intact.
- After review, **Continue** opens a separate destination confirmation. A distinct
  **Send** press submits the prompt. Browsing, paging, and resuming do not send it.
- **Sending** waits for acknowledgement. **Sent** means the prompt was accepted;
  it does not claim that the coding work is finished.
- A missing destination offers **Retry** and **Projects** with capture disabled.
  An unacknowledged send asks the user to inspect the session and is never retried
  automatically. Empty and interrupted recordings provide a return action.

While awake, BOOT explicitly selects the open T3 conversation. It does not continuously follow
browser navigation and cannot change a recording or reviewed prompt's destination.
Pausing affects voice input, not the agent's work. See
[physical controls](physical-controls.md) for hardware behavior and future proposals.

## Idle and wake

After 60 seconds without interaction, ordinary session, project, empty, and
disconnected screens yield to a dim, slowly moving native ambient scene. The
panel brightness drops from 25% to 6%. This is a screensaver: USB, the local
bridge connection, and touch remain active. Firmware draws the animation locally;
the host does not stream video frames or repeatedly upload the unchanged screen.

Tap the glass or press BOOT to wake. The entire waking contact is consumed,
including a long hold: it cannot record, navigate, change the destination, or
start speech. The host sends a fresh screen and control token before controls
become active again. The selected project/session and **Pause voice** preference
stay as they were. A subsequent gesture performs the normal action.

Recording, transcription, review, confirmation, sending, actionable errors,
new-session drafts, window choices, active visuals, and queued or active speech
keep the display awake. A newly arriving visual or speech request wakes it. An
explicit browser Record request first wakes and restores the screen, then starts
capture; cancelling or pausing during that transition cancels the pending start.
Routine background session polling does not restart the inactivity timer.

`device.py … watch --device-ui --idle-seconds 120` changes the delay;
`--idle-seconds 0` disables it. `SHARK_IDLE_SECONDS` supplies the same default to
the watcher, including when launched by the companion. Firmware advertises
`ui.idleMode`; older compatible firmware keeps ordinary controls working with
idle disabled. Geometry and layout version remain unchanged.

## Layout contracts

Touch coordinates are physical panel pixels. The displayed controls must stay
inside their established input regions in `DeviceUI.event` and
[`gesture.c`](../firmware/main/gesture.c):

| Control | Horizontal range | Vertical range |
| --- | --- | --- |
| Project header | 90–376 | 45–126 |
| Previous | 0–96 | 130–315 |
| Next | 370–465 | 130–315 |
| Left action | 46–212 | 327–452 |
| Right action | 255–420 | 327–452 |

The center remains the hold-to-talk surface on an enabled home screen. Control
areas, stale-frame rejection, microphone gating, and dispatch semantics are shared
behavioral contracts; a visual revision must preserve them. Keep primary copy
large, bound long labels to the available arc, and check seven-line review pages
and long destination names at the actual 466-pixel resolution.

## Preview and validation

The interactive simulator uses the real renderer, controller, and native gesture
logic with synthetic sessions and speech:

```sh
python -m simulator --port 8876
```

Open <http://127.0.0.1:8876>. Choose a scenario, tap the displayed controls, drag
across the glass, or hold its center. The labeled buttons beneath the panel offer
keyboard equivalents; focus the panel for arrow keys, Space to hold, Enter to
tap, and B for BOOT. **Skip 60 seconds** exercises idle and its consumed wake
contact. The event trace shows selection changes and simulated submissions.
**Show touch zones** overlays the established control regions.

Frames and their active controls stay paired while a new image loads. New input
includes the loaded control version; decorative character frames retain it while
navigation and review changes advance it. Releases and cancellations complete
their original contact even if the screen changes. A rejected stale action is
never automatically replayed. Reduced-motion preferences stop decorative frames.

The simulator does not connect to T3, open USB, use the microphone, or send a real
prompt. Ambient idle artwork is an explicitly labeled approximation; the native
voice ring remains generated by firmware code. A physical device is still needed
to verify the touch controller's reported coordinates and panel orientation.

Install `requirements-device.txt` and ensure a C compiler is available, then run:

```sh
python tests/device_ui_preview.py
python -m unittest discover -s tests -p 'test_device_ui.py' -v
```

The preview writes `gallery.png`, `review-flow.png`, `recovery.png`, individual
screens, and `listening.gif` under `artifacts/circular-ui/`. The animation compiles
the actual firmware ring renderer with demo audio levels. The preview never opens
USB or records audio. Generated artifacts are ignored by Git.

Inspect the PNGs for clipped arcs, competing instructions, and crowded actions.
Inspect the GIF for a single clear recording ring. These previews and UI tests
verify rendering and interaction logic; a physical panel is still needed to judge
glass contrast, finger placement, and perceived response time.
