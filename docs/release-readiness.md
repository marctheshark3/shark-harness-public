# Release verification

The first public source snapshot was approved on 2026-10-28 under Apache-2.0
for original project code and artwork. This repository starts with a clean
source import. Private Git history, credentials, machine-specific configuration,
T3 data, recordings, downloaded models, installed applications, and flash
backups are excluded. [NOTICE](../NOTICE) preserves project attribution;
external dependencies retain their own terms.

## Verified source snapshot

- 251 Python tests passed on Python 3.10 and 3.14, with 8 Node regressions and
  seven native C suites.
- Chromium checks passed for the general interface, hardware-free demo,
  browser voice routing, native interaction simulator, and character gallery.
  The gallery covers three characters, seven states, keyboard controls,
  motion/reduced motion, 320/360/728px layouts, circular aspect ratio, and
  untrusted attribution treated as text.
- Actual local Piper synthesis, PCM/duration/USB encoding, and Whisper checks
  for speech, a long pause, silence, and quiet noise passed without hardware
  or a real prompt submission.
- The ESP32-S3 build passed. Its release tree contained 67 checksummed files,
  including 55 collected upstream notices.
- Compilation, JavaScript syntax, workflow lint, local documentation links,
  privacy-ignore checks, and whitespace checks passed.
- All 168 bundled character/state/pose frames were rendered and compressed.
  The largest measured payload was 27,829 bytes against the firmware's
  65,536-byte compressed-frame limit. Motion is capped at two decorative
  updates per second; this does not establish physical USB latency.
- Independent reviews found and closed metadata updates postponing sleep,
  an older branding asset, and recursive gallery template substitution.
  Behavioral failures were reproduced before the fixes.

The source was scanned with checksum-verified Gitleaks 8.30.1. No findings were
reported in the publication tree. A separate check excluded known development
machine/relay labels and device-specific defaults. These checks are evidence,
not proof of an absence of every private value.

## Device and integration verification

A versioned host candidate retained the deployed runtime's files and local
settings and passed 16 character/telemetry and 50 controller checks. USB
reconnected, the firmware acknowledged a presented control frame, and the
operator confirmed Reef shark visible with the correct session title after
retry. The prior host install remains available locally for rollback.

Visibility and selection are physically confirmed. The current change's
hold-to-talk/audio quality was not re-tested by sending a real prompt; regression
checks and the prior working-device confirmation are the evidence for that path.
Firmware was built without flashing a device.

The source includes a client-helper repair for app-created thread IDs. URL
components decode exactly once before matching raw native routes; bounded
colon/percent IDs retain their identity. Tests cover encoded pathname/hash
routes, pinned destinations, acknowledgements, and no duplicate sends. The
optional desktop integration remains exact-build-specific and needs deliberate
installation into a separately installed T3 application. A host update does not
automatically replace that application's bundle.

## Contributor and distribution boundaries

Use [CONTRIBUTING.md](../CONTRIBUTING.md) for the package-free first contribution,
full test commands, and the offline character/simulator workflows. Current
hosted results are in this repository's [Actions](https://github.com/marctheshark3/shark-harness-public/actions).
The first public workflow is validated separately from the local results above;
no hosted pass is implied by this document.

Source archives use tracked Git content. Firmware archives include binaries,
flash arguments, project license/notice, dependency metadata, collected upstream
notices, and checksums. The existing `v*` tag workflow publishes those archives
after its required jobs pass; an ordinary source push does not publish a tagged
binary release.

Piper is an optional GPL dependency. The Lessac voice's model-level commercial
and redistribution permissions remain unresolved. This source/firmware release
contains neither that voice runtime nor weights and is not a prebundled voice
product. Review the sourced [dependency inventory](dependencies.md) before
proposing a different distribution or adding external assets.

Report vulnerabilities through [SECURITY.md](../SECURITY.md). Use synthetic
sessions and sanitized diagnostics in public issues and screenshots.
