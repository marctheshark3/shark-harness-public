"""Run the production renderer/controller and input engine with synthetic I/O.

Only the panel, microphone, speech recognition, and T3 service are simulated.
The native input engine, DeviceUI, and ListenStore are the production code.
"""
from copy import deepcopy
import io
import math
from pathlib import Path
import tempfile

from device import ProtocolError
from device_ui import DeviceUI
from listen import FINISHED, ListenStore
from simulator.native import NativeInput

SCENARIOS = [
    ("projects", "Project library"), ("sessions", "Session navigation"),
    ("paused", "Voice paused"), ("review", "Review a long prompt"),
    ("confirm", "Confirm destination"), ("idle", "Dim idle / wake"),
    ("offline", "T3 disconnected"), ("uncertain", "Unconfirmed send"),
    ("empty", "No projects yet"), ("scene", "Temporary visual"),
]
TRANSCRIPT = ("This is a synthetic simulator prompt. Check the navigation in both directions, "
              "keep the recording destination fixed, and verify that waking the screen does not "
              "activate a control. Review every page before confirming the destination. "
              "No microphone, database, or real agent is connected. ") * 3


def unpack565(data):
    from PIL import Image, ImageChops
    high = Image.frombytes("L", (466, 466), data[::2])
    low = Image.frombytes("L", (466, 466), data[1::2])
    red = high.point(lambda v: (v & 248) | (v >> 5))
    green = ImageChops.add(high.point(lambda v: (v & 7) << 3), low.point(lambda v: v >> 5))
    green = green.point(lambda v: (v << 2) | (v >> 4))
    blue = low.point(lambda v: ((v & 31) << 3) | ((v & 31) >> 2))
    return Image.merge("RGB", (red, green, blue))


class SyntheticSession:
    """A deliberately network-free stand-in for the authenticated T3 adapter."""
    def __init__(self, snapshot):
        self.snapshot, self.attempts, self.drafts = snapshot, [], {}
        self.draft_counter = 0
        self.uncertain = False

    def describe(self, thread_id):
        if thread_id in self.drafts:
            return dict(self.drafts[thread_id])
        thread = next((t for t in self.snapshot["threads"] if t["id"] == thread_id), None)
        if thread is None: raise ValueError("That synthetic session is unavailable.")
        return {"thread_id": thread["id"], "title": thread["title"], "project_id": thread["projectId"]}

    def draft(self, thread_id=None, project_id=None):
        if thread_id:
            row = self.describe(thread_id)
            if project_id is None: project_id = row["project_id"]
            if project_id != row["project_id"]: raise ValueError("Synthetic project changed.")
        project = next((p for p in self.snapshot["projects"] if p["id"] == project_id), None)
        if project is None: raise ValueError("Choose a synthetic project.")
        self.draft_counter += 1
        thread_id = f"sim-draft-{self.draft_counter}"
        row = {"thread_id": thread_id, "title": "New voice session", "project_id": project_id,
               "projectTitle": project["title"]}
        self.drafts[thread_id] = row
        return dict(row)

    def send(self, thread_id, text, command_id, message_id, *, draft=None):
        if any(item["commandId"] == command_id for item in self.attempts):
            raise ValueError("Duplicate synthetic submission.")
        self.attempts.append({"threadId": thread_id, "text": text, "commandId": command_id})
        if self.uncertain: raise TimeoutError("Simulated lost acknowledgement")
        if draft:
            self.snapshot["threads"].append({"id": thread_id, "title": text[:60],
                "projectId": draft["project_id"], "project": draft["projectTitle"], "state": "idle"})
            self.drafts.pop(thread_id, None)
        return len(self.attempts)


class SimDisplay:
    """Display transport boundary, backed by the actual native input engine."""
    def __init__(self, simulation):
        self.sim = simulation
        self.native = NativeInput()
        self.pixels = b"\x00\x00" * (466 * 466)
        self.version, self.image_active, self.mic_enabled = 0, False, False
        self.frame_version, self.presented_screen = 0, None
        self.recording, self.available, self.capture_id = False, False, 0
        self.audio = {"playing": False, "armed": False, "uploading": False}

    def status(self):
        status = deepcopy(self.native.status(self.sim.ms))
        status.update(ready=True, imageActive=self.image_active and status.get("imageActive", False),
                      frames=self.version, bootError=0)
        status["mic"] = {"ready": True, "enabled": self.mic_enabled, "recording": self.recording,
            "available": self.available, "id": self.capture_id, "bytes": 0, "error": 0}
        status["audio"] = dict(self.audio)
        return status

    def request(self, op, **fields):
        if op in {"status", "hello"}: return self.status()
        if op == "ui.configure":
            prior = self.native.status(self.sim.ms)["ui"]["active"]
            self.native.configure(fields["enabled"], self.sim.ms)
            if not prior or not fields["enabled"]: self.image_active = False
        elif op == "ui.ack": self.native.ack(fields["event"])
        elif op == "ui.idle":
            busy = self.recording or self.available or any(self.audio.values())
            if fields["enabled"] and busy or not self.native.idle(fields["enabled"]):
                raise ProtocolError("Device rejected ui.idle: ui_idle_busy")
        elif op == "mic.configure":
            self.mic_enabled = fields["enabled"]
            if not self.mic_enabled and self.recording: self.recording = self.available = False
        elif op == "mic.clear": self.recording = self.available = False
        elif op == "audio.stop": self.audio = dict.fromkeys(self.audio, False)
        elif op == "clear":
            self.image_active = False
            self.native.clear(self.sim.ms)
        else: raise ProtocolError(f"Simulator does not implement {op}")
        return self.status()

    def send_frame(self, pixels, *, seconds=600, service=None, ui_token=None, hold_to_talk=False):
        self.pixels, self.image_active = bytes(pixels), True
        self.sim.frame_counter += 1
        self.version = self.sim.frame_counter
        screen = self.sim.ui.screen()
        if screen != self.presented_screen:
            # Animation repaints keep the same controls. A changed destination,
            # voice state, or review page still invalidates the browser's image.
            self.frame_version = self.version
            self.presented_screen = deepcopy(screen)
        self.native.frame(ui_token or 1, voice=hold_to_talk, version=self.version, now_ms=self.sim.ms)
        return {**self.status(), "uploadSeconds": 0}


class ObservedUI(DeviceUI):
    """Log the production controller's returned actions without changing them."""
    def event(self, event, screen):
        result = super().event(event, screen)
        if result:
            after = self.screen()
            if result in {"next", "previous", "next-project", "previous-project", "next-t3", "previous-t3"}:
                forward = result.startswith("next")
                subject = "project" if after["kind"] == "projects" else "session"
                label = after.get("project") if subject == "project" else after.get("title")
                index, prior = after.get("index", 1), screen.get("index", 1)
                wrapped = (forward and index < prior) or (not forward and index > prior)
                message = f"{'Next' if forward else 'Previous'} {subject} → {label} · {index:02d} of {after.get('total', 1):02d}"
                if wrapped: message += " (wrapped)"
            elif result == "current-t3": message = "BOOT selected the open synthetic session"
            else: message = result.replace("-", " ").capitalize()
            self.observer("control", message)
        return result


class Simulation:
    def __init__(self, scenario="projects"):
        self.temporary = tempfile.TemporaryDirectory(prefix="shark-simulator-")
        self.display = None
        self.frame_counter = 0
        self.reset(scenario)

    @property
    def ms(self): return round(self.now * 1000)

    def log(self, kind, message):
        self.sequence += 1
        self.events.append({"id": self.sequence, "time": round(self.now - 100, 2),
                            "kind": kind, "message": message})
        self.events = self.events[-60:]
        self.last_action = message

    def reset(self, scenario="projects"):
        if scenario not in dict(SCENARIOS): raise ValueError("Unknown simulator scenario.")
        if self.display: self.display.native.close()
        self.now, self.sequence, self.events = 100.0, 0, []
        self.scenario, self.last_action = scenario, "Ready for input"
        self.pointer, self.button_pressed = None, False
        self.hold_started, self.transcribe_at, self.recordings = None, None, 0
        self.last_lease, self.previous_kind, self.previous_idle = 0, None, False
        self.snapshot = {"connected": True, "overlay": None, "projects": [], "threads": []}
        for slug, title in [("atlas", "Atlas"), ("beacon", "Beacon"), ("cirrus", "Cirrus")]:
            self.snapshot["projects"].append({"id": slug, "title": title, "path": f"/demo/projects/{slug}"})
            for index, name in enumerate(("Build the navigation", "Tune the ambient display", "Prepare the release")):
                self.snapshot["threads"].append({"id": f"{slug}-{index + 1}", "title": name,
                    "project": title, "projectId": slug, "state": "idle"})
        if scenario == "empty": self.snapshot.update(projects=[], threads=[])
        if scenario == "offline": self.snapshot["connected"] = False
        self.session = SyntheticSession(self.snapshot)
        self.store = ListenStore(self.session, clock=lambda: 1800000000 + self.now,
                                 monotonic=lambda: self.now)
        self.display = SimDisplay(self)
        self.display.request("ui.configure", enabled=True)
        selection = Path(self.temporary.name) / "selection.json"
        selection.unlink(missing_ok=True)
        self.ui = ObservedUI(self.display, 0, lambda: None, selection=selection,
            auto_send=scenario not in {"review", "confirm"}, api=self.api, clock=lambda: self.now)
        self.ui.observer = self.log
        self.ui.choosing_project = scenario == "projects"
        self._tick()
        if scenario == "paused":
            self.store.configure({"threadId": self.store.target["id"], "enabled": False})
        if scenario in {"review", "confirm", "uncertain"}:
            self.session.uncertain = scenario == "uncertain"
            job = self.store.begin({"captureId": 1})["job"]
            self.store.progress({"id": job["id"], "state": "transcribing"})
            self.store.progress({"id": job["id"], "state": "review", "text": TRANSCRIPT})
        if scenario == "scene":
            self.snapshot["overlay"] = {"kind": "message", "title": "Make room for good ideas",
                "detail": "Synthetic visual · tap to return", "expiresAt": 1800000000 + self.now + 30}
        self._tick()
        if scenario == "confirm": self.ui.confirming = True; self._tick()
        if scenario == "idle": self.advance(60.1)
        self.log("scenario", dict(SCENARIOS)[scenario])
        return self.state()

    def api(self, path, value=None, method=None):
        if path == "/api/status": return deepcopy(self.snapshot)
        if path == "/api/speech": return {"job": None}
        if path == "/api/current-session":
            if not self.snapshot["connected"] or not self.snapshot["threads"]:
                raise ValueError("Open a synthetic T3 conversation first.")
            row = self.snapshot["threads"][1]
            return {"threadId": row["id"], "projectId": row["projectId"], "title": row["title"]}
        if path == "/api/listen": return self.store.discard() if method == "DELETE" else self.store.get()
        routes = {"/api/listen/configure": self.store.configure, "/api/listen/new": self.store.new_session,
                  "/api/listen/confirm": self.store.confirm}
        if path not in routes: raise ValueError("Unknown synthetic API route.")
        return routes[path](value)

    def _tick(self):
        if self.now - self.last_lease >= .4:
            self.display.request("ui.configure", enabled=True)
            self.last_lease = self.now
        desired = self.store.get()
        job = desired["job"]
        can_record = desired["enabled"] and desired["destinationReady"] and (
            not job or job["state"] in FINISHED | {"listening"})
        self.display.mic_enabled = bool(can_record)
        if not can_record and self.display.recording:
            self.display.recording = self.display.available = False
            self.transcribe_at = None
        health = self.display.status()
        held = health["touch"].get("holdable", False)
        if held and self.hold_started is None: self.hold_started = self.now
        if not held: self.hold_started = None
        if (held and can_record and self.hold_started is not None and self.now - self.hold_started >= .8
                and not self.display.recording):
            self.display.capture_id += 1
            try: self.store.begin({"captureId": self.display.capture_id})
            except ValueError: pass
            else:
                self.display.recording = True
                self.recordings += 1
                self.log("voice", "Synthetic recording started · destination locked")
        if self.display.recording and not health["touch"].get("pressed"):
            self.display.recording = False
            self.display.available = True
            job = self.store.get()["job"]
            if job and job["state"] == "listening":
                self.store.progress({"id": job["id"], "state": "transcribing"})
                self.transcribe_at = self.now + .6
                self.log("voice", "Released · synthetic transcription")
        if self.transcribe_at is not None and self.now >= self.transcribe_at:
            self.transcribe_at, self.display.available = None, False
            job = self.store.get()["job"]
            if job and job["state"] == "transcribing":
                self.store.progress({"id": job["id"], "state": "review", "text": TRANSCRIPT})
        scene = self.snapshot.get("overlay")
        if scene and scene["expiresAt"] <= 1800000000 + self.now: self.snapshot["overlay"] = None
        health = self.display.status()
        self.store.hardware({"mic": health["mic"], "ui": health["ui"], "touch": health["touch"]})
        self.ui.last_refresh = 0  # The in-memory API is synchronous; never hide a new synthetic state.
        self.ui.tick(health)
        status = self.display.status()
        idle = status["ui"]["idle"]
        if idle != self.previous_idle:
            self.log("idle", "Dim ambient · tap to wake" if idle else "Awake · wake press consumed")
        self.previous_idle = idle
        kind = self.ui.screen()["kind"]
        if kind != self.previous_kind:
            self.log("screen", kind.replace("-", " ").capitalize())
        self.previous_kind = kind

    def advance(self, seconds):
        if type(seconds) not in {int, float} or not math.isfinite(seconds) or not 0 <= seconds <= 900:
            raise ValueError("Advance must be 0–900 seconds.")
        end = self.now + seconds
        while self.now < end - .000001:
            self.now = min(end, self.now + (.02 if self.pointer or self.button_pressed else .2))
            if self.pointer:
                x, y, raw = self.pointer
                self.display.native.report(self.ms, 1, x, y, raw=raw)
            if self.button_pressed: self.display.native.button(self.ms, True)
            self._tick()

    def pointer_event(self, phase, x=233, y=233, *, raw=False):
        if phase not in {"down", "move", "up", "cancel"}: raise ValueError("Unknown pointer phase.")
        # Pointer capture can release outside the circle, or after a layout
        # change. Releases must always drain the native contact/recording latch.
        if phase in {"up", "cancel"}:
            x, y = (self.pointer or (233, 233, False))[:2]
        elif any(type(v) is not int for v in (x, y)):
            raise ValueError("Touch coordinates must be integer panel pixels.")
        elif not (0 <= x < 466 and 0 <= y < 466):
            x = y = 466  # Actual native invalid-contact path, not a clamped hold.
        if phase == "down":
            if self.pointer is not None: raise ValueError("A touch is already active.")
            self.pointer = (x, y, raw)
        elif phase == "move":
            if self.pointer is None: return
            self.pointer = (x, y, raw)
        else:
            if phase == "cancel":
                self.display.native.interrupt(self.ms, error=True)
                if self.display.recording:
                    self.store.discard()
                    self.display.recording = self.display.available = False
                    self.transcribe_at = None
                    self.log("voice", "Contact cancelled · synthetic capture discarded")
            self.pointer = None
        self.display.native.report(self.ms, 1 if self.pointer else 0, x, y, raw=raw)
        self._tick()

    def gesture(self, start, end=None, *, duration=.12, raw=False):
        self.pointer_event("down", *start, raw=raw)
        if end is None: self.advance(duration)
        else:
            for step in range(1, 7):
                self.advance(duration / 6)
                point = tuple(round(a + (b - a) * step / 6) for a, b in zip(start, end))
                self.pointer_event("move", *point, raw=raw)
        self.pointer_event("up", *(end or start), raw=raw)
        self.advance(.2)

    def action(self, value):
        if not isinstance(value, dict): raise ValueError("Expected a simulator action.")
        kind = value.get("type")
        if kind in {"reset", "scenario"}: return self.reset(value.get("scenario", "projects"))
        if kind == "advance": self.advance(value.get("seconds", 60))
        elif kind == "pointer":
            self.pointer_event(value.get("phase"), value.get("x", 233), value.get("y", 233))
        elif kind in {"tap", "hold"}:
            duration = value.get("durationMs", 120 if kind == "tap" else 1200)
            if type(duration) not in {int, float} or not math.isfinite(duration) or not 20 <= duration <= 5000:
                raise ValueError("Contact duration must be 20–5000 ms.")
            self.gesture((value.get("x", 233), value.get("y", 233)), duration=duration / 1000)
        elif kind == "swipe":
            direction = value.get("direction")
            if direction not in {"left", "right"}: raise ValueError("Choose left or right.")
            start, end = ((330, 235), (135, 235)) if direction == "left" else ((135, 235), (330, 235))
            self.gesture(start, end, duration=.24)
        elif kind in {"boot", "key"}:
            if kind == "key" and type(value.get("pressed")) is not bool: raise ValueError("pressed must be boolean.")
            pressed = value.get("pressed", True)
            self.button_pressed = pressed
            self.display.native.button(self.ms, pressed)
            self._tick()
            if kind == "boot":
                self.advance(.1)
                self.button_pressed = False
                self.display.native.button(self.ms, False)
                self._tick()
        else: raise ValueError("Unknown simulator action.")
        return self.state()

    def controls(self, screen, idle=False):
        if idle: return [{"id": "center", "label": "Wake display", "x": 233, "y": 233}]
        kind = screen["kind"]
        controls = []
        def add(id, label, x, y): controls.append({"id": id, "label": label, "x": x, "y": y})
        if kind in {"projects", "t3-choices"} or (kind == "home" and not screen.get("new") and not screen.get("browser")):
            add("previous", "Previous", 43, 233); add("next", "Next", 423, 233)
        if kind == "home":
            add("header", "Choose project", 233, 85)
            add("left", "Projects" if screen.get("browser") else "Cancel new" if screen.get("new") else "New session", 128, 390)
            add("right", "Pause voice" if screen["enabled"] else "Resume voice", 335, 390)
            if screen["enabled"]:
                controls.append({"id": "center", "label": "Hold to talk", "kind": "hold", "x": 233, "y": 233})
        elif kind == "listening":
            controls.append({"id": "center", "label": "Release to finish", "kind": "hold", "x": 233, "y": 233})
        elif kind == "projects":
            add("center", "Open project", 233, 220)
            add("left", "New session", 128, 390); add("right", "Open sessions", 335, 390)
        elif kind == "review":
            add("left", "Discard", 128, 390)
            add("right", "Next page" if screen["page"] + 1 < screen["pages"] else "Continue", 335, 390)
        elif kind == "confirm":
            add("left", "Back to review", 128, 390); add("right", "Send synthetic prompt", 335, 390)
        elif kind in {"empty-project", "current-error", "voice-offline", "t3-choices"}:
            add("left", "Projects", 128, 390)
            add("right", {"empty-project": "New session", "t3-choices": "Use session"}.get(kind, "Retry"), 335, 390)
        elif kind in {"scene", "error", "empty", "uncertain", "sent", "control-error"}:
            add("center", "Return", 233, 230)
        return controls

    def state(self):
        health = self.display.status()
        screen, desired = self.ui.screen(), self.store.get()
        ui = health["ui"]
        return {"synthetic": True, "scenario": self.scenario,
            "scenarios": [{"id": id, "label": label} for id, label in SCENARIOS],
            "screen": deepcopy(screen), "clock": {"seconds": round(self.now - 100, 2), "idleAfter": 60},
            "idle": ui["idle"], "wakePending": ui["wakePending"], "brightness": 6 if ui["idle"] else 25,
            "recording": self.display.recording, "recordings": self.recordings,
            "destination": ({**desired["target"], "project": next((p["title"] for p in self.snapshot["projects"]
                if p["id"] == desired["target"].get("projectId")), "Synthetic project")} if desired["target"] else None),
            "mode": "send" if desired["autoSend"] else "review",
            "paused": not desired["enabled"], "lastAction": self.last_action,
            "events": deepcopy(self.events), "frameVersion": self.display.frame_version,
            "imageVersion": self.display.version,
            "frameUrl": f"/frame.png?v={self.display.frame_version}&paint={self.display.version}",
            "submissions": len(self.session.attempts),
            "controls": self.controls(screen, ui["idle"]), "input": deepcopy(health),
            "job": deepcopy(desired["job"])}

    def frame_png(self):
        from PIL import Image, ImageDraw
        status = self.display.status()
        if status["ui"]["idle"]:
            # Illustrative ambient preview; actual panel luminosity needs hardware.
            picture = Image.new("RGB", (466, 466), "#05090c")
            draw = ImageDraw.Draw(picture)
            for offset, color in ((-10, "#122f36"), (0, "#16454b"), (10, "#122f36")):
                points = [(x, round(233 + offset + 50 * math.sin(x * .012 + self.now * .2)
                          + 22 * math.sin(x * .022 - self.now * .3))) for x in range(466)]
                draw.line(points, fill=color, width=3)
        else:
            pixels = self.display.pixels
            if self.display.recording:
                from simulator.native import render_ring
                pixels = render_ring(pixels, self.now, round(4500 + 4000 * math.sin(self.now * 4)), True)
            picture = unpack565(pixels)
        result = io.BytesIO(); picture.save(result, format="PNG")
        return result.getvalue()

    def close(self):
        if self.display: self.display.native.close()
        self.temporary.cleanup()

    def __enter__(self): return self
    def __exit__(self, *args): self.close()
