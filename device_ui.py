"""Round-screen session controls. No browser is needed to navigate or send speech."""
import base64
from functools import lru_cache
import io
import json
import math
import os
import secrets
from pathlib import Path
import time
from urllib.error import HTTPError, URLError

from listen import FINISHED
from device import ProtocolError, rgb565
from speech import TERMINAL as SPEECH_FINISHED, bridge_request
from characters import FPS, FRAMES, STATES, load_character, render_character, selected_state

DEFAULT_SELECTION = Path.home() / ".local/state/shark-harness/selection.json"


@lru_cache(maxsize=48)
def font(size, bold=False, mono=False):
    from PIL import ImageFont
    try:
        family = "DejaVuSansMono" if mono else "DejaVuSans"
        return ImageFont.truetype(family + ("-Bold" if bold else "") + ".ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def lines(text, width=330, size=21, bold=False):
    """Wrap every character, including long code tokens, without losing text."""
    result, line = [], ""
    for paragraph in text.split("\n"):
        for word in paragraph.split(" "):
            trial = (line + " " + word).strip()
            if font(size, bold).getlength(trial) <= width:
                line = trial
                continue
            if line:
                result.append(line); line = ""
            for char in word:
                if font(size, bold).getlength(line + char) > width:
                    result.append(line); line = ""
                line += char
        result.append(line); line = ""
    return result or [""]


# Draw at twice the panel resolution so fine rings and icons remain smooth.
# Layout coordinates are physical pixels, shared with gesture.c's circular
# control regions. Only the completed frame is reduced to 466 × 466.
SCALE = 2
INK = "#f1f0eb"
MUTED = "#a8aba8"
ACCENT = "#ff5c35"
SURFACE = "#151617"
LINE = "#454745"
CONTROL = "#303230"
PINK = "#ff739a"


class PanelDraw:
    def __init__(self, image):
        from PIL import ImageDraw
        self.draw = ImageDraw.Draw(image)

    def shape(self, method, box, **kwargs):
        if "width" in kwargs: kwargs["width"] *= SCALE
        if "radius" in kwargs: kwargs["radius"] *= SCALE
        getattr(self.draw, method)(tuple(v * SCALE for v in box), **kwargs)

    def ellipse(self, box, **kwargs): self.shape("ellipse", box, **kwargs)
    def arc(self, box, start, end, **kwargs): self.shape("arc", box, start=start, end=end, **kwargs)
    def rounded_rectangle(self, box, **kwargs): self.shape("rounded_rectangle", box, **kwargs)
    def rectangle(self, box, **kwargs): self.shape("rectangle", box, **kwargs)

    def sector(self, start, end, inner=170, outer=217, fill=LINE):
        points = []
        for radius, angles in [(outer, range(start, end + 1)), (inner, range(end, start - 1, -1))]:
            points += [((233 + radius * math.cos(math.radians(a))) * SCALE,
                        (233 + radius * math.sin(math.radians(a))) * SCALE) for a in angles]
        self.draw.polygon(points, fill=fill)
        radius, cap = (inner + outer) / 2, (outer - inner) / 2
        for angle in (start, end):
            x = 233 + radius * math.cos(math.radians(angle))
            y = 233 + radius * math.sin(math.radians(angle))
            self.ellipse((x - cap, y - cap, x + cap, y + cap), fill=fill)

    def line(self, points, fill, width=1):
        self.draw.line([(x * SCALE, y * SCALE) for x, y in points], fill=fill,
                       width=width * SCALE, joint="curve")

    def text(self, value, y, size=18, color=INK, x=233, bold=False, mono=False):
        self.draw.text((x * SCALE, y * SCALE), value, font=font(size * SCALE, bold, mono),
                       fill=color, anchor="mt")


def fit_label(value, width, size, bold=False):
    """Ellipsize using the same font metrics as the final supersampled text."""
    value = " ".join(value.split())
    face = font(size * SCALE, bold)
    if face.getlength(value) <= width * SCALE: return value
    while value and face.getlength(value.rstrip() + "…") > width * SCALE:
        value = value[:-1]
    return value.rstrip() + "…"


@lru_cache(maxsize=128)
def arc_label(value, radius=161, center=270, size=15, color=INK, bottom=False, bold=False, width=245):
    """Place upright glyphs along the glass, with measured spacing and a hard arc limit."""
    from PIL import Image, ImageDraw
    value = fit_label(value, width, size, bold)
    layer = Image.new("RGBA", (466 * SCALE, 466 * SCALE))
    face = font(size * SCALE, bold)
    advances = [face.getlength(char) / SCALE + .6 for char in value]
    while len(value) > 1 and sum(advances) > width:
        value = value.removesuffix("…")[:-1].rstrip() + "…"
        advances = [face.getlength(char) / SCALE + .6 for char in value]
    cursor = -sum(advances) / 2
    for char, advance in zip(value, advances):
        offset = (cursor + advance / 2) / radius * 180 / math.pi
        angle = center - offset if bottom else center + offset
        theta = math.radians(angle)
        tile = Image.new("RGBA", (size * SCALE * 4, size * SCALE * 4))
        ImageDraw.Draw(tile).text((tile.width / 2, tile.height / 2), char, font=face, fill=color, anchor="mm")
        rotation = 90 - angle if bottom else -90 - angle
        tile = tile.rotate(rotation, resample=Image.Resampling.BICUBIC, expand=True)
        x, y = (233 + radius * math.cos(theta)) * SCALE, (233 + radius * math.sin(theta)) * SCALE
        layer.alpha_composite(tile, (round(x - tile.width / 2), round(y - tile.height / 2)))
        cursor += advance
    bounds = layer.getbbox()
    return (layer.crop(bounds), bounds[:2]) if bounds else (Image.new("RGBA", (1, 1)), (0, 0))


@lru_cache(maxsize=1)
def background():
    from PIL import Image
    # A flat charcoal surface keeps labels crisp and compresses well on USB.
    return Image.new("RGB", (466 * SCALE, 466 * SCALE), SURFACE)


def render(screen, character=None, character_frame=0):
    from PIL import Image, ImageDraw, ImageOps
    kind = screen["kind"]
    image = background().copy()
    draw = PanelDraw(image)
    text = draw.text

    def curved(value, **kwargs):
        layer, origin = arc_label(value, **kwargs)
        image.paste(layer, origin, layer)

    def block(value, y, size=25, count=3, width=280, color=INK, bold=False, centered=False):
        wrapped = lines(value, width * SCALE, size * SCALE, bold)
        if len(wrapped) > count:
            wrapped = wrapped[:count]
            wrapped[-1] = fit_label(wrapped[-1] + "…", width, size, bold)
        if centered: y -= (len(wrapped) * (size + 7) - 7) / 2
        for i, line in enumerate(wrapped):
            text(line, y + i * (size + 7), size, color, bold=bold)

    def edge(color=LINE):
        # Printed dial marks stay outside the established circular touch zones.
        for angle in range(0, 360, 10):
            theta = math.radians(angle)
            inner = 219 if angle % 30 == 0 else 223
            draw.line([(233 + inner * math.cos(theta), 233 + inner * math.sin(theta)),
                       (233 + 227 * math.cos(theta), 233 + 227 * math.sin(theta))], fill=color)

    def plate(value, y=131):
        width = font(11 * SCALE, mono=True).getlength(value) / SCALE + 20
        draw.rounded_rectangle((233 - width / 2, y, 233 + width / 2, y + 22),
                               radius=2, outline=LINE, width=1)
        text(value, y + 4, 11, MUTED, mono=True)

    def position(index, total):
        # The upper rim is an actual position indicator, not a progress meter.
        count = min(15, max(1, total))
        start = max(0, min(index - 1 - count // 2, total - count))
        span = min(110, count * 11)
        step = span / count
        for i in range(count):
            angle = 270 - span / 2 + i * step
            active = start + i == index - 1
            draw.arc((17, 17, 449, 449), angle + 1, angle + step - 1,
                     fill=ACCENT if active else LINE, width=4 if active else 2)

    def arrows(total):
        # Directions refer to the destination in the list. A finger swipe moves
        # the card itself, so swiping left reveals the next card. Keep the ink
        # inside the unchanged previous/next touch regions (layout version 2).
        color = INK if total > 1 else MUTED
        for start, end, x, direction, label in [(166, 194, 48, -1, "PREV"),
                                                (-14, 14, 418, 1, "NEXT")]:
            draw.arc((25, 25, 441, 441), start, end, fill=LINE, width=2)
            draw.line([(x - direction * 5, 220), (x + direction * 6, 230),
                       (x - direction * 5, 240)], fill=color, width=3)
            text(label, 253, 9, color, x=x, mono=True)

    def actions(left, right, primary=False):
        for label, start, end, center, active in [(left, 106, 140, 123, False),
                                                  (right, 40, 74, 57, primary)]:
            draw.sector(start, end, fill=ACCENT if active else CONTROL)
            curved(label.upper(), radius=192, center=center, size=13, bottom=True,
                   color=SURFACE if active else INK, bold=True, width=130)

    def return_hint(value="Tap to return"):
        curved(value.upper(), radius=184, center=90, size=12, bottom=True, color=MUTED, width=260)

    if kind == "home":
        edge()
        position(screen["index"], screen["total"])
        curved(screen.get("project", "Project"), color=INK, size=15, width=250)
        draw.line([(228, 108), (233, 113), (238, 108)], fill=ACCENT, width=1)
        is_new = screen.get("new")
        if screen.get("character") and not is_new:
            character = character or load_character(screen["character"])
            activity = screen.get("activity", "unknown")
            sprite = render_character(character, activity, character_frame)
            image.paste(sprite, (165 * SCALE, 99 * SCALE), sprite)
            label, color = STATES.get(activity, STATES["unknown"])
            text(label.upper(), 222, 12, color, mono=True)
            block(screen["title"], 267, 21, 2, 278, bold=True, centered=True)
            text(("Hold center · release to send" if screen.get("autoSend", True)
                  else "Hold center · release to review") if screen["enabled"] else "Microphone paused",
                 304, 13, INK)
        else:
            plate("NEW SESSION" if is_new else "SELECTED T3 SESSION" if screen.get("browser")
                  else f"SESSION / {screen['index']:02d} OF {screen['total']:02d}")
            block("New session" if is_new else screen["title"], 214, 27, 3, 278, bold=True, centered=True)
            if is_new: text("Your first prompt starts this session", 257, 13, MUTED)
            text("Hold center to talk" if screen["enabled"] else "Microphone paused", 282, 16, INK)
            text(("Release to send" if screen.get("autoSend", True) else "Release to review")
                 if screen["enabled"] else "Tap Resume to enable voice", 305, 12, MUTED)
        text("BOOT: use open T3 session", 326, 11, MUTED)
        if not is_new and not screen.get("browser"): arrows(screen["total"])
        actions("Projects" if screen.get("browser") else "Cancel new" if is_new else "New session", "Pause voice" if screen["enabled"] else "Resume voice",
                primary=not screen["enabled"])
    elif kind == "t3-choices":
        edge()
        position(screen["index"], screen["total"])
        curved(f"Open T3 windows · {screen['index']} of {screen['total']}", color=ACCENT, size=14)
        block(screen["title"], 200, 28, 3, 278, bold=True, centered=True)
        text(fit_label(screen["project"], 280, 13), 273, 13, MUTED)
        text("Choose where your voice goes", 298, 14, MUTED)
        text("BOOT: refresh open windows", 319, 11, MUTED)
        arrows(screen["total"])
        actions("Projects", "Use session", primary=True)
    elif kind in {"current-error", "voice-offline"}:
        edge(ACCENT)
        curved("Voice destination", color=ACCENT)
        block("Choose a T3 session" if kind == "current-error" else "Reconnect T3 voice",
              174, 27, 2, 280, bold=True, centered=True)
        block(screen["error"], 230, 16, 4, 285, MUTED)
        text("BOOT or Retry to reconnect", 319, 12, MUTED)
        actions("Projects", "Retry", primary=True)
    elif kind == "projects":
        edge()
        position(screen["index"], screen["total"])
        curved("PROJECT LIBRARY", color=MUTED, size=13)
        plate(f"PROJECT / {screen['index']:02d} OF {screen['total']:02d}")
        block(screen["project"], 214, 27, 3, 282, bold=True, centered=True)
        text(fit_label(screen.get("path", ""), 280, 12), 278, 12, MUTED)
        text(f"{screen['sessions']:02d} SESSION" + ("S" if screen['sessions'] != 1 else ""),
             301, 12, MUTED, mono=True)
        text("BOOT: use open T3 session", 319, 11, MUTED)
        arrows(screen["total"])
        actions("New session", "Open sessions", primary=True)
    elif kind == "empty-project":
        edge()
        curved(screen["project"], color=INK, size=15)
        block("Ready for a new session", 204, 28, 3, 280, bold=True, centered=True)
        text("No active sessions in this project", 287, 14, MUTED)
        actions("Projects", "New session", primary=True)
    elif kind == "listening":
        # The firmware owns the ONE live circular waveform. No host waveform,
        # static recording ring, or second audio indicator is drawn underneath.
        curved(screen["title"], radius=160, color=MUTED, size=14, width=255)
        text("MIC / RECORDING", 144, 11, PINK, mono=True)
        text("Listening", 201, 37, bold=True)
        text("Release to send" if screen["autoSend"] else "Release to review", 272, 17, INK)
        text("DESTINATION LOCKED", 315, 11, MUTED, mono=True)
    elif kind == "review":
        edge()
        curved(screen.get("title", "Review prompt"), color=MUTED, size=14)
        position(screen["page"] + 1, screen["pages"])
        plate(f"REVIEW / {screen['page'] + 1:02d} OF {screen['pages']:02d}", y=98)
        for i, line in enumerate(screen["lines"]): text(line, 133 + i * 29, 20)
        actions("Discard", "Next" if screen["page"] + 1 < screen["pages"] else "Continue", primary=True)
    elif kind == "confirm":
        edge()
        curved("SEND TO SESSION", color=ACCENT, size=14)
        plate("FINAL CHECK")
        block(screen["title"], 228, 23, 5, 300, bold=True, centered=True)
        text("Tap Send to deliver your prompt", 310, 13, MUTED)
        actions("Back", "Send", primary=True)
    elif kind == "scene":
        scene = screen["scene"]
        if scene["kind"] == "image":
            source = io.BytesIO(base64.b64decode(scene["image"].split(",", 1)[1]))
            with Image.open(source) as artwork:
                image = ImageOps.fit(artwork.convert("RGB"), image.size)
            draw = PanelDraw(image)
            text = draw.text
            draw.rounded_rectangle((67, 318, 399, 390), radius=3, fill=SURFACE)
            block(scene["title"], 335, 20, 2, 305)
        elif scene["kind"] == "chart":
            edge()
            values = scene["values"]; low, high = min(values), max(values)
            points = [(65 + i * 336 / (len(values) - 1), 272 - (v - low) * 133 / (high - low or 1)) for i, v in enumerate(values)]
            for y in (139, 206, 272): draw.line([(65, y), (401, y)], fill=LINE)
            draw.line(points, fill=ACCENT, width=3)
            x, y = points[-1]
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=INK)
            block(scene["title"], 320, 20, 2, 305)
        else:
            edge()
            curved("T3 / VISUAL OUTPUT", color=MUTED, size=13)
            block(scene["title"], 211, 29, 3, 306, bold=True, centered=True)
            if scene.get("detail"): block(scene["detail"], 286, 16, 3, 290, MUTED)
        return_hint()
    else:
        edge(ACCENT if kind in {"error", "empty", "control-error", "uncertain"} else LINE)
        title, detail = {
            "transcribing": ("Transcribing", "Next: send to this session" if screen.get("autoSend")
                              else "Next: review your words"),
            "sending": ("Sending", "Waiting for T3 to confirm receipt"),
            "sent": ("Sent", ""),
            "empty": ("Didn't catch that", "No words were detected"),
            "control-error": ("Couldn't make that change", "Check the project in T3"),
            "error": ("Voice interrupted", "Please record your prompt again"),
            "uncertain": ("Check your session", "The send wasn't acknowledged.\nYour prompt won't be resent."),
            "offline": ("Reconnecting", "Check that T3 is running"),
            "empty-home": ("Choose a session", "Open a T3 conversation,\nthen press BOOT to select it"),
        }.get(kind, ("Ready", "Hold to talk"))
        if kind in {"error", "control-error"} and screen.get("error"):
            detail = screen["error"]
            if detail == "USB sender restarted. Hold to record again.":
                detail = "Please record your prompt again"
        if screen.get("title"): curved(screen["title"], size=14, color=MUTED, width=245)
        if kind == "sent":
            draw.rounded_rectangle((202, 161, 264, 223), radius=3, fill=ACCENT)
            draw.line([(216, 192), (228, 204), (251, 179)], fill=SURFACE, width=3)
            text(title, 245, 35, bold=True)
            text("PROMPT ACCEPTED", 300, 11, MUTED, mono=True)
        else:
            labels = {"transcribing": "SPEECH TO TEXT", "sending": "PROMPT / SENDING",
                      "empty": "VOICE / NO SPEECH", "error": "VOICE / INTERRUPTED",
                      "control-error": "CHANGE / NOT APPLIED", "uncertain": "SEND / UNCONFIRMED",
                      "offline": "T3 / DISCONNECTED", "empty-home": "VOICE / SETUP"}
            if kind in labels: plate(labels[kind])
            block(title, 216, 31, 3, 300, centered=True)
            if detail: block(detail, 293, 16, 3, 298, MUTED)
        if kind in {"empty", "control-error", "error", "uncertain"}: return_hint()
    mask = Image.new("L", image.size)
    ImageDraw.Draw(mask).ellipse((SCALE, SCALE, 465 * SCALE, 465 * SCALE), fill=255)
    image.paste((0, 0, 0), mask=ImageOps.invert(mask))
    return image.resize((466, 466), Image.Resampling.LANCZOS)


def pixels565(image):
    return rgb565(image)


def same_home_controls(a, b):
    """Relative navigation/toggle controls retain their meaning across home cards."""
    return bool(a and b and a["kind"] == b["kind"] == "home"
                and not a.get("new") and not b.get("new")
                and bool(a.get("browser")) == bool(b.get("browser"))
                and (not a.get("browser") or a.get("threadId") == b.get("threadId"))
                and a.get("projectId") == b.get("projectId"))


def same_home_destination(a, b):
    """Metadata can change without a new destination or a change to voice controls."""
    return (same_home_controls(a, b) and a.get("threadId") == b.get("threadId")
            and a.get("enabled") == b.get("enabled") and a.get("autoSend") == b.get("autoSend"))


class DeviceUI:
    def __init__(self, display, port, service, selection=DEFAULT_SELECTION, auto_send=True, api=None,
                 idle_seconds=60, clock=None, record_pending=None, character=None, motion=None):
        if type(idle_seconds) not in (int, float) or not math.isfinite(idle_seconds) or idle_seconds < 0:
            raise ValueError("Idle seconds must be a finite number of seconds, or 0 to disable.")
        self.display, self.port, self.service = display, port, service
        self.clock = clock or time.monotonic
        self.idle_seconds = idle_seconds
        self.idle_supported = False
        self.idle_active = False
        self.last_activity = self.clock()
        self.last_idle_input = None
        self.last_wake = None
        self.force_new_token = False
        self.speech = None
        self.record_pending = record_pending or (lambda: False)
        character = character if character is not None else os.environ.get("SHARK_CHARACTER", "reef")
        try:
            self.character = None if character == "none" else load_character(character)
        except ValueError:
            print("Character pack unavailable or invalid; using Reef shark.", flush=True)
            self.character = load_character("reef")
        self.character_motion = motion if motion is not None else os.environ.get("SHARK_CHARACTER_MOTION", "on") != "off"
        self.pending_passive_frame = False
        self.api = api or (lambda path, value=None, method=None: bridge_request(port, path, value, method, timeout=15))
        self.selection = Path(selection)
        self.auto_send = auto_send
        self.order = []
        self.last_event = None
        self.presented = None
        self.presented_key = None
        self.frame_token = secrets.randbelow(1000000000) + 1
        self.presented_token = None
        self.page = 0
        self.confirming = False
        self.last_job = None
        self.state_since = self.clock()
        self.dismissed_job = None
        self.dismissed_scene = None
        self.draft_source = None
        self.last_refresh = 0
        self.last_paint = 0
        self.snapshot = None
        self.listen = None
        self.response_samples = []
        self.choosing_project = True
        self.project_cursor = ""
        self.control_error = None
        self.current_error = False
        self.t3_choices = []
        self.t3_cursor = 0
        self.empty_project = None
        try:
            saved = json.loads(self.selection.read_text()).get("projectSessions", {})
            self.project_sessions = saved if isinstance(saved, dict) else {}
        except (OSError, ValueError): self.project_sessions = {}

    def projects(self):
        groups = {p["id"]: {**p, "threads": []} for p in self.snapshot.get("projects", [])}
        for thread in self.snapshot["threads"]:
            key = thread.get("projectId")
            if key not in groups and "projects" not in self.snapshot:
                groups[key] = {"id": key, "title": thread.get("project", "Project"), "threads": []}
            if key in groups: groups[key]["threads"].append(thread["id"])
        return sorted(groups.values(), key=lambda p: (p["title"].casefold(), p["id"]))

    def session_ids(self, project_id):
        known = {t["id"] for t in self.snapshot["threads"] if t.get("projectId") == project_id}
        return [i for i in self.order if i in known]

    def open_projects(self, project_id):
        self.t3_choices = []
        self.control_error = None
        self.current_error = False
        self.project_cursor = project_id
        self.choosing_project = True
        self.empty_project = None

    def start_new(self, project_id, source=None):
        self.listen = self.api("/api/listen/new", {"threadId": source, "projectId": project_id,
                                                    "autoSend": self.auto_send})
        self.draft_source = source
        self.empty_project = None
        self.choosing_project = False
        self.dismiss_visual()

    def dismiss_visual(self):
        scene = self.snapshot.get("overlay") if self.snapshot else None
        if scene: self.dismissed_scene = scene["expiresAt"]

    def report_presented(self, health):
        status = health.get("ui", {})
        version = status.get("presentedVersion", 0)
        pending = []
        for sample in self.response_samples:
            if version < sample["imageVersion"]:
                pending.append(sample)
            elif version == sample["imageVersion"]:
                elapsed = (status["presentedMs"] - sample["releasedMs"]) & 0xffffffff
                print("Response: " + json.dumps({**sample, "releaseToDisplayMs": elapsed}), flush=True)
            else:
                print("Response: " + json.dumps({**sample, "coalesced": True}), flush=True)
        self.response_samples = pending

    def save_selection(self, thread_id):
        if (self.listen.get("target") or {}).get("browser"): return
        thread = next((t for t in self.snapshot["threads"] if t["id"] == thread_id), self.listen.get("target") or {})
        project_id = thread.get("projectId")
        if project_id: self.project_sessions[project_id] = thread_id
        self.selection.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.selection.with_suffix(".tmp")
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as out:
            json.dump({"threadId": thread_id, "projectId": project_id, "projectSessions": self.project_sessions}, out)
        os.replace(temporary, self.selection)

    def select(self, thread_id):
        enabled = self.listen["enabled"] if self.listen and self.listen["target"] else True
        self.listen = self.api("/api/listen/configure", {"threadId": thread_id, "enabled": enabled, "autoSend": self.auto_send})
        self.save_selection(thread_id)
        self.dismiss_visual()
        self.page = 0; self.confirming = False

    def use_current(self, selected):
        thread_id = selected.get("threadId")
        if not selected.get("browser") and not any(t["id"] == thread_id and t.get("projectId") == selected.get("projectId")
                   for t in self.snapshot["threads"]):
            raise ValueError("That T3 conversation is unavailable on this device. Try again.")
        self.listen = self.api("/api/listen/configure", {"threadId": thread_id,
            "enabled": True, "autoSend": self.auto_send})
        self.save_selection(thread_id)
        self.dismiss_visual()
        self.choosing_project = False
        self.empty_project = None
        self.control_error = None
        self.current_error = False
        self.t3_choices = []
        self.dismissed_job = (self.listen.get("job") or {}).get("id")
        self.page = 0; self.confirming = False
        return "current-t3"

    def choose_current(self):
        # Quiesce capture before the lookup, and retain its destination on error.
        self.current_error = True
        self.dismissed_job = (self.listen.get("job") or {}).get("id")
        self.display.request("mic.configure", enabled=False)
        self.display.request("mic.clear")
        selected = self.api("/api/current-session")
        choices = selected.get("choices")
        if choices:
            self.t3_choices, self.t3_cursor = choices, 0
            self.control_error = None
            self.dismissed_job = (self.listen.get("job") or {}).get("id")
            self.dismiss_visual()
            return "choose-t3"
        return self.use_current(selected)

    def refresh(self):
        self.snapshot = self.api("/api/status")
        self.listen = self.api("/api/listen")
        if self.idle_supported and self.idle_seconds:
            # Device audio flags cannot see speech still queued or synthesizing
            # on the host. Include that work before deciding it is safe to dim.
            self.speech = self.api("/api/speech")
        threads = self.snapshot["threads"]
        known = {t["id"] for t in threads}
        self.order = [i for i in self.order if i in known] + [t["id"] for t in threads if t["id"] not in self.order]
        target = self.listen["target"]
        job = self.listen["job"]
        idle = not job or job["state"] in FINISHED
        if self.order and idle and (not target or (target["id"] not in known and not target.get("new") and not target.get("browser"))):
            try:
                saved = json.loads(self.selection.read_text())["threadId"]
            except (OSError, ValueError, KeyError):
                saved = None
            project_id = target.get("projectId") if target else None
            candidates = self.session_ids(project_id) if target else self.order
            if candidates:
                self.select(saved if saved in candidates else candidates[0])
            else:
                self.choosing_project = True  # Never silently jump to another project.
        elif target and idle and self.listen["autoSend"] != self.auto_send:
            self.listen = self.api("/api/listen/configure", {"threadId": target["id"],
                "enabled": self.listen["enabled"], "autoSend": self.auto_send})
        if target and not target.get("new") and getattr(self, "saved_target", None) != target["id"]:
            self.save_selection(target["id"])
            self.saved_target = target["id"]

    def screen(self):
        target = self.listen["target"] or {}
        job = self.listen["job"]
        identity = (job["id"], job["state"]) if job else None
        if identity != self.last_job:
            self.last_job = identity; self.state_since = self.clock()
            self.page = 0; self.confirming = False
        state = job["state"] if job else None
        if job and state in {"listening", "transcribing", "sending"}:
            return {"kind": state, "title": job["threadTitle"], "autoSend": job.get("autoSend", False), "jobId": job["id"]}
        if job and state == "review":
            if self.confirming:
                return {"kind": "confirm", "title": job["threadTitle"], "jobId": job["id"],
                        "threadId": job["threadId"], "text": job["text"]}
            wrapped = lines(job["text"], 330, 20)
            pages = [wrapped[i:i + 7] for i in range(0, len(wrapped), 7)]
            self.page = min(self.page, len(pages) - 1)
            return {"kind": "review", "lines": pages[self.page], "page": self.page,
                    "pages": len(pages), "jobId": job["id"], "threadId": job["threadId"],
                    "title": job["threadTitle"]}
        if job and state in {"sent", "empty", "error", "uncertain"} and job["id"] != self.dismissed_job:
            if state != "sent" or self.clock() - self.state_since < 1.5:
                return {"kind": state, "jobId": job["id"], "title": job["threadTitle"],
                        "error": job.get("error", "")}
        if not self.snapshot["connected"]: return {"kind": "offline"}
        if self.control_error:
            return {"kind": "current-error" if self.current_error else "control-error", "error": self.control_error}
        if self.t3_choices:
            selected = self.t3_choices[self.t3_cursor]
            return {"kind": "t3-choices", "threadId": selected["threadId"], "title": selected["title"],
                    "project": " · ".join(filter(None, (selected.get("environment"), selected.get("project")))),
                    "index": self.t3_cursor + 1, "total": len(self.t3_choices)}
        projects = self.projects()
        if not projects and not target.get("new") and not target.get("browser"): return {"kind": "empty-home"}
        if self.choosing_project and projects:
            ids = [p["id"] for p in projects]
            if self.project_cursor not in ids:
                thread = next((t for t in self.snapshot["threads"] if t["id"] == target.get("id")), target)
                self.project_cursor = thread.get("projectId") if thread.get("projectId") in ids else ids[0]
            project = projects[ids.index(self.project_cursor)]
            return {"kind": "projects", "projectId": project["id"], "project": project["title"],
                    "path": project.get("path", ""),
                    "sessions": len(project["threads"]), "index": ids.index(project["id"]) + 1, "total": len(ids)}
        if self.empty_project:
            return {"kind": "empty-project", "projectId": self.empty_project["projectId"],
                    "project": self.empty_project["project"]}
        if target.get("browser") and self.listen.get("destinationReady") is False:
            return {"kind": "voice-offline", "error": self.listen["destinationError"]}
        scene = self.snapshot.get("overlay")
        if scene and scene["expiresAt"] != self.dismissed_scene:
            return {"kind": "scene", "scene": scene}
        if not target or (not target.get("new") and not target.get("browser") and target.get("id") not in self.order):
            self.choosing_project = True
            return self.screen()
        thread = next((t for t in self.snapshot["threads"] if t["id"] == target["id"]), target)
        order = self.session_ids(thread.get("projectId"))
        return {"kind": "home", "threadId": target["id"], "title": thread["title"],
                "character": self.character.id if self.character else None,
                "activity": selected_state(self.snapshot, target),
                "project": ((target.get("environment", "T3") + " · ") if target.get("browser") else "") + thread.get("project", "Project"), "projectId": thread.get("projectId"),
                "browser": target.get("browser", False),
                "enabled": self.listen["enabled"], "new": target.get("new", False),
                "autoSend": self.listen["autoSend"],
                "index": 1 if target.get("browser") else order.index(target["id"]) + 1 if target["id"] in order else len(order) + 1,
                "total": 1 if target.get("browser") else len(order) + bool(target.get("new"))}

    def event(self, event, screen):
        gesture, x, y = event["gesture"], event["x"], event["y"]
        kind = screen["kind"]
        if gesture == 5:
            if kind not in {"home", "projects", "empty-project", "empty-home", "scene",
                            "sent", "empty", "error", "uncertain", "control-error",
                            "current-error", "voice-offline", "t3-choices"}:
                return
            return self.choose_current()
        right, left = 255 <= x <= 420 and 327 <= y <= 452, 46 <= x <= 212 and 327 <= y <= 452
        if kind == "t3-choices":
            next_choice = gesture == 2 or (gesture == 1 and x >= 370 and 130 <= y <= 315)
            previous_choice = gesture == 3 or (gesture == 1 and x <= 96 and 130 <= y <= 315)
            if next_choice or previous_choice:
                self.t3_cursor = (self.t3_cursor + (1 if next_choice else -1)) % len(self.t3_choices)
                return "next-t3" if next_choice else "previous-t3"
            if gesture == 1 and right:
                return self.use_current(self.t3_choices[self.t3_cursor])
            if gesture == 1 and left:
                self.open_projects(None)
                return "projects"
        elif kind in {"current-error", "voice-offline"} and gesture == 1:
            if right: return self.choose_current()
            if left:
                self.open_projects(None)
                return "projects"
        elif kind == "home":
            if screen.get("browser") and (gesture in {2, 3}): return
            if screen.get("browser") and gesture == 1 and left:
                self.open_projects(None)
                return "projects"
            order = self.session_ids(screen.get("projectId"))
            if gesture == 1 and 90 <= x <= 376 and 45 <= y <= 126:
                self.open_projects(screen.get("projectId"))
                return "projects"
            next_session = gesture == 2 or (gesture == 1 and x >= 370 and 130 <= y <= 315)
            previous_session = gesture == 3 or (gesture == 1 and x <= 96 and 130 <= y <= 315)
            if (next_session or previous_session) and order and not screen.get("new") and not screen.get("browser"):
                index = order.index(screen["threadId"]) if screen["threadId"] in order else (-1 if next_session else 0)
                self.select(order[(index + (1 if next_session else -1)) % len(order)])
                return "next" if next_session else "previous"
            elif gesture == 1 and left:
                if screen["new"]:
                    if order:
                        self.select(self.draft_source if self.draft_source in order else order[0])
                    else:
                        self.listen = self.api("/api/listen/configure", {"threadId": None, "enabled": False})
                        self.choosing_project = True
                else:
                    self.start_new(screen.get("projectId"), screen["threadId"])
                return "cancel-new" if screen["new"] else "new"
            elif gesture == 1 and right:
                self.listen = self.api("/api/listen/configure", {"threadId": screen["threadId"],
                    "enabled": not screen["enabled"], "autoSend": self.auto_send})
                return "pause" if screen["enabled"] else "resume"
        elif kind == "projects":
            projects = self.projects()
            ids = [p["id"] for p in projects]
            if screen["projectId"] not in ids: return
            next_project = gesture == 2 or (gesture == 1 and x >= 370 and 130 <= y <= 315)
            previous_project = gesture == 3 or (gesture == 1 and x <= 96 and 130 <= y <= 315)
            if next_project or previous_project:
                self.project_cursor = ids[(ids.index(screen["projectId"]) + (1 if next_project else -1)) % len(ids)]
                return "next-project" if next_project else "previous-project"
            project_card = 100 <= x <= 366 and 120 <= y <= 315
            if gesture == 1 and left:
                order = self.session_ids(screen["projectId"])
                saved = self.project_sessions.get(screen["projectId"])
                self.start_new(screen["projectId"], saved if saved in order else next(iter(order), None))
                return "new"
            if gesture == 1 and (right or project_card):
                target = self.listen["target"] or {}
                self.dismiss_visual()
                if target.get("new") and target.get("projectId") == screen["projectId"]:
                    self.choosing_project = False
                    return "open-project"
                order = self.session_ids(screen["projectId"])
                saved = self.project_sessions.get(screen["projectId"])
                if order:
                    self.select(saved if saved in order else order[0])
                else:
                    self.empty_project = screen
                self.choosing_project = False
                return "open-project"
        elif kind == "empty-project" and gesture == 1:
            if left:
                self.open_projects(screen["projectId"])
                return "projects"
            if right:
                self.start_new(screen["projectId"])
                return "new"
        elif kind == "review":
            if gesture == 1 and left:
                self.listen = self.api("/api/listen", method="DELETE")
            elif gesture == 2 or (gesture == 1 and right):
                if self.page + 1 < screen["pages"]: self.page += 1
                elif gesture == 1: self.confirming = True
            elif gesture == 3: self.page = max(0, self.page - 1)
        elif kind == "confirm" and gesture == 1:
            if left: self.confirming = False
            elif right:
                self.listen = self.api("/api/listen/confirm", {"id": screen["jobId"],
                    "threadId": screen["threadId"], "text": screen["text"], "confirm": True})
        elif kind == "scene" and gesture:
            self.dismissed_scene = screen["scene"]["expiresAt"]
        elif kind in {"sent", "empty", "error", "uncertain"} and gesture == 1:
            self.dismissed_job = screen["jobId"]
        elif kind == "control-error" and gesture == 1:
            self.control_error = None

    def idle_allowed(self, screen, health, check_frame=True):
        """Only ordinary browsing can rest; actionable work stays visible."""
        if self.record_pending():
            return False
        if screen["kind"] not in {"home", "projects", "empty-project", "empty-home", "offline"}:
            return False
        if (self.listen.get("target") or {}).get("new") or self.control_error or self.t3_choices:
            return False
        scene = self.snapshot.get("overlay")
        if scene and scene["expiresAt"] != self.dismissed_scene:
            return False
        status = health.get("ui", {})
        if health.get("touch", {}).get("pressed") or status.get("buttonPressed") or status.get("wakePending"):
            return False
        if any(health.get("mic", {}).get(key) for key in ("recording", "available")):
            return False
        if any(health.get("audio", {}).get(key) for key in ("uploading", "armed", "playing")):
            return False
        if check_frame and status.get("imageVersion") != status.get("presentedVersion"):
            return False
        if self.speech is None:
            return False
        speech_job = self.speech.get("job")
        return not speech_job or speech_job.get("state") in SPEECH_FINISHED

    def wake(self):
        # Never configure ListenStore here: waking preserves both destination
        # and Pause. Firmware consumes the full waking contact and holds its
        # microphone gate until this replacement frame has actually been drawn.
        result = self.display.request("ui.idle", enabled=False)
        self.idle_active = False
        self.last_activity = self.clock()
        self.presented_key = None
        self.force_new_token = True
        if isinstance(result, dict) and type(result.get("ui", {}).get("wake")) is int:
            self.last_wake = result["ui"]["wake"]

    def enter_idle(self):
        try:
            result = self.display.request("ui.idle", enabled=True)
        except ProtocolError as error:
            if str(error) != "Device rejected ui.idle: ui_idle_busy":
                raise
            # A real contact/capture/upload can start after the last USB status.
            # It is activity, not a broken connection or a reason to retry voice.
            self.last_activity = self.clock()
            return
        if result.get("ui", {}).get("idle") is True:
            self.idle_active = True
        else:
            # A contact may have woken the board before its acknowledgement.
            self.wake()

    def tick(self, health):
        self.report_presented(health)
        now = self.clock()
        status = health.get("ui", {})
        self.idle_supported = status.get("idleMode") is True
        if now - self.last_refresh >= .2:
            self.refresh(); self.last_refresh = now
        if self.listen is None: return
        screen = self.screen()
        key = json.dumps(screen, sort_keys=True)
        physical = tuple(status.get(key) for key in ("contacts", "releases", "event", "wake")) + (
            health.get("touch", {}).get("taps"),)
        activity = self.last_idle_input is not None and physical != self.last_idle_input
        self.last_idle_input = physical
        if activity or health.get("touch", {}).get("pressed") or status.get("buttonPressed"):
            self.last_activity = now
        wake_changed = (self.last_wake is not None and type(status.get("wake")) is int
                        and status["wake"] != self.last_wake)
        if type(status.get("wake")) is int: self.last_wake = status["wake"]
        was_idle = self.idle_active or status.get("idle") is True
        suppress_input = was_idle or wake_changed or status.get("wakePending") is True
        if (self.presented is not None and health.get("imageActive") is False
                and status.get("active") is True and not was_idle and not status.get("wakePending")):
            # A UI lease can expire during a host stall. Renewing it leaves the
            # old image cleared; restore the same destination now, not at the
            # ordinary 500-second image refresh deadline.
            self.presented_key = None
            self.force_new_token = True
            self.last_activity = now
            suppress_input = True
        eligible = self.idle_allowed(screen, health)
        if not eligible and not (self.pending_passive_frame and self.idle_allowed(screen, health, check_frame=False)):
            self.last_activity = now
        if self.idle_supported and (wake_changed
                or (status.get("idle") is True and not self.idle_active)
                or (self.idle_active and (status.get("idle") is not True
                    or activity or not eligible or not self.idle_seconds))):
            self.wake()
        queued = status.get("eventQueue") is True
        events = status.get("events", []) if queued else [status]
        acted = []
        if self.last_event is None:
            self.last_event = status.get("event")
            events = []  # Do not replay input from before this sender connected.
        for event in events:
            if event.get("event") == self.last_event: continue
            self.last_event = event.get("event")
            mic = health.get("mic", {})
            # The microphone can start between ListenPump's poll and this status
            # read. Keep its destination fixed before the bridge claims capture.
            capturing = mic.get("recording") or mic.get("available")
            project_navigation = (screen["kind"] == "projects" and self.presented
                and self.presented["kind"] == "projects" and (event.get("gesture") in {2, 3}
                    or (event.get("gesture") == 1 and (event.get("x", 233) <= 96 or event.get("x", 233) >= 370)
                        and 130 <= event.get("y", 0) <= 315)))
            compatible = not suppress_input and not capturing and (key == self.presented_key
                or (queued and (same_home_controls(screen, self.presented) or project_navigation)))
            action = None
            screen_kind = screen["kind"]
            visible_project = screen.get("projectId")
            print(f"Touch: gesture={event.get('gesture')}, position=({event.get('x')},{event.get('y')}), "
                  f"frameMatches={event.get('eventFrame') == self.presented_token}, "
                  f"controlsMatch={compatible}", flush=True)
            if event.get("eventFrame") == self.presented_token and compatible:
                if event.get("gesture") == 4:
                    pass  # Firmware consumed this tap to start or stop speech.
                elif event.get("gesture") != 5 and health.get("audio", {}).get("playing"):
                    self.display.request("audio.stop")
                else:
                    try:
                        action = self.event(event, screen)
                    except (HTTPError, URLError, TimeoutError, ValueError) as error:
                        if isinstance(error, HTTPError):
                            try: detail = json.load(error).get("error")
                            except (ValueError, OSError): detail = None
                        elif isinstance(error, (URLError, TimeoutError)):
                            detail = "T3 did not respond. Check its connection, then retry."
                        else: detail = str(error)
                        self.control_error = detail or "Please try that action again."
                        print("Control: rejected; destination remains unchanged.", flush=True)
                screen = self.screen(); key = json.dumps(screen, sort_keys=True)
            if "releasedMs" in event:
                sample = {"event": event["event"], "gesture": event["gesture"], "x": event["x"], "y": event["y"],
                          "screenKind": screen_kind, "visibleProjectId": visible_project,
                          "selectedProjectId": (self.listen.get("target") or {}).get("projectId"),
                          "releasedMs": event["releasedMs"], "action": action,
                          "hostArrivalMs": (status["nowMs"] - event["releasedMs"]) & 0xffffffff}
                print("Input: " + json.dumps(sample), flush=True)
                if action: acted.append(sample)
        if queued and status.get("events"):
            self.display.request("ui.ack", event=status["events"][-1]["event"])
        if self.idle_active:
            return  # Firmware animates; keep polling and renewing the UI lease.
        if (self.idle_supported and self.idle_seconds and self.idle_allowed(screen, health)
                and now - self.last_activity >= self.idle_seconds
                and (key == self.presented_key or same_home_destination(screen, self.presented))
                and not self.force_new_token):
            self.enter_idle()
            if self.idle_active: return
        # Motion is presentation only. It must not renew the inactivity clock,
        # invalidate a touch token, or compete with the native recording ring.
        animate = (self.character_motion and self.character and screen["kind"] == "home"
            and not screen.get("new") and not self.record_pending()
            and not health.get("touch", {}).get("pressed") and not status.get("buttonPressed")
            and not any(health.get("mic", {}).get(k) for k in ("recording", "available"))
            and status.get("imageVersion") == status.get("presentedVersion")
            and (not self.idle_supported or not self.idle_seconds
                 or now - self.last_activity < self.idle_seconds - 1))
        animation_due = animate and now - self.last_paint >= 1 / FPS
        if key != self.presented_key or now - self.last_paint > 500 or animation_due:
            frame = int(now * FPS) % FRAMES if self.character_motion else 0
            pixels = pixels565(render(screen, self.character, frame))
            # Keep home navigation live while its labels/image change. New,
            # review, confirmation, and voice screens retain distinct tokens.
            if self.force_new_token or not (key == self.presented_key
                    or queued and same_home_controls(screen, self.presented)):
                self.frame_token = self.frame_token % 2147483646 + 1
            self.pending_passive_frame = key == self.presented_key or same_home_destination(screen, self.presented)
            result = self.display.send_frame(pixels, seconds=600, service=self.service, ui_token=self.frame_token,
                                             hold_to_talk=(screen["kind"] == "home" and screen["enabled"])
                                                 or screen["kind"] == "listening")
            print(f"Display: screen={screen['kind']}, upload={result.get('uploadSeconds')}s, "
                  f"token={self.frame_token:08x}", flush=True)
            self.presented_token = f"{self.frame_token:08x}"
            self.presented = screen; self.presented_key = key; self.last_paint = now
            self.force_new_token = False
            version = result.get("ui", {}).get("imageVersion")
            if version is not None:
                self.response_samples.extend({**sample, "imageVersion": version} for sample in acted)
                self.report_presented(result)
        elif acted:
            for sample in acted:
                print("Response: " + json.dumps({**sample, "coalesced": True}), flush=True)

    def close(self):
        self.display.request("ui.configure", enabled=False)
