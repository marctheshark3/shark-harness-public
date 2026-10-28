"""Opt-in real microphone checks. Captures briefly in RAM; never saves or sends speech."""
import argparse
import json
from pathlib import Path
import struct
import sys
import time
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from device import connect
from listen import MAX_CAPTURE
from hardware_smoke import rejected
from hardware_audio_smoke import finished


def stopped(display, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = display.request("status")
        if not result["mic"]["recording"]:
            return result
        time.sleep(.05)
    raise AssertionError("Microphone did not stop")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--stream-seconds", type=int, default=0,
                        help="Also verify a streaming capture longer than 30 seconds (31–300).")
    args = parser.parse_args()
    if args.stream_seconds and not 31 <= args.stream_seconds <= 300:
        parser.error("--stream-seconds must be 31–300")
    with connect(args.port) as display:
        original = display.request("audio.stop")
        finished(display)
        try:
            display.request("mic.configure", enabled=False)
            display.request("mic.clear")
            rejected(display, "mic.start", "microphone_disabled")
            rejected(display, "mic.configure", "invalid_microphone_config", enabled=1)
            display.request("mic.configure", enabled=True)
            start = display.request("mic.start")
            rejected(display, "audio.begin", "audio_busy", bytes=2, sampleRate=22050, crc32="00000000")
            rejected(display, "mic.read", "invalid_capture", captureId=start["mic"]["id"] + 1, offset=0)
            time.sleep(1)
            display.request("mic.stop")
            end = stopped(display)
            mic = end["mic"]
            assert mic["available"] and mic["error"] == 0, mic
            assert end["frames"] > start["frames"] and end["touch"]["ready"]
            pcm = display.capture_audio(mic)
            samples = list(struct.iter_unpack("<h", pcm))
            peak = max(abs(v[0]) for v in samples)
            assert peak > 0, "ADC returned only zeros"
            rejected(display, "mic.read", "invalid_capture", captureId=mic["id"] + 1, offset=0)
            rejected(display, "mic.read", "invalid_capture", captureId=mic["id"], offset=1)
            display.request("mic.clear")
            rejected(display, "mic.read", "invalid_capture", captureId=mic["id"], offset=0)
            stream_bytes = 0
            if args.stream_seconds:
                assert mic.get("streaming") is True, "Update the firmware for streaming capture"
                display.request("mic.configure", enabled=True)
                capture_id = display.request("mic.start")["mic"]["id"]
                streamed = bytearray()
                deadline = time.monotonic() + args.stream_seconds
                while time.monotonic() < deadline:
                    live = display.request("mic.configure", enabled=True)["mic"]
                    assert live["recording"] and live["id"] == capture_id, live
                    while len(streamed) < live["bytes"]:
                        streamed.extend(display.capture_chunk(live, len(streamed)))
                    time.sleep(.05)
                display.request("mic.stop")
                completed = stopped(display)["mic"]
                assert completed["available"] and completed["error"] == 0, completed
                while len(streamed) < completed["bytes"]:
                    streamed.extend(display.capture_chunk(completed, len(streamed)))
                assert len(streamed) > MAX_CAPTURE
                assert f"{zlib.crc32(streamed):08x}" == completed["crc32"]
                stream_bytes = len(streamed)
                streamed.clear()
                display.request("mic.clear")
            # Heartbeat loss must disable capture and discard the unfinished clip.
            display.request("mic.configure", enabled=True)
            display.request("mic.start")
            time.sleep(5.3)
            abandoned = stopped(display)["mic"]
            assert not abandoned["enabled"] and not abandoned["available"] and abandoned["bytes"] == 0, abandoned
            # Switching the shared clock back to 22.05 kHz must preserve playback.
            display.request("audio.volume", volume=0)
            list(display.audio_chunks(bytes(22050)))
            speaker = finished(display)["audio"]
            assert speaker["played"] == 22050 and speaker["error"] == 0, speaker
            print(json.dumps({"ok": True, "captureBytes": len(pcm), "peak": peak,
                "streamSeconds": args.stream_seconds, "streamBytes": stream_bytes,
                "checks": ["explicit enable", "half-duplex exclusion", "CRC download", "stale id rejection",
                           "clear", "heartbeat expiry discards", "speaker after capture", "display progress"]}))
        finally:
            display.request("mic.configure", enabled=False)
            display.request("mic.clear")
            display.request("audio.stop")
            display.request("audio.volume", volume=original["audio"]["volume"])

if __name__ == "__main__":
    main()
