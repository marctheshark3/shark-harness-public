# Device interaction simulator

The simulator runs the actual 466 × 466 renderer, `DeviceUI` controller,
`ListenStore`, and firmware input engine against synthetic projects and speech.
It is a separate local development tool; it does not open USB, read T3's database,
load credentials or speech models, or send a real prompt. Its temporary selection
file is isolated from the installed companion's remembered session.

## Run it

Use a source checkout with **Python 3.10+, Pillow, and a C11 compiler**. The
current native build uses the Linux/macOS compiler interface; CI targets Linux.
Native Windows compilation is not supported by this launcher.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-device.txt
.venv/bin/python -m simulator --port 8876
```

Open <http://127.0.0.1:8876> on that machine. The launcher binds only to loopback;
keep that boundary when forwarding the port. `CC` selects a different C compiler.
The shared library is built in a private temporary directory and removed when
the process exits. Docker, ESP-IDF, T3, microphone permission, and hardware are
not needed. The installed companion runtime does not include this source tool.

Use the virtual environment command above if your system Python does not have
the device dependencies.

## Try the controls

- Tap **PREV** on the left to select the previous item; tap **NEXT** on the right
  to select the next. Lists wrap at the ends. The position counter and event
  trace report the result, including a wrap.
- Swipe the card left to reveal the next item, or right for the previous item.
  This follows the motion of a card being moved aside.
- Choose **Session navigation** to see the production character animation. The
  same `SHARK_CHARACTER` and `SHARK_CHARACTER_MOTION` settings used by the device
  select its artwork and motion. Browser reduced-motion preferences freeze
  decorative frames while controls continue to work.
- Hold the center on an enabled session screen. After 800 ms, the simulator
  starts a synthetic recording and overlays the actual firmware voice ring.
  Release to transcribe the fixed sample phrase. No audio is captured.
- Choose **Review a long prompt** to page through a transcript, continue to the
  destination confirmation, and explicitly send once to the synthetic adapter.
- Use **Skip 60 seconds** for idle. The first touch or BOOT press wakes only,
  including a long hold. The next interaction operates the controls.
- Select paused, disconnected, empty, temporary-visual, or unconfirmed-send
  scenarios to check their recovery paths. Reset returns to the project library.
- Turn on **Show touch zones** to compare visible controls with their input areas.

With the glass focused, arrow keys tap the corresponding side arrow, Space holds
the center until released, Enter taps the center, and B presses BOOT. The buttons
below the glass provide keyboard-accessible equivalents. Moving focus away or
losing pointer capture cancels a held synthetic recording.

The browser clock follows real elapsed time while the page polls. **Skip 60
seconds** advances the same virtual clock used by idle, input timing, and review
expiry. Automated Python checks control it directly and do not sleep for a minute.

## What is shared with the device

| Layer | Simulator behavior |
| --- | --- |
| Raw touch calibration | Native tests feed raw CST9217 samples through the production transform: X unchanged, Y = 465 − raw Y |
| Touch, swipes, BOOT, voice gates, queue, idle | `firmware/main/input.c`, `gesture.c`, `tap.c`, and `idle.c`, compiled through `simulator/native.c` |
| Project/session selection and review controls | Production `DeviceUI`, with its real frame tokens and hit regions |
| Recording destination, review and duplicate-send rules | Production `ListenStore`, with injected deterministic clocks and a synthetic session adapter |
| Characters, images and recording ring | Production Pillow renderer, character packs and native `voice_ring.c` |
| Microphone, transcription, T3 transport | Explicitly simulated, using one fixed transcript and in-memory submissions |

New pointer/keyboard presses include the control version of the image actually
loaded by the browser. Character animation changes `imageVersion` while keeping
`frameVersion` stable, so moving artwork does not interrupt input. Navigation,
destination, and review changes advance the control version; a stale image cannot
activate a newer screen's controls. Releases and cancellations still drain an
existing contact after a frame changes. Failed actions are reported; the browser
does not automatically replay them.

The ambient preview is illustrative. This tool does **not** establish physical
brightness, colors, capacitive response, USB/DMA timing, codec quality, or real
speech recognition. Hardware tests and an operator's observations remain part
of release validation. A simulator passing its own assumed calibration does not
prove the physical wiring: known left/right taps must be measured on the unit.

## Verify changes

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_native_input.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_simulator.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_release_notices.py' -v
node --check simulator/web/app.js
.venv/bin/python tests/browser_simulator_smoke.py
```

The browser check also needs `requirements-dev.txt` and Playwright Chromium;
`BROWSER_EXECUTABLE` may select an existing Chromium binary. It saves synthetic
screenshots under `artifacts/browser/simulator/`. CI runs these checks alongside
the existing browser, host, and firmware suites.

The local HTTP interface serves a fixed set of assets, checks Host and Origin,
requires a per-process token for writes, and bounds action sizes. It has no
forwarding route to the live bridge. Keep simulator scenarios and screenshots
synthetic when extending it.
