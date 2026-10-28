"""Compile and run the production firmware input path without USB or hardware."""
import ctypes
import functools
import math
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import weakref


class _Event(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in
                ("event", "gesture", "x", "y", "event_frame", "started_ms", "released_ms")]


class _Status(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "active", "pressed", "holdable", "taps", "x", "y", "button_pressed",
        "event", "gesture", "gesture_x", "gesture_y", "event_frame", "frame",
        "idle", "wake", "wake_pending", "voice_ready", "front_voice", "contacts", "releases",
        "tap_drops", "dropped", "event_count", "image_version", "presented_version",
        "presented_ms", "now_ms", "image_active", "error")] + [("events", _Event * 16)]


@functools.lru_cache(maxsize=1)
def _library():
    root = Path(__file__).resolve().parent.parent
    firmware = root / "firmware" / "main"
    temporary = tempfile.TemporaryDirectory(prefix="shark-simulator-")
    output = Path(temporary.name) / "input.so"
    compiler = shlex.split(os.environ.get("CC", "cc"))
    command = [*compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2", "-fPIC", "-shared",
               f"-I{firmware}", str(root / "simulator" / "native.c"),
               *[str(firmware / source) for source in
                 ("input.c", "gesture.c", "tap.c", "idle.c", "voice_ring.c")],
               "-lm", "-o", str(output)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise RuntimeError(f"Native simulator compilation failed:\n{result.stderr.strip()}")
        library = ctypes.CDLL(str(output))
    except OSError as error:
        temporary.cleanup()
        raise RuntimeError("The device simulator requires a C compiler (cc or $CC).") from error
    except Exception:
        temporary.cleanup()
        raise
    pointer, u32, u64, boolean = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_bool
    signatures = {
        "create": (pointer, []), "destroy": (None, [pointer]),
        "configure": (None, [pointer, boolean, u64]),
        "clear": (None, [pointer, u64]),
        "begin_frame": (None, [pointer, u32, u64]),
        "frame": (None, [pointer, u32, boolean, u32, u64]),
        "present": (None, [pointer, u32, u64]),
        "idle": (boolean, [pointer, boolean]),
        "touch": (None, [pointer, u64, u32, ctypes.c_uint16, ctypes.c_uint16, boolean]),
        "button": (None, [pointer, u64, boolean]), "missing": (None, [pointer, u64, boolean]),
        "ack": (None, [pointer, u32]), "status": (None, [pointer, u64, ctypes.POINTER(_Status)]),
        "control": (boolean, [ctypes.c_uint16, ctypes.c_uint16]),
        "coordinates": (boolean, [ctypes.c_uint16, ctypes.c_uint16,
                                   ctypes.POINTER(ctypes.c_uint16), ctypes.POINTER(ctypes.c_uint16)]),
        "ring": (None, [ctypes.POINTER(ctypes.c_uint8), ctypes.c_float, u32, boolean]),
    }
    for name, (restype, argtypes) in signatures.items():
        function = getattr(library, f"shark_sim_{name}")
        function.restype, function.argtypes = restype, argtypes
    # Keep the private build alive with the loaded library; never write binaries
    # into tracked source, the installed companion, or the firmware build tree.
    library._temporary = temporary
    return library


def _integer(value, maximum, name):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{name} must be an integer from 0 to {maximum}")
    return value


class NativeInput:
    """Deterministic firmware input with explicit millisecond time and frame draws.

    report() takes display coordinates by default; raw=True passes CST9217
    coordinates through the same calibrated transform used by the device.
    ui.holdToTalk matches the physical protocol's committed-frame preference;
    ui.holdReady additionally exposes the effective input gate for inspection.
    """

    def __init__(self):
        self._lib = _library()
        self._handle = self._lib.shark_sim_create()
        if not self._handle:
            raise MemoryError("Could not allocate native simulator state")
        self._cleanup = weakref.finalize(self, self._lib.shark_sim_destroy, self._handle)
        self.now_ms = 0
        self.version = 0

    def close(self):
        self._cleanup()
        self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()

    def _time(self, now_ms):
        if self._handle is None:
            raise RuntimeError("NativeInput is closed")
        now_ms = self.now_ms if now_ms is None else _integer(now_ms, 2**64 - 1, "now_ms")
        if now_ms < self.now_ms:
            raise ValueError("Native simulator time must not move backwards")
        self.now_ms = now_ms
        return now_ms

    def configure(self, enabled, now_ms=None):
        self._lib.shark_sim_configure(self._handle, bool(enabled), self._time(now_ms))
        return self.status()

    def begin_frame(self, token, now_ms=None):
        self._lib.shark_sim_begin_frame(self._handle, _integer(token, 2**32 - 1, "token"), self._time(now_ms))
        return self.status()

    def clear(self, now_ms=None):
        self._lib.shark_sim_clear(self._handle, self._time(now_ms))
        return self.status()

    def frame(self, token, *, voice=True, version=None, presented=True, now_ms=None):
        now = self._time(now_ms)
        version = (self.version + 1) % 2**32 if version is None else _integer(version, 2**32 - 1, "version")
        self._lib.shark_sim_frame(self._handle, _integer(token, 2**32 - 1, "token"), bool(voice), version, now)
        self.version = version
        if presented:
            self._lib.shark_sim_present(self._handle, version, now)
        return self.status()

    def present(self, version, now_ms=None):
        self._lib.shark_sim_present(self._handle, _integer(version, 2**32 - 1, "version"), self._time(now_ms))
        return self.status()

    def idle(self, enabled):
        self._time(None)
        return bool(self._lib.shark_sim_idle(self._handle, bool(enabled)))

    def report(self, now_ms, points, x=0, y=0, *, raw=False):
        self._lib.shark_sim_touch(self._handle, self._time(now_ms), _integer(points, 255, "points"),
                                 _integer(x, 65535, "x"), _integer(y, 65535, "y"), bool(raw))
        return self.status()

    def button(self, now_ms, pressed):
        self._lib.shark_sim_button(self._handle, self._time(now_ms), bool(pressed))
        return self.status()

    def interrupt(self, now_ms, *, error=False):
        self._lib.shark_sim_missing(self._handle, self._time(now_ms), bool(error))
        return self.status()

    def ack(self, event):
        self._time(None)
        self._lib.shark_sim_ack(self._handle, _integer(event, 1000000, "event"))
        return self.status()

    def status(self, now_ms=None):
        native = _Status()
        self._lib.shark_sim_status(self._handle, self._time(now_ms), ctypes.byref(native))
        events = [{"event": event.event, "gesture": event.gesture, "x": event.x, "y": event.y,
                   "eventFrame": f"{event.event_frame:08x}", "startedMs": event.started_ms,
                   "releasedMs": event.released_ms} for event in native.events[:native.event_count]]
        return {
            "touch": {"ready": True, "pressed": bool(native.pressed), "holdable": bool(native.holdable),
                      "taps": native.taps, "x": native.x, "y": native.y, "error": native.error},
            "imageActive": bool(native.image_active),
            "ui": {"active": bool(native.active), "buttonReady": True,
                   "buttonPressed": bool(native.button_pressed), "event": native.event,
                   "gesture": native.gesture, "x": native.gesture_x, "y": native.gesture_y,
                   "eventFrame": f"{native.event_frame:08x}", "frame": f"{native.frame:08x}",
                   "eventQueue": True, "voiceGate": True, "voiceRing": True, "layoutVersion": 2,
                   "idleMode": True, "idle": bool(native.idle), "wake": native.wake,
                   "wakePending": bool(native.wake_pending),
                   "holdToTalk": bool(native.front_voice and not native.idle and not native.wake_pending),
                   "holdReady": bool(native.voice_ready),
                   "brightness": 6 if native.idle else 25, "dropped": native.dropped,
                   "contacts": native.contacts, "releases": native.releases, "tapDrops": native.tap_drops,
                   "imageVersion": native.image_version, "presentedVersion": native.presented_version,
                   "presentedMs": native.presented_ms, "nowMs": native.now_ms, "events": events},
        }


def control_region(x, y):
    return bool(_library().shark_sim_control(_integer(x, 65535, "x"), _integer(y, 65535, "y")))


def coordinates(raw_x, raw_y):
    x, y = ctypes.c_uint16(), ctypes.c_uint16()
    valid = _library().shark_sim_coordinates(_integer(raw_x, 65535, "raw_x"),
        _integer(raw_y, 65535, "raw_y"), ctypes.byref(x), ctypes.byref(y))
    return (x.value, y.value) if valid else None


def render_ring(pixels, seconds, level, recording=True):
    """Overlay the firmware's voice ring on one RGB565 big-endian frame."""
    if len(pixels) != 466 * 466 * 2:
        raise ValueError("The native ring needs exactly one 466 × 466 RGB565 frame")
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 <= seconds <= 2**32:
        raise ValueError("seconds must be finite and between 0 and 2**32")
    output = (ctypes.c_uint8 * len(pixels)).from_buffer_copy(pixels)
    _library().shark_sim_ring(output, seconds, _integer(level, 65535, "level"), bool(recording))
    return bytes(output)
