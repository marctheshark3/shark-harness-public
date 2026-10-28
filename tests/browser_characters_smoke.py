"""Verify actual character frames and accessible preview controls in Chromium."""
import os
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from character_preview import build_preview, gallery_html
from characters import FRAMES, STATES, load_character
from playwright.sync_api import sync_playwright


def main():
    output = ROOT / "artifacts/characters"
    screenshots = ROOT / "artifacts/browser/characters"
    screenshots.mkdir(parents=True, exist_ok=True)
    metrics = build_preview(output)
    with sync_playwright() as p:
        options = {"headless": True}
        if os.environ.get("BROWSER_EXECUTABLE"):
            options["executable_path"] = os.environ["BROWSER_EXECUTABLE"]
        browser = p.chromium.launch(**options)
        page = browser.new_page(viewport={"width": 728, "height": 950}, reduced_motion="reduce")
        errors, requests = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))
        page.goto((output / "index.html").as_uri())
        page.get_by_role("button", name="Play motion").wait_for()
        page.wait_for_function("document.querySelector('#device-image').complete")
        assert page.locator("#device-image").evaluate("image => image.naturalWidth") == 466
        for name in ("Reef shark", "Mochi manta", "Dumpling octopus"):
            page.get_by_role("button", name=name, exact=True).click()
            for state in ("working", "waiting", "error", "done", "idle", "offline", "unknown"):
                page.get_by_label("Selected session state").select_option(state)
                page.wait_for_function("document.querySelector('#device-image').complete && document.querySelector('#device-image').naturalWidth === 466")
                assert page.locator("#device-image").get_attribute("src").endswith(f"-{state}-0.png")
                assert page.locator("#state-note").inner_text()
        page.get_by_label("Selected session state").select_option("working")
        page.get_by_role("button", name="Play motion").click()
        page.wait_for_function("!document.querySelector('#device-image').getAttribute('src').endsWith('-0.png')")
        page.get_by_role("button", name="Pause motion").click()
        stopped = page.locator("#device-image").get_attribute("src")
        page.wait_for_timeout(650)
        assert page.locator("#device-image").get_attribute("src") == stopped
        for width in (728, 360, 320):
            page.set_viewport_size({"width": width, "height": 1100})
            dimensions = page.locator("#device-image").bounding_box()
            assert abs(dimensions["width"] - dimensions["height"]) < 1, dimensions
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(screenshots / f"preview-{width}.png"), full_page=True)
        page.get_by_role("button", name="Reef shark", exact=True).focus()
        page.keyboard.press("Enter")
        assert page.get_by_role("button", name="Reef shark", exact=True).get_attribute("aria-pressed") == "true"
        assert all(url.startswith("file:") for url in requests), requests
        assert not errors, errors
        # A custom pack's attribution is displayed as text, never executed.
        with tempfile.TemporaryDirectory() as temp:
            custom = json.loads((ROOT / "characters/reef.json").read_text())
            pack = Path(temp) / "custom.json"
            images = {"reef": {state: [(output / f"reef-{state}-{frame}.png").as_uri()
                        for frame in range(FRAMES)] for state in STATES}}
            for name in ("__FIRST__", "__LABELS__", "</script><script>pwned=1</script>"):
                custom["name"] = name
                pack.write_text(json.dumps(custom))
                document = Path(temp) / "metadata.html"
                document.write_text(gallery_html([load_character(pack)], images))
                page.goto(document.as_uri())
                page.get_by_role("button", name=name, exact=True).click()
                page.get_by_label("Selected session state").select_option("waiting")
                page.wait_for_function("document.querySelector('#device-image').complete && document.querySelector('#device-image').naturalWidth === 466")
                assert page.evaluate("typeof window.pwned") == "undefined"
                assert not errors, errors
        browser.close()
    print("Character browser smoke passed: three original characters, seven states, exact circular aspect, keyboard, reduced motion, pause, 728/360/320px, local assets only.")
    print(f"Largest measured compressed frame: {metrics['maxZlibBytes']} / {metrics['firmwareZlibLimit']} bytes.")


if __name__ == "__main__":
    main()
