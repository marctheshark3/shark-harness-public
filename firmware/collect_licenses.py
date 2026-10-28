#!/usr/bin/env python3
"""Collect upstream notices from the pinned firmware build inputs."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


NOTICE_NAME = re.compile(r"^(LICENSE|LICENCE|COPYING|NOTICE|COPYRIGHT)([._-].*)?$", re.I)


def notice_files(root):
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"Missing dependency directory: {root}")
    found = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ".git" in relative.parts or not path.is_file():
            continue
        if not (NOTICE_NAME.match(path.name) or path.suffix.lower() == ".license"
                or any(part.lower() in {"licenses", "licences"} for part in relative.parts[:-1])):
            continue
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"Notice symlink leaves dependency directory: {relative}")
        found.append(path)
    return found


def collect(idf_root, managed_root, output, *, image="unknown"):
    idf_root, managed_root, output = (Path(p).resolve() for p in (idf_root, managed_root, output))
    for root in (idf_root, managed_root):
        if output.is_relative_to(root) or root.is_relative_to(output):
            raise ValueError("Notice output must be separate from dependency inputs.")
    if not (idf_root / "LICENSE").is_file():
        raise ValueError("The ESP-IDF root license is missing.")
    if not managed_root.is_dir():
        raise ValueError("Managed components are missing; build firmware first.")
    components = [p for p in sorted(managed_root.iterdir()) if p.is_dir() and not p.name.startswith(".")]
    if not components:
        raise ValueError("No managed components were found.")

    sources = [("esp-idf", idf_root), *[(f"managed_components/{p.name}", p) for p in components]]
    inputs = []
    for label, root in sources:
        notices = notice_files(root)
        if not notices:
            raise ValueError(f"No upstream license or notice files found for {label}.")
        inputs.extend((label, root, path) for path in notices)

    try:
        revision = subprocess.check_output(
            ["git", "-C", str(idf_root), "rev-parse", "HEAD"], stderr=subprocess.DEVNULL,
            text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "unknown"

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".notices-", dir=output.parent) as temporary:
        staging = Path(temporary) / "licenses"
        staging.mkdir()
        records = []
        for label, root, path in inputs:
            relative = Path(label) / path.relative_to(root)
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            records.append({"path": relative.as_posix(),
                            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()})
        manifest = {"idfImage": image, "idfGitRevision": revision,
                    "scope": "Standalone notices from ESP-IDF and all managed components; may include unlinked components.",
                    "files": records}
        (staging / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if output.exists():
            shutil.rmtree(output)
        staging.rename(output)
    return len(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idf-root", required=True, type=Path)
    parser.add_argument("--idf-image", required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parent
    count = collect(args.idf_root, project / "managed_components", project / "build/release/licenses",
                    image=args.idf_image)
    print(f"Collected {count} upstream notice files.")


if __name__ == "__main__":
    main()
