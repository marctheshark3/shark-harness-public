"""Opt-in display latency and bounded decompression checks. Stop the companion first."""
import argparse
import base64
import json
from pathlib import Path
import statistics
import sys
import time
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from device import connect, FRAME_BYTES, CHUNK_BYTES
from device_ui import render, pixels565
from hardware_smoke import rejected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    args = parser.parse_args()
    timings, uploads, sizes = [], [], []
    with connect(args.port) as display:
        assert display.frame_zlib_max == 65536, "Install firmware with zlib frame support."
        display.request("mic.configure", enabled=False)
        try:
            # Warm the reusable background; each iteration still draws different text.
            template = dict(kind="home", project="T3 diagnostics", title="Response check",
                            index=1, total=6, enabled=False, autoSend=True)
            render(template)
            for i in range(6):
                display.request("ui.configure", enabled=True)
                started = time.perf_counter()
                pixels = pixels565(render({**template, "title": f"Response check {i + 1}", "index": i + 1}))
                result = display.send_frame(pixels, ui_token=1000 + i, seconds=30,
                    service=lambda: display.request("ui.configure", enabled=True))
                deadline = time.monotonic() + 3
                while display.request("status")["ui"]["frame"] != f"{1000 + i:08x}":
                    assert time.monotonic() < deadline, "Panel did not finish rendering."
                    time.sleep(.005)
                timings.append(time.perf_counter() - started)
                uploads.append(result["uploadSeconds"])
                sizes.append(result["transferBytes"])
            checksum = display.request("status")["crc32"]
            valid = zlib.compress(bytes(FRAME_BYTES))
            malformed = [b"invalid", valid[:-1], valid[:-1] + bytes([valid[-1] ^ 1]),
                         valid + b"trailing", zlib.compress(bytes(FRAME_BYTES - 2)),
                         zlib.compress(bytes(FRAME_BYTES + 2))]
            for payload in malformed + [valid]:
                display.request("frame.begin", bytes=len(payload), encoding="zlib565", crc32="ffffffff")
                for offset in range(0, len(payload), CHUNK_BYTES):
                    display.request("frame.chunk", offset=offset,
                        data=base64.b64encode(payload[offset:offset + CHUNK_BYTES]).decode())
                rejected(display, "frame.end", "crc_mismatch" if payload == valid else "invalid_zlib", seconds=30)
                assert display.request("status")["crc32"] == checksum, "Invalid frame replaced visible pixels."
            rejected(display, "frame.begin", "invalid_frame", bytes=65537, encoding="zlib565", crc32="00000000")
            print(json.dumps({"ok": True, "renderedSeconds": [round(t, 3) for t in timings],
                "medianRenderedSeconds": round(statistics.median(timings), 3), "uploadSeconds": uploads,
                "transferBytes": sizes, "checks": ["full panel presentation", "bad stream", "truncation",
                "Adler checksum", "trailing bytes", "decoded size and overflow", "CRC", "encoded size cap"]}))
        finally:
            display.request("ui.configure", enabled=False)
            display.request("clear")


if __name__ == "__main__": main()
