#!/usr/bin/env python3
"""USB client for our shark-display firmware (never flashes or updates firmware)."""

import argparse
import base64
from contextlib import contextmanager
import io
import json
import math
import os
from pathlib import Path
import time
import zlib
from speech import DEFAULT_VOICE, MAX_BYTES, SAMPLE_RATE, SpeechPump
from listen import DEFAULT_MODEL, MAX_CAPTURE, ListenPump

WIDTH = HEIGHT = 466
FRAME_BYTES = WIDTH * HEIGHT * 2
CHUNK_BYTES = 2048
# Base64 plus JSON must fit the board's 8 KiB USB receive buffer, even when
# firmware advertises support for decoding larger individual frame chunks.
MAX_FRAME_CHUNK_BYTES = 4096


def rle565(pixels):
    """Encode big-endian (run length, RGB565 pixel) pairs without changing pixels."""
    encoded = bytearray()
    previous, count = pixels[:2], 0
    for offset in range(0, len(pixels), 2):
        pixel = pixels[offset:offset + 2]
        if pixel != previous or count == 65535:
            encoded.extend(count.to_bytes(2, "big") + previous)
            previous, count = pixel, 0
        count += 1
    encoded.extend(count.to_bytes(2, "big") + previous)
    return encoded


def rgb565(image):
    """Pack exact RGB565 bytes using Pillow's native channel operations."""
    from PIL import Image, ImageChops
    r, g, b = image.convert("RGB").split()
    high = ImageChops.add(r.point(lambda v: v & 248), g.point(lambda v: v >> 5))
    low = ImageChops.add(g.point(lambda v: (v & 28) << 3), b.point(lambda v: v >> 3))
    return Image.merge("LA", (high, low)).tobytes()


class ProtocolError(RuntimeError):
    pass


class FrameUploadError(ProtocolError):
    """An interrupted image transfer can restart without restarting voice."""


class BufferedSerial:
    """Read available USB bytes in batches instead of one syscall per byte."""
    def __init__(self, serial):
        self.serial = serial
        self.pending = bytearray()

    def write(self, data):
        return self.serial.write(data)

    def read_until(self, expected=b"\n", size=8192):
        while True:
            end = self.pending.find(expected)
            if end >= 0 or len(self.pending) >= size:
                count = min(end + len(expected), size) if end >= 0 else size
                result = bytes(self.pending[:count])
                del self.pending[:count]
                return result
            chunk = self.serial.read(max(1, min(self.serial.in_waiting, size - len(self.pending))))
            if not chunk:
                result = bytes(self.pending)
                self.pending.clear()
                return result  # Display.request retains a partial line across timeouts.
            self.pending.extend(chunk)


class Display:
    def __init__(self, transport):
        self.transport = transport
        self.request_id = 0
        self.identified = False
        self.frame_rle = False
        self.frame_zlib_max = 0
        self.frame_chunk_bytes = CHUNK_BYTES

    def request(self, op, **fields):
        if not self.identified and op != "hello":
            raise ProtocolError("Identify the device before sending commands.")
        self.request_id = self.request_id % 1000000 + 1
        request = {**fields, "op": op, "id": self.request_id}
        data = json.dumps(request, separators=(",", ":")).encode() + b"\n"
        transfer_error = FrameUploadError if op.startswith("frame.") else ProtocolError
        limit = self.frame_chunk_bytes * 4 // 3 + 256 if op == "frame.chunk" else 4096
        if len(data) > limit:
            raise ValueError("USB command exceeds firmware's line limit.")
        if self.transport.write(data) != len(data):
            raise transfer_error("Incomplete USB write.")
        deadline = time.monotonic() + 5
        pending = bytearray()
        while time.monotonic() < deadline:
            part = self.transport.read_until(b"\n", 8192 - len(pending))
            if not part:
                continue
            pending.extend(part)
            if len(pending) >= 8192 and not pending.endswith(b"\n"):
                raise transfer_error("USB response exceeds the protocol limit.")
            if not pending.endswith(b"\n"):
                continue  # A serial timeout can split a valid JSON response.
            line = bytes(pending)
            pending.clear()
            try:
                result = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue  # Ignore bootloader diagnostics while the board starts.
            if not isinstance(result, dict):
                continue
            if result.get("product") != "shark-display" or result.get("protocol") != 1:
                raise ProtocolError("This is not our shark-display protocol 1 firmware.")
            if result.get("id") != self.request_id:
                continue
            if result.get("ok") is not True:
                if op.startswith("frame.") and result.get("error") in {
                        "invalid_offset", "invalid_chunk", "invalid_rle", "invalid_zlib",
                        "incomplete_frame", "crc_mismatch"}:
                    raise FrameUploadError(f"Device rejected {op}: {result['error']}")
                raise ProtocolError(f"Device rejected {op}: {result.get('error', 'unknown error')}")
            if result.get("width") != WIDTH or result.get("height") != HEIGHT:
                raise ProtocolError("Unexpected display dimensions.")
            if op == "hello":
                self.identified = True
                self.frame_rle = result.get("frameRle") is True
                limit = result.get("frameZlibMaxBytes")
                self.frame_zlib_max = limit if type(limit) is int and 1 <= limit <= FRAME_BYTES else 0
                chunk = result.get("frameChunkBytes")
                self.frame_chunk_bytes = min(chunk, MAX_FRAME_CHUNK_BYTES) if type(chunk) is int and chunk in (2048, 4096, 8192, 16384) else CHUNK_BYTES
            return result
        raise transfer_error(f"No reply to {op}. Check the port and installed firmware.")

    def send_frame(self, pixels, seconds=20, expires_at=None, service=None, ui_token=None, hold_to_talk=None):
        if len(pixels) != FRAME_BYTES:
            raise ValueError(f"Expected {FRAME_BYTES} RGB565 bytes.")
        if type(seconds) is not int or not 1 <= seconds <= 600:
            raise ValueError("Duration must be an integer between 1 and 600 seconds.")
        checksum = f"{zlib.crc32(pixels):08x}"
        start = time.monotonic()
        fields = {"uiToken": ui_token} if ui_token is not None else {}
        if hold_to_talk is not None:
            if type(hold_to_talk) is not bool: raise ValueError("hold_to_talk must be boolean")
            fields["holdToTalk"] = hold_to_talk
        payload = pixels
        compressed = zlib.compress(pixels, 6) if self.frame_zlib_max else None
        if compressed is not None and len(compressed) <= self.frame_zlib_max and len(compressed) < len(pixels):
            payload = compressed
            fields["encoding"] = "zlib565"
        elif self.frame_rle:
            compressed = rle565(pixels)
            if len(compressed) < len(pixels):
                payload = compressed
                fields["encoding"] = "rle565"
        for attempt in range(2):
            try:
                result = self._upload_frame(payload, checksum, fields, seconds, expires_at, service)
                return {**result, "uploadSeconds": round(time.monotonic() - start, 2), "transferBytes": len(payload)}
            except FrameUploadError as error:
                if attempt: raise
                # A short write may leave half a JSON command on the board.
                # Delimit it before starting the replacement frame; any id=0
                # parse-error reply is ignored by request's ID matching.
                if self.transport.write(b"\n") != 1:
                    raise ProtocolError("Could not resynchronize the image upload.") from error
                # Restart only this image, retaining its control token and voice
                # gate. Smaller JSON lines also fit the board's 8 KiB RX buffer.
                # Never retry a microphone, speech, or session command here.
                self.frame_chunk_bytes = min(self.frame_chunk_bytes, CHUNK_BYTES)
                print(f"Display: retrying interrupted frame; {error} "
                      f"chunkBytes={self.frame_chunk_bytes}", flush=True)

    def _upload_frame(self, payload, checksum, fields, seconds, expires_at, service):
        result = self.request("frame.begin", bytes=len(payload), crc32=checksum, **fields)
        if result.get("offset") != 0:
            raise FrameUploadError("Device did not reset the upload offset.")
        for offset in range(0, len(payload), self.frame_chunk_bytes):
            if service:
                service()
            chunk = payload[offset:offset + self.frame_chunk_bytes]
            result = self.request("frame.chunk", offset=offset,
                                  data=base64.b64encode(chunk).decode("ascii"))
            if result.get("offset") != offset + len(chunk):
                raise FrameUploadError(f"Wrong upload offset: expected {offset + len(chunk)}, "
                                       f"received {result.get('offset')}.")
        if expires_at is not None:
            remaining = math.ceil(expires_at / 1000 - time.time())
            if remaining <= 0:
                return {**self.request("clear"), "skipped": "expired_during_upload"}
            seconds = min(seconds, remaining)
        result = self.request("frame.end", seconds=seconds)
        if result.get("crc32") != checksum:
            raise FrameUploadError("Device acknowledged a different image checksum.")
        return result

    def audio_chunks(self, pcm, on_tap=False):
        """Yield between checked PCM chunks so watch can process cancellation."""
        if not isinstance(pcm, bytes) or not 2 <= len(pcm) <= MAX_BYTES or len(pcm) % 2:
            raise ValueError("Expected at most 20 seconds of mono PCM16 little-endian.")
        checksum = f"{zlib.crc32(pcm):08x}"
        result = self.request("audio.begin", bytes=len(pcm), sampleRate=SAMPLE_RATE, crc32=checksum)
        if result.get("audio", {}).get("offset") != 0:
            raise ProtocolError("Device did not reset the audio offset.")
        yield result
        for offset in range(0, len(pcm), CHUNK_BYTES):
            chunk = pcm[offset:offset + CHUNK_BYTES]
            result = self.request("audio.chunk", offset=offset, data=base64.b64encode(chunk).decode("ascii"))
            if result.get("audio", {}).get("offset") != offset + len(chunk):
                raise ProtocolError("Device acknowledged the wrong audio offset.")
            yield result
        result = self.request("audio.end", onTap=on_tap)
        if result.get("audio", {}).get("crc32") != checksum:
            raise ProtocolError("Device acknowledged a different audio checksum.")

    def capture_audio(self, mic):
        size, capture_id = mic.get("bytes"), mic.get("id")
        if (not mic.get("available") or mic.get("recording") or mic.get("sampleRate") != 16000
                or type(size) is not int or not 8000 <= size <= MAX_CAPTURE or size % 2
                or type(capture_id) is not int or not 1 <= capture_id <= 1000000):
            raise ProtocolError("Invalid microphone capture metadata.")
        pcm = bytearray()
        while len(pcm) < size:
            pcm.extend(self.capture_chunk(mic, len(pcm)))
        if f"{zlib.crc32(pcm):08x}" != mic.get("crc32"):
            raise ProtocolError("Microphone checksum mismatch.")
        return bytes(pcm)

    def capture_chunk(self, mic, offset):
        size, capture_id = mic.get("bytes"), mic.get("id")
        streaming = mic.get("streaming") is True
        if (mic.get("sampleRate") != 16000 or type(size) is not int or not 0 < size <= 0xffffffff
                or size % 2 or type(offset) is not int or not 0 <= offset < size or offset % 2
                or type(capture_id) is not int or not 1 <= capture_id <= 1000000
                or not (mic.get("available") or (streaming and mic.get("recording")))):
            raise ProtocolError("Invalid microphone capture metadata.")
        count = min(CHUNK_BYTES, size - offset)
        fields = {"count": count} if streaming else {}
        result = self.request("mic.read", captureId=capture_id, offset=offset, **fields)
        if result.get("captureId") != capture_id or result.get("captureOffset") != offset:
            raise ProtocolError("Microphone capture changed during transfer.")
        try:
            chunk = base64.b64decode(result.get("captureData", ""), validate=True)
        except (ValueError, TypeError) as error:
            raise ProtocolError("Invalid microphone audio encoding.") from error
        if len(chunk) != count:
            raise ProtocolError("Incomplete microphone audio transfer.")
        if streaming and f"{zlib.crc32(chunk):08x}" != result.get("captureCrc32"):
            raise ProtocolError("Microphone chunk checksum mismatch.")
        return chunk


def image_pixels(source):
    """Center-crop and flatten transparency onto black, then encode RGB565 BE."""
    from PIL import Image, ImageOps
    with Image.open(source) as image:
        image = ImageOps.fit(ImageOps.exif_transpose(image).convert("RGBA"),
                             (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS)
        background = Image.new("RGBA", image.size, "black")
        background.alpha_composite(image)
        return rgb565(background)


@contextmanager
def connect(port):
    import serial
    # Linux raises both lines when opening the port. Keep DTR high and lower RTS
    # so pyserial never passes through DTR-low/RTS-high (the chip-reset state).
    transport = serial.Serial(port=None, baudrate=460800, timeout=0.5,
                              write_timeout=5, exclusive=True)
    transport.dtr = True
    transport.rts = False
    transport.port = port
    try:
        transport.open()
        display = Display(BufferedSerial(transport))
        display.request("hello")
        yield display
    finally:
        transport.close()


def watch(display, bridge_port, browser_executable=None, voice=DEFAULT_VOICE, stt_model=None):
    """Send each new T3 visual once; leave ambient animation on the ESP32."""
    from playwright.sync_api import sync_playwright
    if not 1 <= bridge_port <= 65535:
        raise ValueError("Bridge port must be between 1 and 65535.")
    with sync_playwright() as playwright:
        options = {"executable_path": browser_executable} if browser_executable else {}
        browser = playwright.chromium.launch(**options)
        speech = SpeechPump(display, bridge_port, voice)
        listen = ListenPump(display, bridge_port, stt_model) if stt_model else None
        def service():
            if listen:
                listen.tick()
            speech.tick()
        try:
            page = browser.new_page(viewport={"width": 1100, "height": 900})
            page.goto(f"http://127.0.0.1:{bridge_port}", wait_until="networkidle")
            page.locator("#screen[aria-label]").wait_for()
            display.request("clear")
            previous, previous_state = None, None
            previous_touch = None
            print("USB display connected. Following T3 visuals; Ctrl-C stops the sender.", flush=True)
            while True:
                service()
                # The renderer returns metadata and pixels from the same completed
                # frame. Only capture pixels when a visual changes, never at video rate.
                frame = page.evaluate("() => window.captureT3Display(false)")
                if frame is None:
                    time.sleep(0.1)
                    continue
                if frame["state"] != previous_state:
                    display.request("state", state=frame["state"])
                    previous_state = frame["state"]
                key = json.dumps(frame["overlay"], sort_keys=True) if frame["overlay"] else None
                if key != previous and frame["imageReady"]:
                    if key is None:
                        display.request("clear")
                    else:
                        frame = page.evaluate("() => window.captureT3Display(true)")
                        # The page may have polled between calls; use the captured
                        # frame's own metadata, including expiration and image readiness.
                        if frame["overlay"] is None or not frame["imageReady"]:
                            continue
                        key = json.dumps(frame["overlay"], sort_keys=True)
                        png = base64.b64decode(frame["png"].split(",", 1)[1], validate=True)
                        result = display.send_frame(image_pixels(io.BytesIO(png)), 600,
                                                    frame["overlay"]["expiresAt"], service=service)
                        print(f"Visual acknowledged: {result.get('crc32', '')}", flush=True)
                    previous = key
                # Keep checking USB even when T3 is idle; fail visibly on unplug.
                health = display.request("status")
                if not health.get("ready"):
                    raise ProtocolError(f"Display stopped: bootError={health.get('bootError')}")
                touch = health.get("touch")
                if isinstance(touch, dict):
                    touch_key = (touch.get("ready"), touch.get("error"), touch.get("taps"),
                                 touch.get("x"), touch.get("y"), health.get("ambientScene"))
                    if touch_key != previous_touch:
                        print(f"Touch: ready={touch.get('ready')}, taps={touch.get('taps')}, "
                              f"last=({touch.get('x')},{touch.get('y')}), "
                              f"scene={health.get('ambientScene')}, error={touch.get('error')}", flush=True)
                        previous_touch = touch_key
                time.sleep(0.1)
        finally:
            try:
                try:
                    if listen:
                        listen.close()
                finally:
                    speech.close()
            finally:
                browser.close()


def watch_companion(display, bridge_port, voice, stt_model, confirm_prompts=False, idle_seconds=60):
    """Device-native interaction, with direct bridge calls and no browser process."""
    from device_ui import DeviceUI
    speech = SpeechPump(display, bridge_port, voice)
    listen = ListenPump(display, bridge_port, stt_model)
    last_heartbeat = 0
    def service():
        nonlocal last_heartbeat
        if time.monotonic() - last_heartbeat > .5:
            display.request("ui.configure", enabled=True)
            last_heartbeat = time.monotonic()
        listen.tick()
        speech.tick()
    ui = DeviceUI(display, bridge_port, service, auto_send=not confirm_prompts, idle_seconds=idle_seconds,
                  record_pending=lambda: listen.pending_record)
    try:
        health = display.request("ui.configure", enabled=True)
        if "ui" not in health:
            raise ProtocolError("Install firmware with on-device session controls first.")
        if health["ui"].get("voiceGate") is not True:
            raise ProtocolError("Install firmware with project-picker voice gating first.")
        if health["ui"].get("layoutVersion", 0) < 2 or health["ui"].get("voiceRing") is not True:
            raise ProtocolError("Install firmware with circular controls and the native voice ring first.")
        print("T3 device controls connected: swipe sessions, hold to talk, release to send.", flush=True)
        if idle_seconds:
            if health["ui"].get("idleMode") is True:
                print(f"Display rests after {idle_seconds:g}s; touch or BOOT wakes without acting.", flush=True)
            else:
                print("Idle animation unavailable on this firmware; session controls remain active.", flush=True)
        while True:
            service()
            ui.tick(display.request("status"))
            time.sleep(.05)
    finally:
        try:
            ui.close()
        finally:
            try:
                listen.close()
            finally:
                speech.close()


def idle_seconds(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("Idle seconds must be a number, or 0 to disable.") from None
    if not math.isfinite(seconds) or seconds < 0:
        raise argparse.ArgumentTypeError("Idle seconds must be finite and nonnegative; 0 disables idle.")
    return seconds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="Use the stable /dev/serial/by-id/... path.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("clear")
    state = sub.add_parser("state")
    state.add_argument("state", choices=["idle", "working", "waiting", "done", "error", "offline"])
    send = sub.add_parser("send", help="Send one image, then return to the native ambient animation.")
    send.add_argument("image", type=Path)
    send.add_argument("--seconds", type=int, default=20)
    follow = sub.add_parser("watch", help="Forward T3 scenes from the running local bridge.")
    follow.add_argument("--bridge-port", type=int, default=8874)
    follow.add_argument("--browser-executable", default=os.environ.get("BROWSER_EXECUTABLE"))
    follow.add_argument("--voice", type=Path, default=DEFAULT_VOICE, help="Local 22050 Hz Piper ONNX model.")
    follow.add_argument("--stt", action="store_true", help="Enable the push-to-talk microphone worker.")
    follow.add_argument("--stt-model", type=Path, default=DEFAULT_MODEL)
    follow.add_argument("--device-ui", action="store_true", help="Use the on-device session interface without a browser.")
    follow.add_argument("--confirm-prompts", action="store_true", help="Review on the device before sending; default is send on release.")
    follow.add_argument("--idle-seconds", type=idle_seconds, default=os.environ.get("SHARK_IDLE_SECONDS", "60"),
                        help="Dim ambient animation after this many idle seconds with --device-ui; 0 disables (default: 60).")
    args = parser.parse_args()
    try:
        # Decode before opening USB, so a bad image cannot start a partial upload.
        pixels = image_pixels(args.image) if args.command == "send" else None
        with connect(args.port) as display:
            if args.command == "watch":
                if args.device_ui:
                    watch_companion(display, args.bridge_port, args.voice, args.stt_model, args.confirm_prompts, args.idle_seconds)
                else:
                    watch(display, args.bridge_port, args.browser_executable, args.voice,
                          args.stt_model if args.stt else None)
                return
            elif pixels is not None:
                result = display.send_frame(pixels, args.seconds)
            elif args.command == "state":
                result = display.request("state", state=args.state)
            else:
                result = display.request(args.command)
            print(json.dumps(result, indent=2))
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        parser.exit(1, f"Device: {error}\n")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
