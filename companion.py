#!/usr/bin/env python3
"""Install/run the device companion for desktop T3 and the legacy T3 service."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
DATA = Path.home() / ".local/share/shark-harness"
UNIT = "shark-companion.service"


def unit_argument(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def service_text(python, entrypoint, port):
    command = " ".join(unit_argument(x) for x in [python, entrypoint, "run", "--device", port])
    return f"""[Unit]
Description=T3 circular display and local voice companion
After=t3code.service
PartOf=t3code.service
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStart={command}
Restart=always
RestartSec=3
TimeoutStopSec=12
KillSignal=SIGINT
KillMode=mixed
UMask=0077

[Install]
WantedBy=default.target t3code.service
"""


def stop_child(child):
    if child is None or child.poll() is not None:
        return
    child.send_signal(signal.SIGINT)
    try:
        child.wait(timeout=4)
    except subprocess.TimeoutExpired:
        child.terminate()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill(); child.wait()


def run(device):
    from speech import bridge_request
    stopping = False
    def stop(signum, frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    # Refuse a second bridge/USB owner, including an old manual launch.
    try:
        bridge_request(8874, "/api/status")
    except OSError:
        pass
    else:
        raise RuntimeError("Port 8874 already has a companion. Stop the manual bridge before starting the service.")
    bridge = subprocess.Popen([sys.executable, "-u", str(ROOT / "bridge.py")], cwd=ROOT)
    sender = None
    try:
        deadline = time.monotonic() + 10
        while not stopping:
            if bridge.poll() is not None:
                raise RuntimeError("The bridge exited during startup.")
            try:
                bridge_request(8874, "/api/status")
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError("The bridge did not become ready.")
                time.sleep(.1)
        waiting = False
        while not stopping:
            if bridge.poll() is not None:
                raise RuntimeError("The bridge stopped; restarting the companion.")
            if sender is None or sender.poll() is not None:
                if Path(device).exists():
                    sender = subprocess.Popen([sys.executable, "-u", str(ROOT / "device.py"),
                        "--port", device, "watch", "--device-ui"], cwd=ROOT)
                    waiting = False
                    time.sleep(2)
                elif not waiting:
                    print("Waiting for the USB device; the companion will reconnect automatically.", flush=True)
                    waiting = True
            time.sleep(.25)
    finally:
        stop_child(sender)
        stop_child(bridge)


def runtime_files(root=ROOT):
    """Files needed by the versioned companion, including UI assets and notices."""
    root = Path(root)
    files = [*root.glob("*.py"), *root.glob("requirements*.txt")]
    for directory in ("web", "assets", "characters"):
        files.extend(path for path in (root / directory).rglob("*") if path.is_file())
    files.extend(root / name for name in ("LICENSE", "NOTICE") if (root / name).is_file())
    return sorted(files)


def install(python, device):
    python = Path(python).absolute()
    subprocess.run([str(python), "-c", "import serial, PIL, faster_whisper, websocket, piper"], check=True)
    from listen import DEFAULT_MODEL
    from speech import DEFAULT_VOICE
    from t3_session import DEFAULT_TOKEN
    for path in [DEFAULT_MODEL / "model.bin", DEFAULT_VOICE, DEFAULT_TOKEN]:
        if not path.is_file(): raise ValueError(f"Install the local dependency first: {path}")
    files = runtime_files()
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(ROOT)).encode()); digest.update(path.read_bytes())
    release = DATA / "installs" / digest.hexdigest()[:16]
    release.mkdir(parents=True, exist_ok=True)
    for path in files:
        destination = release / path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    current = DATA / "current"
    temporary = DATA / ".current-new"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(release, target_is_directory=True)
    os.replace(temporary, current)
    units = Path.home() / ".config/systemd/user"
    units.mkdir(parents=True, exist_ok=True)
    (units / UNIT).write_text(service_text(python, current / "companion.py", device))
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", UNIT], check=True)
    subprocess.run(["systemctl", "--user", "restart", UNIT], check=True)
    print(f"Installed {release.name}. Starts at login and also follows the legacy T3 service.")


def status():
    from speech import bridge_request
    result = bridge_request(8874, "/api/listen")
    target = result.get("target") or {}
    print("Device:", "connected" if result["device"]["connected"] else "waiting for USB")
    print("Session:", target.get("title", "waiting for T3 sessions"))
    print("Voice:", "release to send" if result.get("autoSend") else "review before sending")
    print("Microphone:", "enabled" if result["enabled"] else "paused")
    job = result.get("job")
    if job:
        print("Last prompt:", job["state"])
        if job.get("error"): print("Details:", job["error"])
    print("Controls: swipe or tap arrows; + New starts a draft; hold, speak, release.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["install", "run", "start", "stop", "restart", "status"])
    parser.add_argument("--device", default=os.environ.get("SHARK_DEVICE_PORT"),
                        help="USB serial path (or set SHARK_DEVICE_PORT); required for run/install.")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    if args.command in {"run", "install"} and not (args.device and args.device.strip()):
        parser.error("run/install require --device PATH or SHARK_DEVICE_PORT; use the device's stable /dev/serial/by-id/ path.")
    try:
        if args.command == "run": run(args.device)
        elif args.command == "install": install(args.python, args.device)
        elif args.command == "status": status()
        else:
            subprocess.run(["systemctl", "--user", args.command, UNIT], check=True)
            print(f"Companion {args.command} requested.")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Companion: {error}\n")

if __name__ == "__main__":
    main()
