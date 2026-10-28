"""Render circular UI samples and the actual firmware animation with demo levels.

Run: python tests/device_ui_preview.py
Requires Pillow and a C compiler; never opens USB or records audio.
"""
import ctypes
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from device_ui import MUTED, font, lines, pixels565, render


def unpack565(data):
    from PIL import Image, ImageChops
    high = Image.frombytes("L", (466, 466), data[::2])
    low = Image.frombytes("L", (466, 466), data[1::2])
    red = high.point(lambda v: (v & 248) | (v >> 5))
    green = ImageChops.add(high.point(lambda v: (v & 7) << 3), low.point(lambda v: v >> 5))
    green = green.point(lambda v: (v << 2) | (v >> 4))
    blue = low.point(lambda v: ((v & 31) << 3) | ((v & 31) >> 2))
    return Image.merge("RGB", (red, green, blue))


def main():
    from PIL import Image, ImageDraw
    destination = ROOT / "artifacts/circular-ui"
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        temporary = Path(temporary)
        shim = temporary / "preview.c"
        shim.write_text('''#include "voice_ring.h"
void preview_ring(uint8_t *pixels, float seconds, unsigned level) {
    voice_ring_t ring;
    voice_ring_prepare(&ring, seconds, level, true);
    voice_ring_draw(&ring, pixels, 466, 0, 466);
}
''')
        library = temporary / "ring.so"
        subprocess.run(["cc", "-std=c11", "-shared", "-fPIC", "-I", str(ROOT / "firmware/main"),
                        str(shim), str(ROOT / "firmware/main/voice_ring.c"), "-lm", "-o", str(library)], check=True)
        native = ctypes.CDLL(str(library))
        native.preview_ring.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_float, ctypes.c_uint]
        native.preview_ring.restype = None

        def frame(screen, seconds=1, level=6500):
            data = pixels565(render(screen))
            if screen["kind"] == "listening":
                buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
                native.preview_ring(buffer, seconds, level)
                data = bytes(buffer)
            return unpack565(data)

        home = dict(kind="home", project="shark-harness", title="Make something great",
                    index=2, total=6, enabled=True, autoSend=True)
        listening = dict(kind="listening", title=home["title"], autoSend=True)
        samples = [
            ("Projects", dict(kind="projects", project="shark-harness", path="~/.t3/projects/shark-harness", sessions=6, index=2, total=11)),
            ("Session", home), ("Listening", listening),
            ("Paused", {**home, "enabled": False}),
            ("New session", {**home, "new": True, "index": 7, "total": 7}),
            ("Sent", dict(kind="sent", title=home["title"])),
        ]
        def gallery(name, samples):
            artwork = Image.new("RGB", (1398, ((len(samples) + 2) // 3) * 502), "#090a0a")
            draw = ImageDraw.Draw(artwork)
            for i, (label, screen) in enumerate(samples):
                panel = frame(screen)
                panel.save(destination / (label.lower().replace(" ", "-") + ".png"))
                x, y = i % 3 * 466, i // 3 * 502
                artwork.paste(panel, (x, y + 36))
                draw.text((x + 233, y + 10), f"{i + 1:02d} / {label.upper()}",
                          font=font(12, mono=True), fill=MUTED, anchor="mt")
            artwork.save(destination / name)

        gallery("gallery.png", samples)
        review_lines = lines("Give the controls more room to breathe. Keep the voice destination "
                             "visible, and make the next step clear before anything is sent. "
                             "Review the full prompt, then confirm the session on the following screen.", 330, 20)
        gallery("review-flow.png", [
            ("Review mode", {**home, "autoSend": False}),
            ("Transcribing", dict(kind="transcribing", title=home["title"], autoSend=False)),
            ("Review", dict(kind="review", title=home["title"], page=0,
                            pages=(len(review_lines) + 6) // 7, lines=review_lines[:7])),
            ("Confirm", dict(kind="confirm", title=home["title"])),
            ("Sending", dict(kind="sending", title=home["title"])),
            ("Accepted", dict(kind="sent", title=home["title"])),
        ])
        gallery("recovery.png", [
            ("Choose session", dict(kind="t3-choices", title=home["title"],
                                    project="Studio · shark-harness", index=1, total=2)),
            ("Reconnect", dict(kind="voice-offline", error="The selected browser disconnected. Open T3, then retry.")),
            ("No speech", dict(kind="empty", title=home["title"])),
            ("Interrupted", dict(kind="error", title=home["title"], error="USB sender restarted. Hold to record again.")),
            ("Unconfirmed", dict(kind="uncertain", title=home["title"])),
            ("First connection", dict(kind="empty-home")),
        ])
        # Vary demo levels to show silence and speech, using the firmware code.
        levels = [0, 100, 400, 900, 2500, 5000, 8500, 10000, 7000, 2500, 1200, 400, 100, 0, 0, 0]
        frames = [frame(listening, i * .08, levels[(i // 2) % len(levels)]) for i in range(32)]
        frames[0].save(destination / "listening.gif", save_all=True, append_images=frames[1:],
                       duration=80, loop=0, disposal=2)
    print(destination / "gallery.png")
    print(destination / "review-flow.png")
    print(destination / "recovery.png")
    print(destination / "listening.gif")


if __name__ == "__main__": main()
