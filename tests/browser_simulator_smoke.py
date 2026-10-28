"""Exercise the real simulator in Chromium, with synthetic I/O only.

Run: python tests/browser_simulator_smoke.py
Requires Pillow, a C compiler, and Playwright Chromium. BROWSER_EXECUTABLE can
select an installed Chromium binary. No USB, microphone, database, or T3 access.
"""
from contextlib import ExitStack, redirect_stdout
import io
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from browser_smoke import wait_for_page
from simulator.__main__ import SimulatorServer


def ready(page):
    try:
        wait_for_page(page, """() => state && displayedVersion === state.frameVersion
            && pendingActions === 0 && !frameBusy && document.querySelector('#frame').naturalWidth === 466""")
    except AssertionError as error:
        details = page.evaluate("""() => ({displayedVersion, version: state?.frameVersion,
            image: state?.imageVersion, pendingActions, frameBusy, frameKey, src: frame.src,
            width: frame.naturalWidth, scenario: state?.scenario, screen: state?.screen.kind})""")
        raise AssertionError(f"{error}\nFrame state: {details}") from error


def choose(page, scenario):
    page.select_option("#scenario", scenario)
    wait_for_page(page, "name => state.scenario === name", scenario)
    ready(page)


def point(page, x, y):
    bounds = page.locator("#display").bounding_box()
    return bounds["x"] + x / 466 * bounds["width"], bounds["y"] + y / 466 * bounds["height"]


def glass_tap(page, x, y):
    ready(page)
    page.mouse.click(*point(page, x, y), delay=100)


def physical_start(value):
    return value["type"] in {"tap", "hold", "swipe", "boot"} or (
        value["type"] == "pointer" and value["phase"] == "down") or (
        value["type"] == "key" and value["pressed"])


class DelayedFrames:
    """Delay real PNG response delivery, preserving each requested version.

    The server renders the frame immediately; only its arrival in Chromium is
    delayed. This models image transfer lag without replacing renderer output.
    """
    def __init__(self, page):
        self.page, self.pending = page, []
        self.released = False

    def intercept(self, route):
        response = route.fetch()
        assert response.ok, response.status
        if self.released:
            route.fulfill(response=response)
        else:
            self.pending.append((route, response))

    def __enter__(self):
        self.page.route("**/frame.png*", self.intercept)
        return self

    def wait(self):
        deadline = time.monotonic() + 5
        while not self.pending and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        assert self.pending, "The PNG request was not intercepted"

    def __exit__(self, *unused):
        self.released = True
        try:
            while self.pending:
                route, response = self.pending.pop(0)
                route.fulfill(response=response)
        finally:
            self.page.unroute("**/frame.png*", self.intercept)


def check_navigation(page):
    choose(page, "projects")
    assert page.evaluate("state.screen.index") == 1
    glass_tap(page, 43, 233)
    wait_for_page(page, "() => state.screen.index === 3")
    expect(page.locator("#last-action")).to_contain_text("Previous project")
    expect(page.locator("#last-action")).to_contain_text("wrapped")
    glass_tap(page, 423, 233)
    wait_for_page(page, "() => state.screen.index === 1")
    expect(page.locator("#last-action")).to_contain_text("Next project")
    ready(page)

    # Finger movement uses the native filter, rather than a synthetic Next call.
    page.mouse.move(*point(page, 330, 235))
    page.mouse.down()
    for x in (300, 270, 240, 210, 180, 135):
        page.wait_for_timeout(45)
        page.mouse.move(*point(page, x, 235))
    page.mouse.up()
    wait_for_page(page, "() => state.screen.index === 2")

    choose(page, "sessions")
    page.locator("#display").focus()
    page.keyboard.press("ArrowRight")
    wait_for_page(page, "() => state.screen.index === 2")
    ready(page)
    page.keyboard.press("ArrowLeft")
    wait_for_page(page, "() => state.screen.index === 1")
    ready(page)
    page.locator("#boot").focus()
    page.keyboard.press("Enter", delay=100)  # Above the physical key's debounce interval.
    wait_for_page(page, "() => state.destination.id === 'atlas-2'")
    assert page.evaluate("state.submissions") == 0


def check_voice_and_wake(page):
    choose(page, "sessions")
    hold = page.locator('.control-button[data-id="center"]')
    hold.focus()
    page.keyboard.down("Space")
    wait_for_page(page, "() => state.recording")
    # Replacing this node during Listening would lose its eventual keyup.
    expect(hold).to_have_attribute("data-held", "true")
    expect(hold).to_be_focused()
    pinned = page.evaluate("state.job.threadId")
    page.keyboard.up("Space")
    wait_for_page(page, "() => state.screen.kind === 'sent' && state.submissions === 1")
    assert page.evaluate("state.job.threadId") == pinned

    choose(page, "sessions")
    page.locator("#display").focus()
    page.keyboard.down("Space")
    wait_for_page(page, "() => state.recording")
    page.locator("#scenario").focus()
    page.keyboard.up("Space")
    wait_for_page(page, "() => !state.recording && !state.input.touch.pressed")
    assert page.evaluate("state.submissions") == 0

    choose(page, "idle")
    target = page.evaluate("state.destination.id")
    glass_tap(page, 423, 233)
    wait_for_page(page, "() => !state.idle && !state.wakePending")
    ready(page)
    assert page.evaluate("state.destination.id") == target
    assert page.evaluate("[state.recordings, state.submissions]") == [0, 0]

    choose(page, "paused")
    page.locator("#advance").click()
    wait_for_page(page, "() => state.idle")
    ready(page)
    page.locator("#display").focus()
    page.keyboard.down("Space")
    page.wait_for_timeout(1300)
    page.keyboard.up("Space")
    wait_for_page(page, "() => !state.idle && !state.wakePending")
    assert page.evaluate("state.paused")
    assert page.evaluate("[state.recordings, state.submissions]") == [0, 0]


def check_review(page):
    choose(page, "review")
    pinned = page.evaluate("state.job.threadId")
    pages = page.evaluate("state.screen.pages")
    assert pages > 1
    for _ in range(pages):
        ready(page)
        version = page.evaluate("state.frameVersion")
        page.locator('.control-button[data-id="right"]').click()
        wait_for_page(page, "old => state.frameVersion > old", version)
    ready(page)
    assert page.evaluate("state.screen.kind") == "confirm"
    assert page.evaluate("state.submissions") == 0
    # Rapid repeated activation cannot deliver the same reviewed prompt twice.
    page.locator('.control-button[data-id="right"]').evaluate("button => { button.click(); button.click(); }")
    wait_for_page(page, "() => state.screen.kind === 'sent' && state.submissions === 1")
    assert page.evaluate("state.job.threadId") == pinned
    page.wait_for_timeout(450)
    assert page.evaluate("state.submissions") == 1


def check_delayed_frames(page, actions):
    choose(page, "sessions")
    old_version = page.evaluate("displayedVersion")
    old_alt = page.locator("#frame").get_attribute("alt")
    with DelayedFrames(page) as delayed:
        # The old visible Pause control occupies the new Send control's region.
        # Applying its old coordinates to the new screen would be a serious bug.
        page.select_option("#scenario", "confirm")
        wait_for_page(page, "() => state.scenario === 'confirm' && frameBusy")
        delayed.wait()
        assert page.evaluate("state.frameVersion") > old_version
        assert page.evaluate("displayedVersion") == old_version
        expect(page.locator('.control-button[data-id="right"]')).to_have_text("Pause voice")
        expect(page.locator("#frame")).to_have_attribute("alt", old_alt)
        count = len(actions)
        page.mouse.click(*point(page, 335, 390), delay=100)
        page.locator('.control-button[data-id="right"]').click()
        page.wait_for_timeout(150)
        assert len(actions) == count, "A stale visible frame dispatched input"
        assert page.evaluate("state.submissions") == 0
    ready(page)
    loaded = page.evaluate("displayedVersion")
    before = len(actions)
    page.locator('.control-button[data-id="right"]').click()
    wait_for_page(page, "() => state.submissions === 1")
    physical = [value for value in actions[before:] if physical_start(value)]
    assert len(physical) == 1 and physical[0]["frameVersion"] == loaded

    # A release/cancellation is part of an existing contact. It must reach the
    # native state machine even while the Listening image has not arrived.
    for cancel in (False, True):
        choose(page, "sessions")
        old_version = page.evaluate("displayedVersion")
        before = len(actions)
        with DelayedFrames(page) as delayed:
            page.locator("#display").focus()
            page.keyboard.down("Space")
            wait_for_page(page, "() => state.recording && frameBusy")
            delayed.wait()
            assert page.evaluate("displayedVersion") == old_version
            assert page.evaluate("state.frameVersion") > old_version
            if cancel: page.locator("#scenario").focus()
            page.keyboard.up("Space")
            wait_for_page(page, "() => !state.recording && !state.input.touch.pressed")
            if not cancel: wait_for_page(page, "() => state.submissions === 1")
            assert page.evaluate("state.submissions") == (0 if cancel else 1)
            starts = [value for value in actions[before:] if physical_start(value)]
            ends = [value for value in actions[before:] if value["type"] == "pointer"
                    and value["phase"] == ("cancel" if cancel else "up")]
            assert len(starts) == 1 and starts[0]["frameVersion"] == old_version
            assert len(ends) == 1 and "frameVersion" not in ends[0]
        ready(page)


def check_motion_and_layout(page, output, frame_requests):
    page.emulate_media(reduced_motion="no-preference")
    choose(page, "sessions")
    before = len(frame_requests)
    version = page.evaluate("state.frameVersion")
    source = page.locator("#frame").get_attribute("src")
    page.wait_for_timeout(900)
    ready(page)
    assert len(frame_requests) > before, "Production character did not animate"
    assert page.locator("#frame").get_attribute("src") != source
    assert page.evaluate("state.frameVersion") == version, "Animation invalidated unchanged controls"
    page.emulate_media(reduced_motion="reduce")
    ready(page)
    page.wait_for_timeout(450)  # Drain a paint requested before the preference changed.
    before = len(frame_requests)
    page.wait_for_timeout(900)
    assert len(frame_requests) == before, "Reduced motion kept requesting character frames"

    page.emulate_media(reduced_motion="no-preference")
    choose(page, "idle")
    before = len(frame_requests)
    page.wait_for_timeout(650)
    assert len(frame_requests) > before, "Ambient preview did not animate"
    page.emulate_media(reduced_motion="reduce")
    ready(page)
    before = len(frame_requests)
    page.wait_for_timeout(650)
    assert len(frame_requests) == before, "Reduced motion kept requesting animation frames"
    expect(page.locator("#simulation-note")).to_contain_text("approximation")

    choose(page, "sessions")
    page.locator('.control-button[data-id="next"]').click()
    wait_for_page(page, "() => state.screen.index === 2")
    ready(page)
    page.locator('.control-button[data-id="next"]').evaluate("button => button.blur()")
    page.screenshot(path=str(output / "desktop.png"), full_page=True)
    for width in (390, 320):
        page.set_viewport_size({"width": width, "height": 844})
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), width
        page.screenshot(path=str(output / f"mobile-{width}.png"), full_page=True)
    page.check("#show-regions")
    expect(page.locator("#hit-regions")).to_be_visible()
    regions = page.locator(".hit-region").evaluate_all("nodes => nodes.map(node => ({w:node.getBoundingClientRect().width,h:node.getBoundingClientRect().height}))")
    assert regions and all(region["w"] > 0 and region["h"] > 0 for region in regions)


def main():
    output = ROOT / "artifacts/browser/simulator"
    output.mkdir(parents=True, exist_ok=True)
    logs = io.StringIO()
    with ExitStack() as stack:
        stack.enter_context(redirect_stdout(logs))
        for target in ("device.connect", "speech.bridge_request", "device_ui.bridge_request", "sqlite3.connect"):
            stack.enter_context(patch(target, side_effect=AssertionError("Simulator touched live I/O")))
        server = SimulatorServer(0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as playwright:
                options = {"headless": True}
                if os.environ.get("BROWSER_EXECUTABLE"):
                    options["executable_path"] = os.environ["BROWSER_EXECUTABLE"]
                browser = playwright.chromium.launch(**options)
                context = browser.new_context(viewport={"width": 1440, "height": 1100}, reduced_motion="reduce")
                context.tracing.start(screenshots=True, snapshots=True)
                page = context.new_page()
                page.set_default_timeout(10000)
                errors, actions, frame_requests, unexpected = [], [], [], []
                page.on("pageerror", lambda error: errors.append(str(error)))

                def observe(request):
                    parsed = urlsplit(request.url)
                    if request.method == "POST" and parsed.path == "/api/action":
                        actions.append(request.post_data_json)
                    if parsed.path == "/frame.png": frame_requests.append(request.url)
                    if parsed.netloc != urlsplit(url).netloc or parsed.path.startswith(("/api/listen", "/api/speech", "/api/voice-client")):
                        unexpected.append(request.url)
                page.on("request", observe)
                try:
                    page.goto(url)
                    ready(page)
                    expect(page.locator(".synthetic-badge")).to_have_text("SYNTHETIC DATA")
                    check_navigation(page)
                    check_voice_and_wake(page)
                    check_review(page)
                    check_delayed_frames(page, actions)
                    check_motion_and_layout(page, output, frame_requests)
                    assert not errors, errors
                    assert not unexpected, unexpected
                except Exception:
                    page.screenshot(path=str(output / "failure.png"), full_page=True)
                    raise
                finally:
                    context.tracing.stop(path=str(output / "trace.zip"))
                    browser.close()
        finally:
            server.shutdown()
            worker.join()
            server.server_close()
            (output / "server.log").write_text(logs.getvalue())
    print("Simulator browser checks passed: native navigation/wrap, keyboard/BOOT, hold/release, "
          "focus cancellation, wake-only, review/confirmation, delayed-frame safety, "
          "reduced motion, desktop/mobile layout, and no live I/O.")


if __name__ == "__main__": main()
