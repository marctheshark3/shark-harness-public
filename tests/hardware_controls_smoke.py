"""Opt-in USB stress check for persistent home controls. Stop the companion first."""
import argparse
import base64
import json
from pathlib import Path
import sys
import time
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from device import connect
from device_ui import pixels565, render
from hardware_smoke import rejected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    args = parser.parse_args()
    with connect(args.port) as display:
        display.request("mic.configure", enabled=False)
        display.request("ui.configure", enabled=True)
        template = dict(kind="home", title="Control check", index=1, total=20,
                        enabled=False, autoSend=True)
        try:
            display.send_frame(pixels565(render(template)), ui_token=101, seconds=30)
            deadline = time.monotonic() + 3
            while display.request("status")["ui"]["frame"] != "00000065":
                assert time.monotonic() < deadline
                time.sleep(.01)
            started = time.monotonic()
            reads = 0
            for i in range(20):
                pixels = pixels565(render({**template, "title": f"Control check {i + 1}", "index": i + 1}))
                payload = zlib.compress(pixels, 6)
                result = display.request("frame.begin", bytes=len(payload), encoding="zlib565",
                                         crc32=f"{zlib.crc32(pixels):08x}", uiToken=101)
                assert result["ui"]["frame"] == "00000065", "Home controls disabled by replacement upload"
                for offset in range(0, len(payload), display.frame_chunk_bytes):
                    result = display.request("frame.chunk", offset=offset,
                        data=base64.b64encode(payload[offset:offset + display.frame_chunk_bytes]).decode())
                    assert result["ui"]["frame"] == "00000065"
                display.request("frame.end", seconds=30)
                for _ in range(20):
                    result = display.request("status")
                    assert result["ui"]["frame"] == "00000065"
                    assert result["ui"]["eventQueue"]
                    events = result["ui"]["events"]
                    if events:
                        display.request("ui.ack", event=events[-1]["event"])
                    display.request("ui.configure", enabled=True)
                    display.request("mic.configure", enabled=False)
                    reads += 1
            # A different control context must still invalidate stale gestures.
            result = display.request("frame.begin", bytes=len(payload), encoding="zlib565",
                                     crc32=f"{zlib.crc32(pixels):08x}", uiToken=102)
            assert result["ui"]["frame"] == "00000000"
            for token, enabled in [(103, False), (104, True)]:
                display.send_frame(pixels, ui_token=token, hold_to_talk=enabled, seconds=30)
                deadline = time.monotonic() + 3
                while True:
                    result = display.request("status")
                    if result["ui"]["frame"] == f"{token:08x}": break
                    assert time.monotonic() < deadline
                    time.sleep(.01)
                assert result["ui"]["voiceGate"]
                assert result["ui"]["holdToTalk"] is enabled
            rejected(display, "frame.begin", "invalid_frame", bytes=len(payload), encoding="zlib565",
                     crc32=f"{zlib.crc32(pixels):08x}", holdToTalk=1)
            print(json.dumps({"ok": True, "frames": 20, "statusReads": reads,
                "seconds": round(time.monotonic() - started, 2),
                "checks": ["home controls stay active during upload", "new context invalidates input",
                           "queued event acknowledgement", "1200 poll/configure requests without timeout",
                           "project picker hold-to-talk gating", "invalid voice flag rejected"]}))
        finally:
            display.request("ui.configure", enabled=False)
            display.request("clear")


if __name__ == "__main__": main()
