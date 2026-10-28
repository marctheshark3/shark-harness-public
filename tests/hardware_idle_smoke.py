"""Opt-in USB idle checks. Stop the companion and leave the controls untouched.

Uses synthetic images, keeps the microphone disabled, and never plays audio.
Physical tap/BOOT wake still requires a person; native C tests cover its logic.
"""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from device import FRAME_BYTES, connect  # noqa: E402
from device_ui import pixels565, render  # noqa: E402
from hardware_smoke import rejected  # noqa: E402


def wait_status(display, predicate, *, heartbeat=True, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if heartbeat:
            display.request("ui.configure", enabled=True)
        status = display.request("status")
        if predicate(status):
            return status
        time.sleep(.05)
    raise AssertionError("Device did not reach the expected idle/presentation state")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    args = parser.parse_args()
    pixels = pixels565(render(dict(kind="home", title="Checking sleep", project="Device check",
                                  index=1, total=1, enabled=True, autoSend=False)))
    with connect(args.port) as display:
        initial = display.request("status")
        assert initial["ready"] and initial["ui"].get("idleMode") is True
        assert not any(initial["mic"].get(k) for k in ("recording", "available")), "Microphone is in use"
        assert not any(initial["audio"].get(k) for k in ("uploading", "armed", "playing")), "Speaker is in use"
        display.request("mic.configure", enabled=False)
        try:
            display.request("ui.configure", enabled=False)
            rejected(display, "ui.idle", "ui_inactive", enabled=True)
            rejected(display, "ui.idle", "invalid_ui_idle", enabled=1)
            display.request("ui.configure", enabled=True)
            display.send_frame(pixels, seconds=30, ui_token=801, hold_to_talk=True)
            awake = wait_status(display, lambda s: s["ui"]["frame"] == "00000321"
                                and s["ui"]["brightness"] == 25 and not s["ui"]["wakePending"])
            # Neither incomplete images nor pending speaker uploads may rest.
            display.request("frame.begin", bytes=FRAME_BYTES, crc32="00000000")
            rejected(display, "ui.idle", "ui_idle_busy", enabled=True)
            display.request("clear")
            display.send_frame(pixels, seconds=30, ui_token=801, hold_to_talk=True)
            wait_status(display, lambda s: s["ui"]["frame"] == "00000321")
            display.request("audio.begin", bytes=2, sampleRate=22050, crc32="00000000")
            rejected(display, "ui.idle", "ui_idle_busy", enabled=True)
            display.request("audio.stop")

            display.request("ui.idle", enabled=True)
            resting = wait_status(display, lambda s: s["ui"]["idle"] and s["ui"]["brightness"] == 6)
            assert resting["ui"]["frame"] == "00000000" and not resting["ui"]["holdToTalk"]
            animated = wait_status(display, lambda s: s["frames"] > resting["frames"] + 2)
            assert animated["ui"]["idle"] and not animated["mic"]["recording"]

            display.request("ui.idle", enabled=False)
            restoring = wait_status(display, lambda s: s["ui"]["brightness"] == 25)
            assert restoring["ui"]["wakePending"] and not restoring["ui"]["holdToTalk"]
            time.sleep(.25)
            assert display.request("status")["ui"]["wakePending"], "Retained image rearmed stale controls"
            display.send_frame(pixels, seconds=30, ui_token=802, hold_to_talk=True)
            restored = wait_status(display, lambda s: s["ui"]["frame"] == "00000322"
                                   and not s["ui"]["wakePending"])
            assert restored["ui"]["holdToTalk"] and restored["ui"]["brightness"] == 25
            assert restored["ui"]["wake"] == awake["ui"]["wake"]
            assert restored["ui"]["event"] == awake["ui"]["event"]
            assert restored["mic"]["id"] == awake["mic"]["id"] and not restored["mic"]["recording"]

            display.request("ui.configure", enabled=True)
            display.request("ui.idle", enabled=True)
            time.sleep(5.3)  # Status reads do not renew the UI's five-second lease.
            disconnected = wait_status(display, lambda s: not s["ui"]["active"]
                                       and not s["ui"]["idle"] and s["ui"]["brightness"] == 25,
                                       heartbeat=False)
            assert disconnected["ui"]["frame"] == "00000000"
            display.request("ui.configure", enabled=True)
            display.send_frame(pixels, seconds=30, ui_token=803, hold_to_talk=False)
            reconnected = wait_status(display, lambda s: s["ui"]["frame"] == "00000323")
            assert not reconnected["ui"]["holdToTalk"] and not reconnected["mic"]["recording"]
            print(json.dumps({"ok": True, "checks": ["idle validation and busy guards",
                "6 percent dim animation with USB progress", "25 percent wake brightness",
                "fresh image required before rearming", "no microphone capture or input actions",
                "heartbeat expiry and UI recovery"], "physicalWakeTested": False}))
        finally:
            display.request("ui.idle", enabled=False)
            display.request("ui.configure", enabled=False)
            display.request("mic.configure", enabled=False)
            display.request("clear")


if __name__ == "__main__":
    main()
