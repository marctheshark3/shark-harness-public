"""Local, explicit speech requests and a cancellable USB playback worker."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time
from urllib.request import Request, urlopen
import uuid
import zlib

SAMPLE_RATE = 22050
MAX_BYTES = SAMPLE_RATE * 2 * 20
DEFAULT_VOICE = Path.home() / ".local/share/shark-harness/voices/en_US-lessac-medium.onnx"
TERMINAL = {"done", "stopped", "error"}


def validate_text(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 400:
        raise ValueError("Speech text must contain 1–400 characters.")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise ValueError("Speech text contains control characters.")
    return value.strip()


def validate_volume(value):
    if type(value) is not int or not 0 <= value <= 80:
        raise ValueError("Volume must be an integer from 0 to 80 (0 mutes).")
    return value


class SpeechStore:
    """One transient request: replacement interrupts, claiming prevents replay."""
    def __init__(self):
        self.lock = threading.RLock()
        self.volume = 60
        self.job = None

    def get(self):
        with self.lock:
            if self.job and self.job["state"] not in TERMINAL and self.job["expiresAt"] <= time.time():
                self.job.update(state="error", error="Speech request expired; check the USB sender.", text="")
            return {"volume": self.volume, "job": dict(self.job) if self.job else None}

    def put(self, value):
        if not isinstance(value, dict):
            raise ValueError("Send a speech object.")
        text = validate_text(value.get("text"))
        volume = validate_volume(value["volume"]) if "volume" in value else None
        on_tap = value.get("onTap", False)
        if type(on_tap) is not bool:
            raise ValueError("onTap must be true or false.")
        with self.lock:
            if volume is not None:
                self.volume = volume
            self.job = {"id": uuid.uuid4().hex, "state": "queued", "text": text, "onTap": on_tap,
                        "expiresAt": time.time() + 90, "error": ""}
            return self.get()

    def stop(self):
        with self.lock:
            if self.job and self.job["state"] not in TERMINAL:
                self.job.update(state="stopped", text="")
            return self.get()

    def set_volume(self, value):
        if not isinstance(value, dict):
            raise ValueError("Send a volume object.")
        volume = validate_volume(value.get("volume"))
        with self.lock:
            self.volume = volume
            return self.get()

    def claim(self, value):
        if not isinstance(value, dict):
            raise ValueError("Send a request id.")
        with self.lock:
            self.get()
            if not self.job or value.get("id") != self.job["id"] or self.job["state"] != "queued":
                return {"claimed": False}
            self.job["state"] = "synthesizing"
            return {"claimed": True, **self.get()}

    def update(self, value):
        if not isinstance(value, dict) or value.get("state") not in {"uploading", "armed", "playing", *TERMINAL}:
            raise ValueError("Invalid speech progress.")
        error = value.get("error", "")
        if not isinstance(error, str) or len(error) > 200:
            raise ValueError("Invalid speech error.")
        with self.lock:
            self.get()
            if not self.job or value.get("id") != self.job["id"] or self.job["state"] in TERMINAL:
                return {"updated": False}
            allowed = {"synthesizing": {"uploading"}, "uploading": {"armed", "playing"},
                       "armed": {"playing"}, "playing": {"done"}}
            state = value["state"]
            if state not in {"error", "stopped"} and state not in allowed.get(self.job["state"], set()):
                raise ValueError("Invalid speech state transition.")
            self.job.update(state=state, error=error)
            if state == "armed":
                self.job["expiresAt"] = time.time() + 3600
            elif state == "playing":
                self.job["expiresAt"] = time.time() + 30
            if state in TERMINAL:
                self.job["text"] = ""
            return {"updated": True}


def bridge_request(port, path="/api/speech", value=None, method=None, timeout=3):
    request = Request(f"http://127.0.0.1:{port}{path}", method=method,
                      data=json.dumps(value).encode() if value is not None else None,
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


class LocalVoice:
    def __init__(self, model=DEFAULT_VOICE):
        self.model = Path(model).expanduser()
        self.voice = None

    def synthesize(self, text):
        text = validate_text(text)
        if self.voice is None:
            if not self.model.is_file() or not Path(str(self.model) + ".json").is_file():
                raise ValueError("Voice model missing. Follow the local speech setup in README.md.")
            from piper import PiperVoice
            self.voice = PiperVoice.load(self.model, use_cuda=False)
        if self.voice.config.sample_rate != SAMPLE_RATE:
            raise ValueError("Use a 22050 Hz Piper voice, such as en_US-lessac-medium.")
        pcm = bytearray()
        for chunk in self.voice.synthesize(text):
            if chunk.sample_rate != SAMPLE_RATE or chunk.sample_width != 2 or chunk.sample_channels != 1:
                raise ValueError("Voice returned an unsupported audio format.")
            pcm.extend(chunk.audio_int16_array.astype("<i2", copy=False).tobytes())
            if len(pcm) > MAX_BYTES:
                raise ValueError("Speech exceeds 20 seconds. Send a shorter message.")
        if not pcm:
            raise ValueError("Voice returned no audio.")
        return bytes(pcm)


class SpeechPump:
    """Only the caller touches USB. Synthesis runs on one bounded worker thread."""
    def __init__(self, display, port, model=DEFAULT_VOICE, voice=None, api=None):
        self.display = display
        self.api = api or (lambda path="/api/speech", value=None: bridge_request(port, path, value))
        self.voice = voice or LocalVoice(model)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-voice")
        self.future = None
        self.active = None
        self.upload = None
        self.volume = None
        self.taps = 0
        self.next_poll = 0
        self.expected_bytes = 0
        self.expected_crc = ""

    def progress(self, state, error=""):
        return self.api("/api/speech/progress", {"id": self.active, "state": state, "error": error})

    def reset(self):
        self.display.request("audio.stop")
        if self.future:
            self.future.cancel()
        self.active = self.upload = None

    def tick(self):
        # Also called during image transfer, keeping stop/volume responsive.
        now = time.monotonic()
        if now < self.next_poll:
            return
        self.next_poll = now + 0.1
        desired = self.api()
        health = self.display.request("status")
        audio = health.get("audio", {})
        job = desired["job"]
        wanted = job["id"] if job and job["state"] not in TERMINAL else None
        if self.active and self.active != wanted:
            self.reset()
            return
        if audio.get("ready") and desired["volume"] != self.volume:
            self.display.request("audio.volume", volume=desired["volume"])
            self.volume = desired["volume"]
        taps = health.get("touch", {}).get("taps", 0)
        # An armed clip's first tap is handled locally by firmware. Do not
        # mistake that trigger for a cancellation while the renderer catches up.
        if self.active and job["state"] != "armed" and taps != self.taps:
            self.progress("stopped")
            self.reset()
            return
        if not self.active:
            # Do not enqueue synthesis behind abandoned requests.
            if self.future and not self.future.done():
                return
            if not job or job["state"] in TERMINAL:
                return
            if job["state"] != "queued":
                self.api("/api/speech/progress", {"id": job["id"], "state": "error",
                         "error": "Speech sender restarted; send the message again."})
                if audio.get("ready"):
                    self.display.request("audio.stop")
                return
            claim = self.api("/api/speech/claim", {"id": job["id"]})
            if not claim["claimed"]:
                return
            self.active, self.taps = job["id"], taps
            if not audio.get("ready"):
                self.progress("error", "Speaker unavailable; check firmware audio status.")
                self.active = None
                return
            self.future = self.executor.submit(self.voice.synthesize, claim["job"]["text"])
            return
        try:
            if job["state"] == "synthesizing" and self.future.done():
                if audio.get("playing"):
                    return  # The previous interrupted clip is still closing.
                pcm = self.future.result()
                self.expected_bytes = len(pcm)
                self.expected_crc = f"{zlib.crc32(pcm):08x}"
                self.upload = self.display.audio_chunks(pcm, on_tap=job["onTap"])
                self.progress("uploading")
            if self.upload is not None:
                # Eight chunks per poll, bounded by USB replies. Stop is checked
                # before every batch, including the batch that starts playback.
                for _ in range(8):
                    next(self.upload)
            elif job["state"] == "armed":
                if audio.get("stopped"):
                    self.progress("stopped")
                    self.active = None
                elif not audio.get("armed"):
                    self.taps = taps
                    self.progress("playing")
                    print("Speech playing after device tap.", flush=True)
            elif job["state"] == "playing" and not audio.get("playing"):
                complete = audio.get("played") == self.expected_bytes and audio.get("crc32") == self.expected_crc
                state = "stopped" if audio.get("stopped") else "error" if audio.get("error") or not complete else "done"
                self.progress(state, "Speaker playback failed." if state == "error" else "")
                print(f"Speech {state}.", flush=True)
                self.active = None
        except StopIteration:
            self.upload = None
            state = "armed" if job["onTap"] else "playing"
            self.progress(state)
            print(f"Speech {state} on device.", flush=True)
        except Exception as error:
            # Keep error details bounded; never log the requested speech text.
            self.progress("error", f"{type(error).__name__}: {error}"[:200])
            self.reset()

    def close(self):
        try:
            if self.active:
                self.progress("stopped")
        finally:
            try:
                if self.active:
                    self.reset()
            finally:
                self.executor.shutdown(wait=False, cancel_futures=True)
