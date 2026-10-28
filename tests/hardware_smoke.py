"""Opt-in real USB check: briefly displays an image, then returns to ambient.

Stop device.py watch first. This never flashes firmware or reads private flash.
python tests/hardware_smoke.py --port /dev/serial/by-id/... --image example.png
"""

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from device import connect, FRAME_BYTES, image_pixels, ProtocolError  # noqa: E402


def rejected(display, operation, expected, **fields):
    try:
        display.request(operation, **fields)
    except ProtocolError as error:
        assert expected in str(error), error
    else:
        raise AssertionError(f"Device accepted invalid {operation}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--image", type=Path, required=True)
    args = parser.parse_args()
    pixels = image_pixels(args.image)
    with connect(args.port) as display:
        display.request("clear")
        initial = display.request("status")
        assert initial["ready"] and not initial["imageActive"], initial
        rejected(display, "frame.begin", "invalid_frame", bytes=1, crc32="00000000")
        rejected(display, "frame.begin", "invalid_frame", bytes=FRAME_BYTES, crc32="-0000001")
        display.request("frame.begin", bytes=FRAME_BYTES, crc32="00000000")
        rejected(display, "frame.chunk", "invalid_offset", offset=1, data="AAAA")
        rejected(display, "frame.chunk", "invalid_chunk", offset=0, data="!invalid!")
        rejected(display, "frame.end", "incomplete_frame", seconds=1)
        display.request("clear")
        uploaded = display.send_frame(pixels, seconds=1)
        assert uploaded["imageActive"], uploaded
        time.sleep(1.5)
        expired = display.request("status")
        assert expired["ready"] and not expired["imageActive"], expired
        assert expired["frames"] > initial["frames"], (initial, expired)
        # Reopening must not reset the chip (DTR/RTS are deliberately controlled).
        previous_frames = expired["frames"]
    time.sleep(0.5)
    with connect(args.port) as display:
        reopened = display.request("status")
        assert reopened["frames"] > previous_frames, reopened
    print(json.dumps({"ok": True, "uploadSeconds": uploaded["uploadSeconds"],
                      "crc32": uploaded["crc32"], "frames": reopened["frames"],
                      "checks": ["identity", "invalid requests", "image checksum",
                                 "local expiry", "renderer progress", "reopen without reset"]}, indent=2))


if __name__ == "__main__":
    main()
