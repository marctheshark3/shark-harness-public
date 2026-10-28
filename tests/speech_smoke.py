"""Synthesize with the real local Piper voice, then verify the USB encoding.

No device or network is used by this check; install the voice first.
"""
import argparse
import hashlib
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from device import Display  # noqa: E402
from speech import DEFAULT_VOICE, LocalVoice, MAX_BYTES  # noqa: E402
from test_device import Transport  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice", type=Path, default=DEFAULT_VOICE)
    args = parser.parse_args()
    # Pin the exact voice/config tested on hardware. A custom voice is a separate
    # local choice; this CI check deliberately exercises our documented default.
    hashes = {"": "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
              ".json": "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0"}
    for suffix, expected in hashes.items():
        assert hashlib.sha256(Path(str(args.voice) + suffix).read_bytes()).hexdigest() == expected
    voice = LocalVoice(args.voice)
    pcm = voice.synthesize("T three is ready. Your display has a local voice.")
    assert 4410 < len(pcm) <= MAX_BYTES and len(pcm) % 2 == 0
    samples = [value[0] for value in struct.iter_unpack("<h", pcm)]
    assert min(samples) < -500 and max(samples) > 500, "Speech is silent or malformed"
    transport = Transport()
    display = Display(transport)
    display.request("hello")
    list(display.audio_chunks(pcm))
    assert bytes(transport.pcm) == pcm
    # The loaded model is reused; overlong speech fails before USB.
    assert voice.synthesize("Ready.")
    try:
        voice.synthesize("Hello there. " * 30)
    except ValueError as error:
        assert "20 seconds" in str(error), error
    else:
        raise AssertionError("Overlong speech was accepted")
    print(f"Piper voice, PCM format, duration limit, and USB encoding passed ({len(pcm) / 44100:.2f} s).")


if __name__ == "__main__":
    main()
