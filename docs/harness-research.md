# What we borrowed, and what we built

Research checked 2026-10-28 against primary source repositories. The user's
reference to “Autonomous Labs” resolves here to **Autonomous's OpenHarness**,
the upstream already cited in our hardware notes. Its former
`autonomous-ai/autonomous-harness` location redirects to
[`autonomous-ai/openharness`](https://github.com/autonomous-ai/openharness).
The license names Autonomous, Inc. This identifies the reference; it does not
establish affiliation or that today's upstream matches the original installed
device binary.

## Start with the companion's job

Someone looking at the round screen needs to know which session their voice
will reach, whether that session is working, and whether it needs attention.
A character can make those states easy to recognize. It cannot establish task
correctness, infer percentage complete, authorize an approval, or choose a new
destination. Those boundaries matter more than an elaborate animation catalog.

We kept adapters, normalized state, presentation, and prompt submission separate.
The existing authenticated T3 adapter remains the only route for prompts.
Characters receive a state and bounded artwork; they receive no credentials,
commands, transcript, or microphone control.

## Autonomous / OpenHarness

Examined revision:
[`55c55b747e242f82dffce1be8e7f48efe09aa21e`](https://github.com/autonomous-ai/openharness/commit/55c55b747e242f82dffce1be8e7f48efe09aa21e),
2026-10-28. Its [root license is MIT](https://github.com/autonomous-ai/openharness/blob/55c55b747e242f82dffce1be8e7f48efe09aa21e/LICENSE).

The physical device's [Focus implementation](https://github.com/autonomous-ai/openharness/blob/55c55b747e242f82dffce1be8e7f48efe09aa21e/devices/harness-device/firmware/main/ui/habitat/focus.c)
uses a cooking character during work, with listening, sending, resting, and
question scenes. Its [motion implementation](https://github.com/autonomous-ai/openharness/blob/55c55b747e242f82dffce1be8e7f48efe09aa21e/devices/harness-device/firmware/main/ui/habitat/character_motion.c)
separates reactions from artwork and limits repeated activity reactions. The
[character registry](https://github.com/autonomous-ai/openharness/blob/55c55b747e242f82dffce1be8e7f48efe09aa21e/devices/harness-device/firmware/main/ui/habitat/character.c)
is another useful separation of character identity from application behavior.

We adopted the concept of recognizable activity and attention states, with
voice taking priority. Our first implementation renders original vector-like
contours in Pillow and sends bounded compressed frames at two frames per second.
That works through our existing firmware protocol and avoids requiring a flash
to try new artwork. Upstream's native sprite implementation is a useful future
performance reference, not code incorporated in this change.

OpenHarness's [custom-pet design](https://github.com/autonomous-ai/openharness/blob/55c55b747e242f82dffce1be8e7f48efe09aa21e/docs/superpowers/specs/2026-10-08-custom-pet-design.md)
also suggests capability negotiation and host-side asset conversion. We stopped
short of a firmware asset loader: data-only local packs and an offline preview
are a smaller contributor boundary that can be checked now.

We did **not** copy its character art. The
[Fo asset notes](https://github.com/autonomous-ai/openharness/blob/55c55b747e242f82dffce1be8e7f48efe09aa21e/devices/harness-device/firmware/assets/pets/fo/README.md)
require permission beyond a demo; [Muse's notes](https://github.com/autonomous-ai/openharness/blob/55c55b747e242f82dffce1be8e7f48efe09aa21e/devices/harness-device/firmware/assets/pets/muse/README.md)
describe mascot-image provenance. A root software license does not by itself
resolve those asset permissions. Reef, Mochi, and Dumpling are original project
contours, rather than adaptations of those mascots.

Its extracted [companions package](https://github.com/autonomous-ai/openharness/blob/55c55b747e242f82dffce1be8e7f48efe09aa21e/companions/README.md)
describes disconnected integrations. We therefore do not present persistent
companion memory as an established upstream feature to reproduce.

## OpenCode

Examined revision:
[`b94a30547f7f677b9b9a7e36d7c66ca1ac14787d`](https://github.com/anomalyco/opencode/commit/b94a30547f7f677b9b9a7e36d7c66ca1ac14787d),
2026-10-28; [MIT license](https://github.com/anomalyco/opencode/blob/b94a30547f7f677b9b9a7e36d7c66ca1ac14787d/LICENSE).

The [session-status schema](https://github.com/anomalyco/opencode/blob/b94a30547f7f677b9b9a7e36d7c66ca1ac14787d/packages/schema/src/session-status-event.ts)
distinguishes busy, idle, and retry, including retry timing. The
[event-stream contract](https://github.com/anomalyco/opencode/blob/b94a30547f7f677b9b9a7e36d7c66ca1ac14787d/packages/protocol/src/groups/event.ts)
warns that disconnected consumers miss events. Our inference: reconnect should
read a fresh snapshot, and unavailable telemetry should be explicit. We use
the existing T3 snapshot rather than infer state from a stream of decorative
events. We did not add retry because the current T3 adapter does not expose
reliable retry timing. No OpenCode code is incorporated.

## Pi

Examined revision:
[`88619669e2fc3efaf2697742608d0fe500ddba9d`](https://github.com/pi0/pi-mono/commit/88619669e2fc3efaf2697742608d0fe500ddba9d),
2026-05-06; [MIT license](https://github.com/pi0/pi-mono/blob/88619669e2fc3efaf2697742608d0fe500ddba9d/LICENSE).

Pi's [extension lifecycle](https://github.com/pi0/pi-mono/blob/88619669e2fc3efaf2697742608d0fe500ddba9d/packages/coding-agent/docs/extensions.md)
distinguishes an agent run from individual model/tool turns and supplies cleanup
events. Its [package documentation](https://github.com/pi0/pi-mono/blob/88619669e2fc3efaf2697742608d0fe500ddba9d/packages/coding-agent/docs/packages.md)
describes version pinning and the full system access of extensions. Our inference:
character authors should not need executable plugins, and a completed turn
should not be labeled as proof that an entire task succeeded. No Pi code is
incorporated.

## Existing project provenance and release boundary

The original Shark firmware entered project history in `0e848a5c` on
2026-10-04. It uses the independent `shark-display` newline-JSON protocol,
version 1. The upstream supplied hardware references; Espressif and component
drivers supply actual dependencies. A comparison of the baseline's 22 tracked
firmware blob hashes against the pinned OpenHarness tree found no identical
files. That supports the documented independent implementation; it does not
prove no fragment was ever adapted. See the [hardware record](device-integration.md)
and [dependency inventory](dependencies.md).

The public snapshot reconciles existing project UI work with the working host
runtime. The contributor demo, simulator, native idle/input engine, and release
notice collector reuse that project work. The source includes the desktop and
normalized v2 database repairs needed by the current integration.

The public release uses Apache-2.0 for
original project source and art. Dependencies and model weights keep their own
terms. Source and firmware archives exclude optional speech runtimes, voices,
recordings, T3 data, and vendor flash backups. Piper's GPL terms and the Lessac
voice's unresolved redistribution/commercial permissions remain explicit in
the dependency inventory. This preparation does not publish a prebundled voice
product. This public repository starts with a clean source import. The original private
history is excluded. [Release verification](release-readiness.md) records what
was checked and the remaining distribution boundaries.
