"""Exercise the public demo with no database, device, or T3 connection.

Run: python tests/browser_demo_smoke.py
Optionally set BROWSER_EXECUTABLE to an installed Chromium binary.
"""

from functools import partial
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bridge import Handler, SceneStore
from browser_smoke import check_setup_and_motion, image_upload, wait_for_page


def main():
    output = ROOT / "artifacts/browser/demo"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary, \
            patch("bridge.read_status", side_effect=AssertionError("Demo opened a real workspace")) as read_status:
        database = Path(temporary) / "must-not-be-created.sqlite"
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(
            Handler, database=database, scenes=SceneStore(), demo=True))
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
                errors, voice_writes = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: voice_writes.append(request.url)
                        if request.method in {"POST", "DELETE"}
                        and urlsplit(request.url).path.startswith(("/api/listen", "/api/speech", "/api/voice-client")) else None)
                try:
                    page.goto(url, wait_until="networkidle")
                    expect(page.locator("#connection-label")).to_have_text("Demo mode")
                    expect(page.locator("#mode-label")).to_contain_text("SAMPLE DATA")
                    expect(page.locator("#voice-readiness")).to_have_text("DEMO · VOICE OFF")
                    expect(page.locator(".thread-card")).to_have_count(3)
                    expect(page.locator("#thread-count")).to_contain_text("SAMPLE DATA")
                    wait_for_page(page, "() => window.captureT3Display(false) !== null")
                    page.screenshot(path=str(output / "initial.png"))
                    for control in ["#listen-session", "#listen-enable", "#listen-record", "#listen-stop",
                                    "#speech-text", "#speech-form button", "#speech-volume", "#speech-stop"]:
                        expect(page.locator(control)).to_be_disabled()
                    for button in page.locator(".thread-select").all():
                        expect(button).to_be_disabled()
                    expect(page.locator("#listen-review")).not_to_be_visible()
                    check_setup_and_motion(page)

                    message = page.get_by_role("textbox", name="A thought on display")
                    message.fill("A small signal")
                    expect(page.locator("#message-count")).to_have_text("14 / 60")
                    page.get_by_role("button", name="Show message on simulator").click()
                    page.locator('canvas[aria-label*="A small signal"]').wait_for()
                    expect(page.locator("#scene-lifetime")).to_have_text(re.compile(r"\d+s LEFT"))
                    expect(message).to_have_value("")
                    assert page.evaluate("() => window.captureT3Display(false).overlay.kind") == "message"
                    page.locator("#chart-button").click()
                    page.locator('canvas[aria-label^="chart:"]').wait_for()
                    assert page.evaluate("() => window.captureT3Display(false).overlay.values.length") > 1
                    page.locator("#image-file").set_input_files(image_upload())
                    page.locator('canvas[aria-label^="image:"]').wait_for()
                    wait_for_page(page, "() => window.captureT3Display(false)?.imageReady === true")
                    page.locator("#clear-button").click()
                    page.locator('canvas[aria-label^="aurora animation"]').wait_for()
                    expect(page.locator("#scene-lifetime")).to_have_text("AMBIENT")

                    # Expiry uses the real scene API and clock; it must update the canvas without a click.
                    response = page.request.post(url + "/api/scene", data={"title": "Back to ambient soon", "seconds": 5})
                    assert response.ok
                    page.locator('canvas[aria-label*="Back to ambient soon"]').wait_for()
                    countdown = page.locator("#scene-lifetime").inner_text()
                    assert re.fullmatch(r"\d+s LEFT", countdown), countdown
                    remaining = int(countdown.split("s")[0])
                    if remaining > 1:
                        wait_for_page(page, "seconds => parseInt(document.querySelector('#scene-lifetime').textContent) < seconds", remaining)
                    page.locator('canvas[aria-label^="aurora animation"]').wait_for()
                    expect(page.locator("#scene-lifetime")).to_have_text("AMBIENT")
                    assert page.evaluate("() => window.captureT3Display(false).overlay") is None

                    page.screenshot(path=str(output / "desktop.png"), full_page=True)
                    for width in [390, 320]:
                        page.set_viewport_size({"width": width, "height": 844})
                        assert not page.evaluate("() => document.documentElement.scrollWidth > innerWidth"), width
                        page.locator("#setup-open").click()
                        expect(page.get_by_role("dialog")).to_be_visible()
                        assert not page.locator("#setup-dialog").evaluate("el => el.scrollWidth > el.clientWidth"), width
                        page.keyboard.press("Escape")
                        page.screenshot(path=str(output / f"mobile-{width}.png"), full_page=True)
                    read_status.assert_not_called()
                    assert not database.exists()
                    assert not voice_writes, voice_writes
                    assert not errors, errors
                    print("Demo browser checks passed: explicit sample data, disabled voice, keyboard setup, "
                          "reduced motion, message/chart/image, scene expiry, mobile layout, no T3 access.")
                finally:
                    context.tracing.stop(path=str(output / "trace.zip"))
                    browser.close()
        finally:
            server.shutdown()
            worker.join()
            server.server_close()


if __name__ == "__main__":
    main()
