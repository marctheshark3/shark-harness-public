"""Opt-in USB audio checks, muted by default. Stop device.py watch first."""
import argparse
import base64
import json
import math
from pathlib import Path
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from device import connect  # noqa: E402
from speech import MAX_BYTES, SAMPLE_RATE  # noqa: E402
from hardware_smoke import rejected  # noqa: E402


def finished(display, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = display.request("status")
        if not status["audio"]["playing"]:
            return status
        time.sleep(0.05)
    raise AssertionError("Speaker did not stop")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    args = parser.parse_args()
    pcm = b"".join(struct.pack("<h", round(math.sin(i * math.tau * 440 / SAMPLE_RATE) * 2000))
                   for i in range(SAMPLE_RATE * 2))
    with connect(args.port) as display:
        original = display.request("audio.stop")
        finished(display)
        assert original["audio"]["ready"], original
        try:
            display.request("audio.volume", volume=0)
            for size, rate, crc in [(1, SAMPLE_RATE, "00000000"), (MAX_BYTES + 2, SAMPLE_RATE, "00000000"),
                                    (2, 16000, "00000000"), (2, SAMPLE_RATE, "-0000001")]:
                rejected(display, "audio.begin", "invalid_audio", bytes=size, sampleRate=rate, crc32=crc)
            rejected(display, "audio.volume", "invalid_volume", volume=81)
            rejected(display, "audio.volume", "invalid_volume", volume=True)
            display.request("audio.begin", bytes=2, sampleRate=SAMPLE_RATE, crc32="00000000")
            rejected(display, "audio.chunk", "invalid_audio_offset", offset=1, data="AAA=")
            rejected(display, "audio.chunk", "invalid_audio_chunk", offset=0, data="!invalid!")
            rejected(display, "audio.chunk", "invalid_audio_chunk", offset=0, data="AAAA")
            rejected(display, "audio.end", "incomplete_audio")
            display.request("audio.chunk", offset=0, data=base64.b64encode(b"\x00\x00").decode())
            rejected(display, "audio.end", "audio_crc_mismatch")
            assert not display.request("status")["audio"]["playing"]

            for repeat in range(2):
                list(display.audio_chunks(pcm))
                started = display.request("status")
                assert started["audio"]["playing"] and started["audio"]["volume"] == 0, started
                rejected(display, "audio.begin", "audio_busy", bytes=2, sampleRate=SAMPLE_RATE, crc32="00000000")
                done = finished(display)
                assert done["audio"]["played"] == len(pcm) and not done["audio"]["error"], done
                assert not done["audio"]["stopped"], done
                assert done["frames"] > started["frames"] and done["touch"]["ready"], done
                assert done["touch"]["error"] == 0, done

            list(display.audio_chunks(pcm * 5))
            time.sleep(0.3)
            stopped_at = time.monotonic()
            display.request("audio.stop")
            stopped = finished(display)
            stop_seconds = time.monotonic() - stopped_at
            assert stopped["audio"]["stopped"] and stopped["audio"]["played"] < len(pcm) * 5, stopped
            assert stop_seconds < 1, stop_seconds
            # Preloaded tap mode is verified and silent until a physical tap.
            list(display.audio_chunks(pcm, on_tap=True))
            armed = display.request("status")
            assert armed["audio"]["armed"] and not armed["audio"]["playing"], armed
            assert armed["audio"]["played"] == 0, armed
            time.sleep(0.3)
            assert display.request("status")["audio"]["armed"]
            display.request("audio.stop")
            assert not display.request("status")["audio"]["armed"]
            # A cancelled partial upload cannot play, even if end arrives late.
            upload = display.audio_chunks(pcm)
            next(upload); next(upload)
            display.request("audio.stop")
            rejected(display, "audio.end", "incomplete_audio")
            upload.close()
            print(json.dumps({"ok": True, "muted": True, "stopSeconds": round(stop_seconds, 3),
                              "checks": ["format and bounds", "CRC rejection", "two complete playbacks",
                                         "mute", "stop", "preload waits for tap", "cancel upload", "display and touch progress"],
                              "audio": stopped["audio"]}, indent=2))
        finally:
            display.request("audio.stop")
            display.request("audio.volume", volume=original["audio"]["volume"])


if __name__ == "__main__":
    main()
