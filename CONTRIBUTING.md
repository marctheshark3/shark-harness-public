# Contributing

Start from the repository root with Python 3.10 or newer. A first documentation,
browser, or bridge contribution does not need a display, microphone, T3 account,
or model weights. The [synthetic preview](README.md#start-without-t3-or-hardware)
and these checks work offline after you have Python and the checkout:

```bash
python3 bridge.py --demo --port 8875
# Stop with Ctrl+C, then:
python3 -S bridge.py --demo --snapshot
python3 -S -m unittest discover -s tests -p test_bridge.py -v
python3 -S -m unittest discover -s tests -p test_demo.py -k DemoCLITests -v
git diff --check
```

The `-S` checks deliberately exclude installed third-party packages. The explicit
preview port keeps it separate from an installed companion on port 8874. Demo
mode supplies synthetic state and leaves T3 credentials and databases unread.

## Make your first contribution

1. Fork [shark-harness-public](https://github.com/marctheshark3/shark-harness-public/fork)
   and clone your fork. Create a branch for the change.
2. Run the synthetic preview, make a focused change, and run the relevant checks
   below. Documentation and character artwork are useful first contributions.
3. Open a pull request against `main` in `marctheshark3/shark-harness-public`.
   The PR template asks for the resulting behavior, validation, and provenance.
   Draft PRs are welcome while you are checking a design.

Use an issue for a reproducible bug or feature proposal. Use the private route
in [SECURITY.md](SECURITY.md) for vulnerabilities.

## Development dependencies

For the full Python suite, install a C11 compiler (`cc`, or set `CC`) for the
simulator, create an isolated environment, and install the device dependency
set. These commands download packages once; unit tests then use fake hardware
and synthetic sessions without model downloads:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-device.txt
.venv/bin/python -m unittest discover -s tests -v
```

Examples use POSIX paths. On Windows use `py` to create the environment and
`.venv\Scripts\python.exe` in place of `.venv/bin/python`.
CI runs on Linux with Python 3.10, 3.12, and 3.14, and Node.js 22 for JavaScript
checks. With Node.js available:

```bash
node --check web/app.js
node --check simulator/web/app.js
node --check integrations/t3-current-session.js
node --check integrations/t3-voice-context.js
node --test tests/t3_current_session_test.cjs
```

For browser verification, add Playwright and its Chromium download:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python tests/browser_smoke.py
.venv/bin/python tests/browser_voice_smoke.py
.venv/bin/python tests/browser_demo_smoke.py
.venv/bin/python tests/browser_characters_smoke.py
.venv/bin/python tests/browser_simulator_smoke.py
```

Linux may also need browser system libraries; Playwright's
`install --with-deps chromium` installs them using the system package manager.
`BROWSER_EXECUTABLE` selects an existing Chromium binary. The browser checks use
synthetic state and fake USB/T3 adapters. Screenshots and traces go to ignored
`artifacts/browser/`. Use synthetic content in screenshots attached to a change.

A C11 compiler (`cc`) also runs the native protocol and gesture checks listed in
[CI](.github/workflows/ci.yml). Pillow plus `cc` also enable
`.venv/bin/python tests/device_ui_preview.py`, which produces the round-display
gallery in `artifacts/circular-ui/`. The
[interaction simulator](docs/simulator.md) exercises navigation, idle/wake, and
voice review using synthetic input and the native firmware input engine.
Docker is needed only for
`bash firmware/build.sh`; it builds without flashing. Optional speech smoke
checks download separate dependencies and models; follow the commands in CI.
Report any checks you could not run.

## Keep changes reviewable

Explain the problem, the resulting behavior, and the checks you ran. Keep a
change focused; add a regression test when a behavior can be tested meaningfully.
Documentation fixes usually need a command/link check rather than a new test.
For interface changes, check narrow and wide screens, keyboard access, visible
focus, and reduced motion. A browser preview does not verify physical hardware.

Keep the preview usable with the standard library alone. Preserve read-only T3
database access, authenticated prompt submission, Host/Origin checks, frame
bounds, and the voice destination captured when recording begins. Do not retry
a send whose acknowledgement is uncertain. Model selection and permission
settings belong to the selected T3 session.

Use explicit device paths for hardware work; see the
[firmware guide](firmware/README.md). Source checks do not require service
installation or client patching. `companion.py install` replaces the active
runtime under `~/.local/share/shark-harness/current`; an older checkout may omit
features from an installed runtime.

Keep credentials, real session databases, recordings, model weights, USB serial
numbers, and flash backups out of contributions. Sanitize logs and screenshots.
Ignore rules do not remove files already tracked in Git. Follow
[SECURITY.md](SECURITY.md) for vulnerability reports. Before proposing dependency
or asset additions, identify their source and redistribution terms. Original
project source uses the [Apache-2.0 license](LICENSE); external
dependencies and artwork retain their own terms. Contributions to original project code are submitted under Apache-2.0; identify
separate terms for third-party material or independently licensed artwork.

## Add a character

Start with [the character guide](docs/characters.md) and an original data-only
pack. Its offline preview uses the actual device renderer, and accepts a local
JSON path so a first art contribution needs no firmware change or USB access.
Include author, source, licensing evidence, state previews, and measured
compressed-frame sizes. Characters cannot add commands or modify voice routing.
