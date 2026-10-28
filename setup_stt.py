#!/usr/bin/env python3
"""Download a pinned local Whisper model; recordings never leave the machine."""
import argparse
from listen import DEFAULT_MODEL

REVISION = "3d3d5dee26484f91867d81cb899cfcf72b96be6c"

def main():
    from huggingface_hub import snapshot_download
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", default=str(DEFAULT_MODEL))
    args = parser.parse_args()
    snapshot_download("Systran/faster-whisper-base.en", revision=REVISION,
                      local_dir=args.destination,
                      allow_patterns=["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"])
    print("Local STT model ready.")

if __name__ == "__main__":
    main()
