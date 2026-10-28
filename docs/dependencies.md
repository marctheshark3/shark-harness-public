# Dependencies and provenance

Reviewed 2026-10-07 against the pinned requirement files, firmware lockfile, and
upstream package metadata/license texts linked below. This is an inventory of
direct dependencies and firmware components, not a complete audit of every
transitive package or binary bundled in their wheels.

Project code uses [Apache-2.0](../LICENSE); [NOTICE](../NOTICE) records project
attribution. External software and model weights retain their own terms.

## Host packages

The browser preview uses the Python standard library. Optional features install
the following packages into a local environment; source archives do not include
that environment.

| Package | Pin | Purpose | Upstream license/source |
| --- | --- | --- | --- |
| pyserial | 3.5 | USB serial transport | [BSD-3-Clause](https://github.com/pyserial/pyserial/blob/v3.5/LICENSE.txt) |
| Pillow | 12.3.0 | Images and on-device session UI | [MIT-CMU](https://pypi.org/project/Pillow/12.3.0/) |
| playwright | 1.63.0 | Browser checks and legacy rendering | [Apache-2.0](https://pypi.org/project/playwright/1.63.0/) |
| piper-tts | 1.8.0 | Optional local speech synthesis | [GPL-3.0-or-later](https://pypi.org/project/piper-tts/1.8.0/) |
| onnxruntime | 1.23.2 | Optional speech inference | [MIT](https://pypi.org/project/onnxruntime/1.23.2/) |
| faster-whisper | 1.2.1 | Optional local transcription | [MIT](https://pypi.org/project/faster-whisper/1.2.1/) |
| websocket-client | 1.9.0 | Authenticated T3 command transport | [Apache-2.0](https://pypi.org/project/websocket-client/1.9.0/) |

`requirements-device.txt`, `requirements-dev.txt`, `requirements-speech.txt`, and
`requirements-listen.txt` separate these feature sets. The direct versions are
pinned, but transitive dependencies are resolved at install time. Playwright's
downloaded browser, native libraries in wheels, and optional runtime dependencies
have additional notices. Review the resolved environment before distributing a
preinstalled application or container.

Piper is imported into the optional speech process. Calling it optional does not
waive its GPL obligations. Project source under Apache-2.0 is not a replacement
license for Piper or a prebundled voice runtime. Review the combined distribution
and its source/notice requirements before shipping one.

## Downloaded models

Models are setup downloads, not tracked source or release payloads.

| Model | Provenance | Verification and terms |
| --- | --- | --- |
| `en_US-lessac-medium` | [Piper voice model card](https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/lessac/medium/MODEL_CARD) | `tests/speech_smoke.py` checks model and config SHA-256 values. The model card points to separate [Lessac dataset terms](https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html); do not infer redistribution rights from Piper's software license. |
| `Systran/faster-whisper-base.en` | [Converted model card](https://huggingface.co/Systran/faster-whisper-base.en), which declares MIT | `setup_stt.py` pins revision `3d3d5dee26484f91867d81cb899cfcf72b96be6c`. Review upstream model terms before redistributing weights. |

Piper downloads use the upstream voice name; the smoke check rejects changed
model/config bytes. Whisper downloads select a fixed repository revision.
Neither mechanism makes a legal or safety assessment of the models.

The Lessac model card's linked dataset agreement restricts the materials to
research and excludes commercial use; it also limits redistribution. The model
card does not establish broad commercial or redistribution permissions for the
derived voice weights. Resolve those permissions, or select a voice with
explicit suitable terms, before shipping a voice-enabled product. This is an
outstanding provenance question separate from the project's Apache-2.0 license.

## Firmware components

The manifest is [firmware/main/idf_component.yml](../firmware/main/idf_component.yml).
[firmware/dependencies.lock](../firmware/dependencies.lock) records resolved
versions and managed-component content hashes. The ESP-IDF container image is
pinned by digest in [firmware/build.sh](../firmware/build.sh).

| Component | Version | Upstream license/source |
| --- | --- | --- |
| ESP-IDF | 5.5.1 | [Apache-2.0 and component-specific terms](https://github.com/espressif/esp-idf/blob/v5.5.1/LICENSE) |
| `espressif/esp_lcd_co5300` | 2.2.0 | [Apache-2.0](https://components.espressif.com/components/espressif/esp_lcd_co5300/versions/2.2.0/license) |
| `78/esp_lcd_touch_cst92xx` | 0.1.0 | [MIT](https://components.espressif.com/components/78/esp_lcd_touch_cst92xx/versions/0.1.0/license) |
| `espressif/esp_codec_dev` | 1.6.1 | [Apache-2.0](https://components.espressif.com/components/espressif/esp_codec_dev/versions/1.6.1/license) |
| `espressif/esp_lcd_touch` | 1.2.1 | [Apache-2.0](https://components.espressif.com/components/espressif/esp_lcd_touch/versions/1.2.1/license) |
| `espressif/cmake_utilities` | 0.5.3 | [Apache-2.0](https://components.espressif.com/components/espressif/cmake_utilities/versions/0.5.3/license) |

Firmware releases collect standalone license, copyright, copying, and notice
files from the pinned ESP-IDF installation and downloaded managed components.
The collected tree preserves upstream paths; its manifest records file hashes
and build provenance. It deliberately includes notices beyond the components
linked into the final image. Each managed component must supply a notice file or
packaging fails. Review the release tree and any source-only copyright headers
when adding a component; automated collection is not a complete legal inventory.

## Distribution boundaries

The source archive is made from tracked Git content, including project license,
notices, documentation, and repository agent instructions. It excludes local
databases, credentials, models, environments, generated screenshots, and private
flash backups because these are not tracked. Review the archive, not only the
ignore file, before publishing.

The firmware archive contains compiled images, flash arguments, project license
and notice files, the dependency manifest/lockfile, collected upstream notices,
and checksums. It contains no voice runtime, voice weights, stock vendor firmware,
or private device backup. T3 is an external application accessed through its
local state and authenticated command interface; it is not bundled. The optional
client integration patches a separately installed compatible T3 build and does
not include that application's bundle.
