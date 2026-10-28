"""Explicit microphone recordings, local STT, and confirmed T3 prompts."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
import uuid
import zlib
from urllib.error import HTTPError
from speech import bridge_request
from t3_session import DispatchRejected

RATE = 16000
# Unacknowledged firmware buffer size; streaming recordings can exceed it.
MAX_CAPTURE = RATE * 2 * 30
MAX_PROMPT = 20000
DEFAULT_MODEL = Path.home() / ".local/share/shark-harness/models/faster-whisper-base.en"
FINISHED = {"sent", "discarded", "empty", "error", "uncertain"}


def prompt_text(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= MAX_PROMPT:
        raise ValueError(f"Prompt must contain 1–{MAX_PROMPT} characters.")
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError("Prompt contains control characters.")
    return value.strip()


class ListenStore:
    def __init__(self, session, thread_id=None, enabled=False, *, clock=None, monotonic=None):
        self.lock = threading.RLock()
        self.clock = clock or (lambda: time.time())
        self.monotonic = monotonic or (lambda: time.monotonic())
        self.session = session
        self.target = None
        self.enabled = False
        self.auto_send = False
        self.draft = None
        self.job = None
        self.action = {"serial": 0, "name": "none"}
        self.device = {}
        self.last_seen = 0
        if thread_id:
            self.configure({"threadId": thread_id, "enabled": enabled})

    def get(self):
        with self.lock:
            if (self.job and self.job["state"] not in {"listening", "sending"}
                    and self.job["expiresAt"] is not None and self.clock() > self.job["expiresAt"]):
                if self.job["state"] not in FINISHED:
                    self.job.update(state="error", error="Recording expired. Try again.")
                self.job["text"] = ""
            destination_ready, destination_error = bool(self.target), "Choose a destination session first."
            if self.target and self.target.get("browser"):
                try:
                    row = self.session.describe(self.target["id"])
                    destination_ready = row.get("ready", True)
                    destination_error = row.get("error", "")
                    self.target.pop("telemetryThreadId", None)
                    if row.get("telemetryThreadId"):
                        self.target["telemetryThreadId"] = row["telemetryThreadId"]
                except ValueError as error:
                    destination_ready, destination_error = False, str(error)
            return {"enabled": self.enabled, "autoSend": self.auto_send,
                    "destinationReady": destination_ready,
                    "destinationError": "" if destination_ready else destination_error,
                    "target": dict(self.target) if self.target else None,
                    "job": {k: v for k, v in self.job.items() if k != "_draft"} if self.job else None, "action": dict(self.action),
                    "device": {**self.device, "connected": self.monotonic() - self.last_seen < 3}}

    def configure(self, value):
        if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
            raise ValueError("Choose a session and whether voice input is enabled.")
        thread_id = value.get("threadId")
        selected_draft = self.draft if self.draft and self.draft["thread_id"] == thread_id else None
        row = selected_draft or (self.session.describe(thread_id) if thread_id else None)
        if value["enabled"] and row is None:
            raise ValueError("Choose a destination session first.")
        if value["enabled"] and row and row.get("ready") is False:
            raise ValueError(row["error"])
        auto_send = value.get("autoSend", self.auto_send)
        if type(auto_send) is not bool:
            raise ValueError("autoSend must be true or false.")
        with self.lock:
            if self.job and self.job["state"] == "sending":
                raise ValueError("Wait for the current send to finish.")
            # Selection changes apply to the next recording. Every in-flight
            # prompt retains the destination and draft captured by begin().
            if (not self.job or self.job["state"] in FINISHED
                    or (not value["enabled"] and self.job["state"] != "review")):
                self.discard()
            self.target = {"id": row["thread_id"], "title": row["title"],
                           "projectId": row.get("project_id")} if row else None
            if row and row.get("browser"):
                self.target.update(browser=True, new=row.get("new", False),
                    project=row["projectTitle"], environment=row["environment"])
            if selected_draft:
                self.target.update(new=True, project=selected_draft["projectTitle"])
            self.enabled = value["enabled"]
            self.auto_send = auto_send
            self.draft = selected_draft
            return self.get()

    def new_session(self, value):
        if not isinstance(value, dict):
            raise ValueError("Choose a project for the new session.")
        auto_send = value.get("autoSend", self.auto_send)
        if type(auto_send) is not bool:
            raise ValueError("autoSend must be true or false.")
        draft = self.session.draft(value.get("threadId"), project_id=value.get("projectId"))
        if value.get("projectId") is not None and draft.get("project_id") != value["projectId"]:
            raise ValueError("The selected project changed. Choose the project again.")
        with self.lock:
            if self.job and self.job["state"] not in FINISHED:
                raise ValueError("Finish the current prompt first.")
            self.discard()
            self.draft = draft
            self.target = {"id": draft["thread_id"], "title": draft["title"],
                           "project": draft["projectTitle"], "projectId": draft.get("project_id"), "new": True}
            self.enabled = True
            self.auto_send = auto_send
            return self.get()

    def control(self, value):
        if not isinstance(value, dict) or value.get("action") not in {"record", "stop"}:
            raise ValueError("Choose record or stop.")
        with self.lock:
            current = self.get()
            if not self.enabled or not current["device"]["connected"] or not self.device.get("ready"):
                raise ValueError("Enable voice input and connect the microphone first.")
            if value["action"] == "record" and not current["destinationReady"]:
                raise ValueError(current["destinationError"])
            if value["action"] == "record" and self.job and self.job["state"] not in FINISHED:
                raise ValueError("Send or discard the current recording first.")
            self.action = {"serial": self.action["serial"] + 1, "name": value["action"]}
            return self.get()

    def hardware(self, value):
        if not isinstance(value, dict) or not isinstance(value.get("mic"), dict):
            raise ValueError("Expected microphone status.")
        with self.lock:
            mic = value["mic"]
            self.device = {k: mic.get(k) for k in ("ready", "recording", "level", "bytes", "error")}
            # Read-only diagnostics travel through the existing USB owner.
            # Inspecting touch/display health must not open a second serial client.
            for field in ("touch", "ui", "display"):
                if isinstance(value.get(field), dict):
                    self.device[field] = value[field]
            self.last_seen = self.monotonic()
            return self.get()

    def begin(self, value):
        if not isinstance(value, dict) or type(value.get("captureId")) is not int:
            raise ValueError("Expected a capture id.")
        with self.lock:
            current = self.get()
            if not self.enabled or not self.target or (self.job and self.job["state"] not in FINISHED):
                raise ValueError("Voice input is disabled or a transcript is waiting.")
            if not current["destinationReady"]:
                raise ValueError(current["destinationError"])
            self.job = {"id": uuid.uuid4().hex, "captureId": value["captureId"],
                        "threadId": self.target["id"], "threadTitle": self.target["title"],
                        "autoSend": self.auto_send, "_draft": self.draft,
                        "state": "listening", "text": "", "error": "", "expiresAt": None}
            return self.get()

    def progress(self, value):
        if not isinstance(value, dict):
            raise ValueError("Expected recording progress.")
        with self.lock:
            self.get()
            if not self.job or value.get("id") != self.job["id"] or self.job["state"] in FINISHED | {"sending"}:
                return self.get()  # A cancelled or superseded worker cannot resurrect its result.
            state = value.get("state")
            allowed = {"listening": {"transcribing", "empty", "error"},
                       "transcribing": {"review", "empty", "error"}}
            if state not in allowed.get(self.job["state"], set()):
                raise ValueError("Invalid recording transition.")
            text = prompt_text(value.get("text")) if state == "review" else ""
            self.job.update(state=state, text=text, error=str(value.get("error", ""))[:200],
                            expiresAt=self.clock() + 600)
            auto = state == "review" and self.job["autoSend"]
            confirmation = {"id": self.job["id"], "threadId": self.job["threadId"],
                            "text": text, "confirm": True}
        if auto:
            try:
                return self.confirm(confirmation)
            except ValueError:
                pass  # Destination/cancellation changed while transcription completed.
        return self.get()

    def discard(self):
        with self.lock:
            if self.job and self.job["state"] == "sending":
                raise ValueError("The prompt is already being sent.")
            if self.job:
                self.job.update(state="discarded", text="", error="")
            self.action = {"serial": self.action["serial"] + 1, "name": "cancel"}
            return self.get()

    def confirm(self, value):
        if not isinstance(value, dict) or value.get("confirm") is not True:
            raise ValueError("Review the transcript and confirm sending it.")
        text = prompt_text(value.get("text"))
        with self.lock:
            self.get()
            job = self.job
            if (not job or job["state"] != "review" or value.get("id") != job["id"]
                    or value.get("threadId") != job["threadId"]):
                raise ValueError("That transcript or destination has changed. Review it again.")
            # Claim before network I/O: double clicks and retries cannot send twice.
            job.update(state="sending", text=text)
            thread_id, job_id, draft = job["threadId"], job["id"], job.get("_draft")
        def dispatch():
            try:
                kwargs = {"draft": draft} if draft else {}
                sequence = self.session.send(thread_id, text, "shark-voice-" + job_id, "shark-voice-" + job_id, **kwargs)
                outcome = {"state": "sent", "sequence": sequence, "text": ""}
            except (DispatchRejected, ValueError) as error:
                outcome = {"state": "error", "error": str(error)[:200] or "T3 rejected the prompt. Check the session before trying again."}
            except Exception:
                # An interrupted acknowledgement does not prove the command failed.
                outcome = {"state": "uncertain", "error": "No T3 acknowledgement. Check the session; this prompt will not be resent."}
            with self.lock:
                job.update(outcome)
                target_matches = self.target and self.target["id"] == thread_id
                if outcome["state"] == "sent" and target_matches and thread_id.startswith("voice-"):
                    try:
                        row = self.session.describe(thread_id)
                        self.target.update(title=row["title"], new=row["new"])
                    except ValueError:
                        pass  # A lost browser after acknowledgement is still a sent prompt.
                if outcome["state"] == "sent" and target_matches and draft:
                    self.target = {"id": thread_id, "title": text[:60], "project": draft["projectTitle"],
                                   "projectId": draft.get("project_id")}
                    self.draft = None
                return self.get()

        if thread_id.startswith("voice-"):
            threading.Thread(target=dispatch, daemon=True).start()
            return self.get()
        return dispatch()


class LocalTranscriber:
    def __init__(self, model=DEFAULT_MODEL):
        self.path, self.model = Path(model), None

    def transcribe(self, pcm):
        if not isinstance(pcm, bytes) or len(pcm) < RATE // 2 or len(pcm) % 2:
            raise ValueError("Expected at least 0.25 seconds of 16 kHz mono PCM16.")
        import numpy as np
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        if float(np.sqrt(np.mean(samples ** 2))) < 0.002:
            return ""
        if self.model is None:
            from faster_whisper import WhisperModel
            self.model = WhisperModel(str(self.path), device="cpu", compute_type="int8", cpu_threads=4,
                                      num_workers=1, local_files_only=True)
        segments, _ = self.model.transcribe(samples, language="en", beam_size=5,
                                            vad_filter=True, condition_on_previous_text=False,
                                            vad_parameters={"min_silence_duration_ms": 400})
        text = " ".join(s.text.strip() for s in segments
                        if s.no_speech_prob < 0.6 and s.avg_logprob > -1.0).strip()
        return prompt_text(text) if text else ""


class ListenPump:
    """Single USB owner; transcription runs in one bounded background worker."""
    def __init__(self, display, port, model=DEFAULT_MODEL):
        self.display, self.port = display, port
        self.voice = LocalTranscriber(model)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="transcription")
        self.future = None
        self.job_id = None
        self.capture_id = None
        self.pcm = bytearray()
        self.capture_crc = 0
        self.download_seconds = 0
        self.serial = None
        self.pending_record = False
        self.next_tick = 0
        self.closed = False
        self.display.request("mic.configure", enabled=False)
        self.display.request("mic.clear")  # Never replay a capture left by an earlier sender.
        current = self.api()
        self.serial = current["action"]["serial"]
        if current["job"] and current["job"]["state"] in {"listening", "transcribing"}:
            self.job_id = current["job"]["id"]
            self.progress("error", error="USB sender restarted. Hold to record again.")
            self.job_id = None

    def api(self, suffix="", value=None, method=None):
        return bridge_request(self.port, "/api/listen" + suffix, value, method,
                              timeout=15 if suffix == "/progress" else 3)

    def progress(self, state, **fields):
        return self.api("/progress", {"id": self.job_id, "state": state, **fields})

    def begin_capture(self, capture_id):
        try:
            begun = self.api("/begin", {"captureId": capture_id})
        except HTTPError as error:
            if error.code != 400:
                raise
            # The user may disable/change destination between polling and begin.
            self.display.request("mic.clear")
            self.job_id = self.capture_id = None
            return False
        self.job_id = begun["job"]["id"]
        self.capture_id = capture_id
        self.pcm.clear()
        self.capture_crc = 0
        self.download_seconds = 0
        return True

    def tick(self):
        if self.closed or time.monotonic() < self.next_tick:
            return
        self.next_tick = time.monotonic() + 0.2
        desired = self.api()
        job = desired["job"]
        destination_ready = desired.get("destinationReady", True) or (job and job["state"] == "listening")
        can_record = desired["enabled"] and destination_ready and (not job or job["state"] in FINISHED | {"listening"}) and self.future is None
        health = self.display.request("mic.configure", enabled=bool(can_record))
        mic = health["mic"]
        self.api("/device", {"mic": mic, "touch": health.get("touch", {}), "ui": health.get("ui", {}),
            "display": {k: health.get(k) for k in ("ready", "imageActive", "frames", "crc32", "bootError")}})
        action = desired["action"]
        self.pending_record = False
        if self.serial is None:
            self.serial = action["serial"]  # Do not replay a browser action from before startup.
        elif action["serial"] != self.serial:
            previous_serial = self.serial
            self.serial = action["serial"]
            if action["name"] == "cancel":
                self.display.request("mic.clear")
                self.job_id = self.capture_id = None
                self.pcm.clear()
                return
            if action["name"] == "record" and can_record:
                if health.get("ui", {}).get("idle") or health.get("ui", {}).get("wakePending"):
                    # The UI owner wakes and presents a fresh control frame.
                    # Retain this explicit browser action until that completes.
                    self.serial = previous_serial
                    self.pending_record = True
                    return
                self.display.request("audio.stop")
                if self.display.request("status")["audio"]["playing"]:
                    self.serial = previous_serial
                    self.pending_record = True
                    return
                try:
                    mic = self.display.request("mic.start")["mic"]
                except RuntimeError as error:
                    if str(error) != "Device rejected mic.start: ui_idle_busy":
                        raise  # Never retry a capture with an uncertain result.
                    self.serial = previous_serial
                    self.pending_record = True
                    return
            elif action["name"] == "stop":
                self.display.request("mic.stop")
        if mic["recording"] and self.capture_id != mic["id"]:
            if not self.begin_capture(mic["id"]):
                return
        if mic.get("streaming") and (mic["recording"] or mic["available"]) and self.future is None:
            if self.capture_id != mic["id"] and not self.begin_capture(mic["id"]):
                return
            started = time.monotonic()
            while len(self.pcm) < mic["bytes"]:
                chunk = self.display.capture_chunk(mic, len(self.pcm))
                self.pcm.extend(chunk)
                self.capture_crc = zlib.crc32(chunk, self.capture_crc)
                # Leave time for USB heartbeats and display/touch updates.
                if time.monotonic() - started >= .5:
                    break
            self.download_seconds += time.monotonic() - started
        if mic["available"] and self.future is None:
            if self.capture_id != mic["id"]:
                if not self.begin_capture(mic["id"]):
                    return
            if mic.get("streaming"):
                if len(self.pcm) < mic["bytes"]:
                    return  # Drain the remaining tail on the next heartbeat.
                if len(self.pcm) != mic["bytes"] or f"{self.capture_crc:08x}" != mic.get("crc32"):
                    raise ValueError("Microphone stream checksum mismatch.")
                pcm = bytes(self.pcm)
                self.pcm.clear()
            else:
                started = time.monotonic()
                pcm = self.display.capture_audio(mic)
                self.download_seconds = time.monotonic() - started
            self.display.request("mic.clear")
            self.progress("transcribing")
            self.future = self.pool.submit(self.voice.transcribe, pcm)
            self.transcription_started = time.monotonic()
        elif not mic["recording"] and self.capture_id is not None and self.future is None and job and job["state"] == "listening":
            self.progress("empty" if not mic["error"] else "error", error="Microphone capture failed." if mic["error"] else "")
            self.capture_id = None
            self.pcm.clear()
        if self.future is not None and self.future.done():
            try:
                text = self.future.result()
                transcription_seconds = time.monotonic() - self.transcription_started
                if self.job_id:
                    submitted = time.monotonic()
                    result = self.progress("review" if text else "empty", text=text)
                    print(f"Voice: transfer={self.download_seconds:.2f}s, transcription={transcription_seconds:.2f}s, "
                          f"dispatch={time.monotonic() - submitted:.2f}s, state={(result.get('job') or {}).get('state')}", flush=True)
            except Exception:
                if self.job_id:
                    self.progress("error", error="Local transcription failed. Check the installed STT model.")
            finally:
                self.future = None
                self.capture_id = None

    def close(self):
        self.closed = True
        self.pcm.clear()
        try:
            self.display.request("mic.configure", enabled=False)
            self.display.request("mic.clear")
        finally:
            self.pool.shutdown(wait=False, cancel_futures=True)
