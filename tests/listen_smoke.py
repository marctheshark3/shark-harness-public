"""Real local Piper -> 16 kHz PCM -> local Whisper, without hardware or a T3 send."""
import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from listen import LocalTranscriber, DEFAULT_MODEL, RATE
from speech import LocalVoice, DEFAULT_VOICE


def main():
    import numpy as np
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice", type=Path, default=DEFAULT_VOICE)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    args = parser.parse_args()
    source = LocalVoice(args.voice).synthesize("Please check the project and tell me which tests are failing.")
    samples = np.frombuffer(source, dtype="<i2")
    pcm = np.interp(np.arange(0, len(samples), 22050 / 16000), np.arange(len(samples)), samples).astype("<i2").tobytes()
    transcriber = LocalTranscriber(args.model)
    started = time.monotonic()
    result = transcriber.transcribe(pcm).lower()
    assert all(word in result for word in ("project", "tests", "failing")), result
    assert transcriber.transcribe(bytes(32000)) == ""
    # The same thought before and after a pause longer than the former capture
    # limit must both survive local transcription.
    extended = pcm + bytes(RATE * 2 * 35) + pcm
    result = transcriber.transcribe(extended).lower()
    assert all(result.count(word) >= 2 for word in ("project", "tests", "failing")), result
    assert transcriber.transcribe(bytes(RATE * 2 * 180)) == ""
    # Low-level room noise must not be promoted to a prompt.
    noise = np.random.default_rng(0).normal(0, 12, 16000).astype("<i2").tobytes()
    assert transcriber.transcribe(noise) == ""
    print(f"Real local speech-to-text, long recording with a pause, silence, and quiet-noise checks passed in {time.monotonic() - started:.2f}s.")

if __name__ == "__main__":
    main()
