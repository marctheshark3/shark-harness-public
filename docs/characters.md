# Companion characters

Reef shark, Mochi manta, and Dumpling octopus are original code-drawn sea
creatures for the physical 466 × 466 display. Rounded juvenile silhouettes,
fins, and a small current beneath them follow the existing circular instrument
and orange branding. Controls retain layout version 2's exact touch regions.

The character belongs to the **selected voice destination**. Other busy sessions
cannot silently change that destination or its expression. Its state label is
authoritative; motion is decorative:

| Observed state | Label | Meaning |
| --- | --- | --- |
| working | Cooking | T3 reports running or queued work |
| waiting | Needs you | T3 reports a question or approval request |
| error | Check T3 | T3 reports an error; inspect it there |
| done | Turn finished | A recent turn completed; whole-task success is not implied |
| idle | At rest | No current activity; success is not implied |
| unknown | State unavailable | This destination has no verified local telemetry |
| offline | Offline | T3 is unavailable; the controller shows its reconnect screen |

BOOT-selected browser routes retain their authenticated routing ID. They may
borrow local state only after matching the server's authenticated environment
ID, native thread ID, and project ID. Remote environments without telemetry
show **State unavailable**, even when voice routing works. A failed telemetry
lookup does not change or disable a voice destination.

## Preview without hardware

Install `requirements-device.txt`, then:

```bash
.venv/bin/python character_preview.py
```

Open `artifacts/characters/index.html` directly in a browser. It compares all
characters and states using actual production-renderer frames and synthetic
session text. The motion toggle honors reduced-motion preferences. The gallery
does not read T3, contact a server, access USB, or record audio.
`metrics.json` records host rendering time and compressed frame sizes.

The [interaction simulator](simulator.md) exercises the same controller, native
input engine, and rendering with synthetic voice. It verifies routing and wake
behavior; neither preview proves physical touch response or USB performance.

## Select a character

For a manual companion, set a bundled ID or an explicit local JSON file:

```bash
SHARK_CHARACTER=manta .venv/bin/python device.py --port "$DISPLAY_PORT" watch --device-ui
SHARK_CHARACTER=/path/to/my-character.json .venv/bin/python device.py --port "$DISPLAY_PORT" watch --device-ui
```

Use `SHARK_CHARACTER=none` for the text-first interface or
`SHARK_CHARACTER_MOTION=off` for a still character. These settings also work in
a systemd user-service drop-in; preserve existing local settings and restart
the companion deliberately after validating the candidate. An invalid or missing
custom pack falls back to Reef shark and leaves the controller usable.

Characters animate on the host at **at most two frames per second**, through
the existing compressed RGB565 frame protocol. They need the host companion;
they do not animate autonomously after USB disconnects. No new firmware command
or flash is required. The native firmware continues to own the voice waveform,
idle animation, and touch/BOOT behavior. Its five-second host lease clears an
abandoned control image on compatible firmware.

Motion pauses while touching, recording, waiting for a recording request, or
waiting for the previous frame to reach the panel. Decoration preserves touch
tokens and cannot reset inactivity. After the existing 60-second idle timeout,
the display dims to its native ambient scene. The first touch or BOOT wakes it
without acting; the next interaction acts. `SHARK_IDLE_SECONDS=0` explicitly
disables idle when an always-visible character is wanted.

## Contribute artwork

Copy [reef.json](../characters/reef.json), change its ID and attribution, and
preview it before opening a PR:

```bash
.venv/bin/python character_preview.py --character /path/to/my-character.json
.venv/bin/python -m unittest discover -s tests -p 'test_character*.py' -v
.venv/bin/python tests/browser_characters_smoke.py
```

Packs contain version `1`, a lowercase ID, display name, author, license,
palette, shapes, two eye anchors, and a mouth anchor. Every coordinate is in a
100 × 100 drawing space. Blobs use 3–32 contour points joined into a smooth,
closed curve. Ellipses use `[left, top, right, bottom]`. Shapes paint in order;
their fills name palette entries. The renderer owns expressions, steam,
attention markers, state labels, timing, and all controls.

The validator accepts at most 32 KiB, 24 shapes, and 12 six-digit hex colors.
Coordinates must be finite and within 0–100; the private sprite canvas clips
contours before compositing. There are no scripts, SVG imports, image URLs,
external assets, file references, or per-pack state/action hooks. New behaviors
require a reviewed renderer change rather than executable character plugins.

Submit original work you can license. Accepted metadata identifiers are
Apache-2.0, MIT, or CC0-1.0; include the applicable license text and provenance
with a new contribution. The identifier alone does not prove permission. The
bundled packs are original project artwork under the Apache-2.0
project license. Do not import upstream mascots or commercial characters merely
because a surrounding repository is open source.

## Deliberate first-release limits

This is a bounded host-rendered implementation. It adds no firmware asset
loader, downloaded plugin system, persistent companion memory, inferred progress
percentage, autonomous approval, or automatic retry. The next useful boundary
would be authenticated remote state telemetry or a native renderer with measured
USB/CPU savings; either needs its own observable behavior and compatibility
contract. [Research and source evidence](harness-research.md) explain those choices.
