"""Measure live device controls without taking ownership of USB or recording speech.

During the window, make at least 20 arrow/Pause/Resume inputs, including pauses
of a few seconds and a burst of quick taps. Include swipes if testing navigation.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen


def hardware(port):
    with urlopen(f"http://127.0.0.1:{port}/api/listen", timeout=2) as response:
        return json.load(response)["device"]


def summarize(records, before, after, errors, target_ms=200, minimum=20):
    inputs = [r for r in records if r["kind"] == "Input"]
    candidates = [r for r in inputs if r["gesture"] in (2, 3, 5) or (r["gesture"] == 1 and (
        ((r["x"] <= 96 or r["x"] >= 370) and 130 <= r["y"] <= 315)
        or (46 <= r["x"] <= 420 and 327 <= r["y"] <= 452)
        or (r.get("screenKind") == "home" and 90 <= r["x"] <= 376 and 45 <= r["y"] <= 126)
        or (r.get("screenKind") == "projects" and 100 <= r["x"] <= 366 and 120 <= r["y"] <= 315)))]
    responses = [r for r in records if r["kind"] == "Response"]
    times = sorted(r["releaseToDisplayMs"] for r in responses if "releaseToDisplayMs" in r)
    def percentile(p): return times[max(0, math.ceil(len(times) * p) - 1)] if times else None
    counters = {k: after.get(k, 0) - before.get(k, 0) for k in ("contacts", "releases", "tapDrops", "dropped")}
    missed = sum(not r.get("action") for r in candidates) + max(0, counters["tapDrops"]) + max(0, counters["dropped"])
    enough = len(times) >= minimum
    failed = bool(errors or missed or any(v < 0 for v in counters.values())
                  or (times and (percentile(.95) > target_ms or times[-1] > 500)))
    return {"status": "fail" if failed else "pass" if enough else "insufficient_samples",
            "targets": {"minimumSamples": minimum, "p95Ms": target_ms, "maximumMs": 500, "missedInputs": 0},
            "completedResponses": len(times), "candidateInputs": len(candidates), "missedInputs": missed,
            "ignoredNonControlTouches": sum(not r.get("action") for r in inputs if r not in candidates),
            "coalescedResponses": sum(bool(r.get("coalesced")) for r in responses),
            "p50Ms": percentile(.5), "p95Ms": percentile(.95), "maximumMs": times[-1] if times else None,
            "counters": counters, "transportErrors": errors,
            "timingScope": "Firmware release detection to completed panel draw; physical sensing delay is additional."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=90)
    parser.add_argument("--port", type=int, default=8874)
    parser.add_argument("--output", type=Path, default=Path("artifacts/device/controls-measurement.json"))
    args = parser.parse_args()
    if not 1 <= args.seconds <= 600: parser.error("seconds must be 1–600")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    before = hardware(args.port).get("ui", {})
    samples, errors, last = [], [], None
    end = time.monotonic() + args.seconds
    print(f"Measuring live controls for {args.seconds}s. Aim for 20 arrow/Pause/Resume inputs.", flush=True)
    while time.monotonic() < end:
        try:
            d = hardware(args.port)
            sample = {"touch": d.get("touch"), "connected": d.get("connected"),
                      "contacts": d.get("ui", {}).get("contacts"), "releases": d.get("ui", {}).get("releases")}
            if sample != last: samples.append({"time": time.time(), **sample}); last = sample
        except OSError as error:
            errors.append(type(error).__name__)
        time.sleep(.05)
    after = hardware(args.port).get("ui", {})
    # Keep the raw capture even if journal access is unavailable on this host.
    args.output.write_text(json.dumps({"status": "collecting_logs", "startedAt": started,
        "before": before, "after": after, "touchSamples": samples}, indent=2) + "\n")
    logs = subprocess.check_output(["journalctl", "--user", "-u", "shark-companion.service",
                                   "--since", started, "--no-pager", "-o", "cat"], text=True)
    records = []
    for line in logs.splitlines():
        for kind in ("Input", "Response"):
            if line.startswith(kind + ": "):
                records.append({"kind": kind, **json.loads(line[len(kind) + 2:])})
        if line.startswith("Device:"): errors.append(line)
    report = summarize(records, before, after, errors)
    args.output.write_text(json.dumps({**report, "startedAt": started, "seconds": args.seconds,
                                       "records": records, "touchSamples": samples}, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    print(f"Saved {args.output}", flush=True)


if __name__ == "__main__": main()
