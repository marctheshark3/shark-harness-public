"""Bounded, declarative companion artwork. Characters never control the device.

Coordinates use a 100 × 100 drawing space. State and motion belong to this
renderer; packs supply only silhouettes, colors, and face anchors.
"""
from dataclasses import dataclass
from functools import lru_cache
import json
import math
from pathlib import Path
import re

PACKS = Path(__file__).resolve().parent / "characters"
MAX_PACK_BYTES = 32768
FRAMES = 8
FPS = 2
STATES = {
    "working": ("Cooking", "#ffad72"),
    "waiting": ("Needs you", "#f4d581"),
    "error": ("Check T3", "#ff8792"),
    "idle": ("At rest", "#b9d9cd"),
    "done": ("Turn finished", "#b9d9cd"),
    "offline": ("Offline", "#a8aba8"),
    "unknown": ("State unavailable", "#a8aba8"),
}


@dataclass(frozen=True)
class Shape:
    kind: str
    coordinates: tuple
    fill: str


@dataclass(frozen=True)
class Character:
    id: str
    name: str
    author: str
    license: str
    palette: tuple
    shapes: tuple
    eyes: tuple
    mouth: tuple


def selected_state(snapshot, target):
    """Report only the shown destination; a remote target may have no telemetry."""
    if not snapshot.get("connected"):
        return "offline"
    if target.get("new"):
        return "idle"
    thread_id = target.get("telemetryThreadId") or target.get("id")
    thread = next((t for t in snapshot.get("threads", []) if t["id"] == thread_id), {})
    state = thread.get("state")
    return state if state in STATES else "unknown"


def _keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError("Character fields must be exactly: " + ", ".join(expected))


def _point(value):
    if (not isinstance(value, list) or len(value) != 2
            or any(type(n) not in (float, int) or not 0 <= n <= 100 for n in value)):
        raise ValueError("Character coordinates must be finite numbers from 0 to 100.")
    return tuple(value)


def parse_character(value):
    """Validate untrusted artwork once; no code, asset paths, or URLs are accepted."""
    _keys(value, ("version", "id", "name", "author", "license", "palette", "shapes", "eyes", "mouth"))
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("Unsupported character version; expected 1.")
    for field, limit in (("id", 32), ("name", 48), ("author", 100)):
        text = value[field]
        if (not isinstance(text, str) or not text.strip() or len(text) > limit
                or any(ord(c) < 32 or ord(c) == 127 for c in text)):
            raise ValueError(f"Invalid character {field}.")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", value["id"]):
        raise ValueError("Character id must use lowercase letters, digits and hyphens.")
    if value["license"] not in ("Apache-2.0", "CC0-1.0", "MIT"):
        raise ValueError("Character license must be Apache-2.0, CC0-1.0 or MIT.")
    palette = value["palette"]
    if not isinstance(palette, dict) or not 1 <= len(palette) <= 12:
        raise ValueError("A character needs 1–12 palette colors.")
    for key, color in palette.items():
        if (not isinstance(key, str) or not re.fullmatch(r"[a-z]{1,16}", key)
                or not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color)):
            raise ValueError("Palette entries must be named six-digit hex colors.")
    shapes = value["shapes"]
    if not isinstance(shapes, list) or not 1 <= len(shapes) <= 24:
        raise ValueError("A character needs 1–24 shapes.")
    parsed = []
    for shape in shapes:
        if not isinstance(shape, dict) or shape.get("kind") not in ("blob", "ellipse"):
            raise ValueError("Character shapes must be blobs or ellipses.")
        kind = shape["kind"]
        geometry = "points" if kind == "blob" else "box"
        _keys(shape, ("kind", geometry, "fill"))
        if not isinstance(shape["fill"], str) or shape["fill"] not in palette:
            raise ValueError("Shape fill must name a palette color.")
        points = shape[geometry]
        if kind == "blob":
            if not isinstance(points, list) or not 3 <= len(points) <= 32:
                raise ValueError("A blob needs 3–32 points.")
            coordinates = tuple(_point(p) for p in points)
        else:
            if not isinstance(points, list) or len(points) != 4:
                raise ValueError("An ellipse needs [left, top, right, bottom].")
            coordinates = _point(points[:2]) + _point(points[2:])
            if coordinates[0] >= coordinates[2] or coordinates[1] >= coordinates[3]:
                raise ValueError("An ellipse must have positive width and height.")
        parsed.append(Shape(kind, coordinates, shape["fill"]))
    if not isinstance(value["eyes"], list) or len(value["eyes"]) != 2:
        raise ValueError("A character needs two eye anchors.")
    return Character(value["id"], value["name"], value["author"], value["license"],
                     tuple(sorted(palette.items())), tuple(parsed),
                     tuple(_point(p) for p in value["eyes"]), _point(value["mouth"]))


def load_character(name="reef"):
    """Load a bundled id or an explicit local JSON path, with a fixed size limit."""
    name = str(name)
    path = PACKS / f"{name}.json" if re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name) else Path(name)
    try:
        with path.open("rb") as source:
            data = source.read(MAX_PACK_BYTES + 1)
        if len(data) > MAX_PACK_BYTES:
            raise ValueError("Character packs must be at most 32 KiB.")
        return parse_character(json.loads(data))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f"Cannot load character {path.name}: {error}") from error


def _curve(points):
    """A closed Catmull–Rom contour gives contributors smooth organic forms."""
    contour = []
    for index, p1 in enumerate(points):
        p0, p2, p3 = points[index - 1], points[(index + 1) % len(points)], points[(index + 2) % len(points)]
        for step in range(12):
            t = step / 12
            contour.append(tuple(.5 * (2*b + (-a+c)*t + (2*a-5*b+4*c-d)*t*t
                                       + (-a+3*b-3*c+d)*t*t*t) for a, b, c, d in zip(p0, p1, p2, p3)))
    return contour


@lru_cache(maxsize=64)
def render_character(character, state, frame=0, size=272):
    """Render a sprite; its private canvas clips every contour before compositing."""
    from PIL import Image, ImageDraw
    if state not in STATES:
        state = "unknown"
    phase = frame % FRAMES / FRAMES * math.tau
    bob = math.sin(phase) * (2 if state == "working" else .6)
    image = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(image)
    scale = size / 100
    color = dict(character.palette)
    ink = "#172329"

    def xy(point):
        return point[0] * scale, (point[1] + bob) * scale

    def line(points, fill=ink, width=1.4):
        draw.line([xy(p) for p in points], fill=fill, width=max(1, round(width * scale)), joint="curve")

    def ellipse(box, fill):
        draw.ellipse((*xy(box[:2]), *xy(box[2:])), fill=fill)

    # A soft current under each sea creature ties the silhouettes to the dial.
    ellipse((27, 83, 73, 87), "#303c3d")
    for shape in character.shapes:
        if shape.kind == "ellipse":
            ellipse(shape.coordinates, color[shape.fill])
        else:
            draw.polygon([xy(p) for p in _curve(shape.coordinates)], fill=color[shape.fill])
    blink = frame % FRAMES == 7 and state in {"idle", "working"}
    for x, y in character.eyes:
        if blink or state == "offline":
            line([(x-2.4, y), (x+2.4, y)])
        elif state == "error":
            line([(x-2, y-2), (x+2, y+2)])
            line([(x+2, y-2), (x-2, y+2)])
        else:
            ellipse((x-2.3, y-3, x+2.3, y+3), ink)
            ellipse((x-.7, y-2.2, x+.3, y-.9), "#ffffff")
        ellipse((x-4, y+5, x+3, y+7.5), "#ed9f99")
    x, y = character.mouth
    if state in {"waiting", "unknown"}:
        ellipse((x-2.3, y-1, x+2.3, y+3), ink)
    elif state == "error":
        line([(x-4, y+2), (x, y), (x+4, y+2)])
    else:
        line([(x-4, y), (x-2, y+2), (x+2, y+2), (x+4, y)])
    accent = STATES[state][1]
    if state == "working":
        # Steam is an activity metaphor, never a completion percentage.
        for i in range(3):
            x = 40 + 10*i
            rise = (frame + i*2) % FRAMES
            line([(x, 23-rise*.5), (x-2, 19-rise*.5), (x+1, 15-rise*.5)], accent, 1.2)
    elif state in {"waiting", "error", "unknown"}:
        # Fixed attention marker: do not flash urgent states.
        ellipse((72, 12, 85, 25), accent)
        line([(78.5, 15), (78.5, 19)], ink, 1.5)
        ellipse((77.7, 21, 79.3, 22.6), ink)
    elif state == "done":
        line([(75, 18), (78, 21), (84, 14)], accent, 2)
    return image
