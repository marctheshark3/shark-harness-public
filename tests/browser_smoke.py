"""Real browser checks against synthetic T3 data, never the user's database.

pip install -r requirements-dev.txt && python -m playwright install chromium
python tests/browser_smoke.py
Optionally set BROWSER_EXECUTABLE to an already-installed Chromium binary.
"""

from functools import partial
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bridge import Handler, SceneStore  # noqa: E402
from speech import SpeechStore  # noqa: E402
from device import Display, image_pixels  # noqa: E402
from test_device import Transport  # noqa: E402
from listen import ListenStore  # noqa: E402
from test_listen import Session  # noqa: E402
import base64
import io


def image_upload():
    """A portable image payload; sandboxed Chromium need not read this checkout."""
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (466, 466), "#151617")
    draw = ImageDraw.Draw(image)
    draw.ellipse((90, 90, 376, 376), fill="#ff5c35")
    draw.rectangle((218, 135, 248, 331), fill="#f1f0eb")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return {"name": "signal.png", "mimeType": "image/png", "buffer": buffer.getvalue()}


def wait_for_page(page, expression, arg=None):
    # Older Playwright versions eval their polling predicate inside the page on
    # later frames, which violates this app's CSP. Keep polling in the test host.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if page.evaluate(expression, arg):
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"Browser condition did not become true: {expression}")


def check_setup_and_motion(page):
    # The canvas must really pause, rather than only changing the button label.
    motion = page.locator("#motion-toggle")
    expect(motion).to_have_accessible_name("Resume animation")
    expect(motion).to_have_attribute("aria-pressed", "true")
    wait_for_page(page, "() => window.captureT3Display(false) !== null")
    still = page.locator("#screen").evaluate("canvas => canvas.toDataURL()")
    page.wait_for_timeout(350)
    assert page.locator("#screen").evaluate("canvas => canvas.toDataURL()") == still
    motion.click()
    expect(motion).to_have_accessible_name("Pause animation")
    wait_for_page(page, "frame => document.querySelector('#screen').toDataURL() !== frame", arg=still)
    motion.click()
    expect(motion).to_have_attribute("aria-pressed", "true")

    opener = page.locator("#setup-open")
    opener.focus()
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    expect(page.get_by_role("button", name="Close setup guide")).to_be_focused()
    page.keyboard.press("Tab")
    expect(dialog.get_by_role("link", name="Open the full setup guide")).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(page.get_by_role("button", name="Close setup guide")).to_be_focused()
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(opener).to_be_focused()
    opener.click()
    page.get_by_role("button", name="Close setup guide").click()
    expect(dialog).not_to_be_visible()
    expect(opener).to_be_focused()


def main():
    output = ROOT / "artifacts/browser"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        database = Path(temp) / "state.sqlite"
        db = sqlite3.connect(database)
        db.executescript((ROOT / "tests/fixtures/t3.sql").read_text())
        db.execute("UPDATE projection_turns SET state='running'")
        db.execute("INSERT INTO projection_projects VALUES ('q','Signal lab','/projects/signal-lab',NULL)")
        rows = {f"preview-{i}": dict(thread_id=f"preview-{i}", project_id="q",
                    title="The eighth session" if i == 7 else f"Workbench session {i}") for i in range(1, 8)}
        for row in rows.values():
            db.execute("INSERT INTO projection_threads VALUES (?,?,?,'2026-10-04T16:00:00Z',NULL,NULL,0,0)",
                       (row["thread_id"], row["project_id"], row["title"]))
        db.commit()
        db.close()
        speech = SpeechStore()
        class BrowserSession(Session):
            remote_ready = True

            def describe(self, thread_id):
                if thread_id == "voice-remote":
                    return dict(thread_id=thread_id, project_id="q", title="Remote browser session",
                                projectTitle="Signal lab", environment="Demo remote", browser=True,
                                new=False, ready=self.remote_ready, error="The demo browser disconnected. Reconnect T3.")
                return rows[thread_id] if thread_id in rows else super().describe(thread_id)

        session = BrowserSession()
        listen = ListenStore(session, "one", True)
        listen.hardware({"mic": {"ready": True, "recording": False, "error": 0}})
        listen.last_seen = time.monotonic() + 120  # Simulated connected microphone for this browser test.
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, database=database, scenes=SceneStore(), speech=speech, listen=listen))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
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
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                try:
                    page.goto(f"http://127.0.0.1:{server.server_port}", wait_until="networkidle")
                    page.locator("#connection-label", has_text="T3 connected").wait_for()
                    page.get_by_role("heading", name="An idea", exact=True).wait_for()
                    expect(page.locator(".thread-card")).to_have_count(8)
                    check_setup_and_motion(page)
                    search = page.get_by_role("searchbox", name="Find a session")
                    search.fill("EIGHTH")
                    expect(page.locator(".thread-card")).to_have_count(1)
                    expect(page.locator(".thread-title")).to_have_text("The eighth session")
                    search.fill("signal lab")
                    expect(page.locator(".thread-card")).to_have_count(7)
                    search.fill("no such session")
                    expect(page.locator(".thread-card")).to_have_count(0)
                    expect(page.locator("#thread-list")).to_contain_text("No matching sessions")
                    search.clear()
                    expect(page.locator(".thread-card")).to_have_count(8)
                    listen.configure({"threadId": "voice-remote", "enabled": True})
                    expect(page.locator("#voice-target")).to_contain_text("Remote browser session")
                    expect(page.locator("#voice-readiness")).to_have_text("READY TO LISTEN")
                    session.remote_ready = False
                    expect(page.locator("#voice-readiness")).to_have_text("SESSION UNAVAILABLE")
                    expect(page.locator("#listen-record")).to_be_disabled()
                    expect(page.locator("#listen-feedback")).to_contain_text("demo browser disconnected")
                    session.remote_ready = True
                    expect(page.locator("#voice-readiness")).to_have_text("READY TO LISTEN")
                    expect(page.locator("#listen-record")).to_be_enabled()
                    assert session.sent == []
                    listen.configure({"threadId": "one", "enabled": True})
                    expect(page.locator("#voice-target")).to_have_text("Session one")
                    job = listen.begin({"captureId": 1})["job"]
                    assert job["expiresAt"] is None
                    page.locator('canvas[aria-label*="Listening…"]').wait_for()
                    expect(page.locator("#scene-lifetime")).to_have_text("RECORDING")
                    # Changing the next prompt's mode must not rewrite this recording's release behavior.
                    listen.configure({"threadId": "one", "enabled": True, "autoSend": True})
                    page.wait_for_timeout(1200)
                    expect(page.locator("#listen-mode")).to_contain_text("review before sending")
                    frame = page.evaluate("() => window.captureT3Display(true)")
                    assert frame["overlay"]["title"] == "Listening…"
                    assert frame["overlay"]["expiresAt"] is None
                    assert "Release to review" in frame["overlay"]["detail"]
                    assert job["threadTitle"] in frame["overlay"]["detail"]
                    transport = Transport()
                    display = Display(transport)
                    display.request("hello")
                    pixels = image_pixels(io.BytesIO(base64.b64decode(frame["png"].split(",", 1)[1])))
                    result = display.send_frame(pixels, 600, expires_at=frame["overlay"]["expiresAt"])
                    assert not result.get("skipped")
                    assert transport.writes[-1]["op"] == "frame.end"
                    assert transport.writes[-1]["seconds"] == 600
                    assert transport.pixels == pixels
                    page.locator(".device").screenshot(path=str(output / "listening.png"))
                    listen.progress({"id": job["id"], "state": "transcribing"})
                    page.locator('canvas[aria-label*="Finding your words…"]').wait_for()
                    frame = page.evaluate("() => window.captureT3Display(false)")
                    assert frame["overlay"]["expiresAt"] > time.time() * 1000
                    assert job["threadTitle"] in frame["overlay"]["detail"]
                    listen.progress({"id": job["id"], "state": "review", "text": "Check the test results."})
                    page.locator("#listen-review").wait_for(state="visible")
                    page.locator('canvas[aria-label*="Your prompt is ready"]').wait_for()
                    assert page.locator("#listen-text").input_value() == "Check the test results."
                    assert session.sent == []
                    page.locator("#listen-text").fill("Check the failing tests first.")
                    page.wait_for_timeout(1200)
                    assert page.locator("#listen-text").input_value() == "Check the failing tests first."
                    # The server accepts once, but its response is lost. A later
                    # authoritative poll must clear the transient action error.
                    stale_review = listen.get()
                    hold_review = {"enabled": True}
                    def voice_status(route):
                        if hold_review["enabled"]:
                            route.fulfill(status=200, content_type="application/json", body=json.dumps(stale_review))
                        else:
                            route.continue_()
                    def drop_confirmation_reply(route):
                        response = route.fetch()
                        assert response.ok
                        route.abort()
                    page.route("**/api/listen", voice_status)
                    page.route("**/api/listen/confirm", drop_confirmation_reply)
                    page.get_by_role("button", name="Confirm & send to T3").click()
                    expect(page.locator("#listen-feedback")).to_contain_text("Failed to fetch")
                    assert len(session.sent) == 1
                    hold_review["enabled"] = False
                    page.locator("#listen-feedback", has_text="Prompt accepted").wait_for()
                    page.unroute("**/api/listen")
                    page.unroute("**/api/listen/confirm")
                    assert len(session.sent) == 1 and session.sent[0][:2] == ("one", "Check the failing tests first.")
                    assert page.locator("#listen-review").is_hidden()
                    listen.configure({"threadId": "one", "enabled": True, "autoSend": True})
                    page.locator("#listen-mode", has_text="release to send directly").wait_for()
                    eighth = page.get_by_role("button", name="Select The eighth session in Signal lab for voice")
                    eighth.click()
                    expect(page.locator("#voice-target")).to_contain_text("The eighth session")
                    expect(page.locator("#listen-session")).to_have_value("preview-7")
                    expect(page.locator("#listen-enable")).to_be_focused()
                    assert listen.get()["target"]["id"] == "preview-7"
                    assert len(session.sent) == 1  # Selecting never dispatches another prompt.
                    selected = page.get_by_role("button", name="Selected The eighth session in Signal lab for voice")
                    selected.focus()
                    page.wait_for_timeout(1700)  # A complete status poll must preserve keyboard focus.
                    expect(selected).to_be_focused()
                    page.locator("#speech-text").fill("Hello from the voice controls")
                    page.get_by_role("button", name="Speak on device").click()
                    page.locator("#speech-feedback", has_text="Queued").wait_for()
                    assert speech.get()["job"]["text"] == "Hello from the voice controls"
                    page.locator("#speech-volume").fill("0")
                    page.locator("#speech-volume").dispatch_event("change")
                    expect(page.locator("#volume-value")).to_have_text("Muted")
                    assert speech.get()["volume"] == 0
                    page.get_by_role("button", name="Stop", exact=True).click()
                    page.locator("#speech-feedback", has_text="Speech stopped").wait_for()
                    assert speech.get()["job"]["state"] == "stopped"
                    page.locator("[data-state=waiting]").click()
                    page.locator('canvas[aria-label*="needs your input"]').wait_for()
                    assert "DEMO STATE" in page.locator("#mode-label").inner_text()
                    page.locator("#chart-button").click()
                    page.locator('canvas[aria-label^="chart:"]').wait_for()
                    page.locator(".device").screenshot(path=str(output / "chart.png"))
                    page.locator("#clear-button").click()
                    page.locator('canvas[aria-label^="aurora animation"]').wait_for()
                    page.locator("#message").fill("A visual from T3")
                    page.get_by_role("button", name="Show message on simulator").click()
                    page.locator('canvas[aria-label*="A visual from T3"]').wait_for()
                    frame = page.evaluate("() => window.captureT3Display(true)")
                    assert frame["overlay"]["title"] == "A visual from T3"
                    assert frame["imageReady"] is True
                    transport = Transport()
                    display = Display(transport)
                    display.request("hello")
                    pixels = image_pixels(io.BytesIO(base64.b64decode(frame["png"].split(",", 1)[1])))
                    display.send_frame(pixels, expires_at=frame["overlay"]["expiresAt"])
                    assert transport.pixels == pixels
                    page.locator("#clear-button").click()
                    page.locator("[data-state=live]").click()
                    for style in ["shark", "orbit", "aurora"]:
                        page.locator(f"[data-scene={style}]").click()
                        page.locator(f'canvas[aria-label^="{style} animation"]').wait_for()
                    image = output / "ambient.png"
                    page.locator(".device").screenshot(path=str(image))
                    page.screenshot(path=str(output / "desktop.png"), full_page=True)
                    assert not page.evaluate("() => document.documentElement.scrollWidth > innerWidth")
                    page.locator("#image-file").set_input_files(image_upload())
                    page.locator('canvas[aria-label^="image:"]').wait_for()
                    wait_for_page(page, "() => window.captureT3Display(false)?.imageReady === true")
                    assert page.evaluate("() => window.captureT3Display(false).overlay.kind") == "image"
                    page.locator("#clear-button").click()
                    page.locator('canvas[aria-label^="aurora animation"]').wait_for()
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.screenshot(path=str(output / "mobile.png"), full_page=True)
                    assert not page.evaluate("() => document.documentElement.scrollWidth > innerWidth")
                    page.route("**/api/status", lambda route: route.abort())
                    page.locator("#connection-label", has_text="T3 unavailable").wait_for()
                    assert not page.locator("#connection-dot").evaluate("el => el.classList.contains('online')")
                    assert not errors, errors
                    print("Browser checks passed: setup keyboard/focus, reduced motion, session search and selection, "
                          "live voice overlays, pinned release mode, lost-ack recovery, single send, "
                          "scenes, speech, images, mobile, offline.")
                finally:
                    context.tracing.stop(path=str(output / "trace.zip"))
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    main()
