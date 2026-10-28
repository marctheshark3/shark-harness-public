import base64
from contextlib import redirect_stderr
import io
import json
import random
import unittest
from unittest.mock import patch
import zlib

import device

from device import BufferedSerial, Display, FRAME_BYTES, MAX_FRAME_CHUNK_BYTES, ProtocolError, image_pixels, rgb565


class DeviceCLITests(unittest.TestCase):
    def test_idle_timeout_options_reach_device_ui_without_a_real_usb_connection(self):
        for environment, option, expected in [(None, None, 60), ("12.5", None, 12.5),
                                               ("12.5", "0", 0), ("12.5", "30", 30)]:
            with self.subTest(environment=environment, option=option), patch.dict("os.environ", {}, clear=True):
                if environment is not None:
                    device.os.environ["SHARK_IDLE_SECONDS"] = environment
                args = ["device.py", "--port", "/not-a-device", "watch", "--device-ui"]
                if option is not None:
                    args += ["--idle-seconds", option]
                with patch("sys.argv", args), patch("device.connect") as connect, \
                        patch("device.watch_companion") as watch:
                    device.main()
                self.assertEqual(watch.call_args.args[-1], expected)
                self.assertIs(watch.call_args.args[0], connect.return_value.__enter__.return_value)

    def test_invalid_idle_timeout_never_opens_usb(self):
        for value in ["-1", "nan", "inf", "not-a-number"]:
            for from_environment in [False, True]:
                with self.subTest(value=value, from_environment=from_environment), \
                        patch.dict("os.environ", {}, clear=True), patch("device.connect") as connect:
                    args = ["device.py", "--port", "/not-a-device", "watch", "--device-ui"]
                    if from_environment:
                        device.os.environ["SHARK_IDLE_SECONDS"] = value
                    else:
                        args += ["--idle-seconds", value]
                    with patch("sys.argv", args), redirect_stderr(io.StringIO()):
                        with self.assertRaises(SystemExit) as raised:
                            device.main()
                    self.assertEqual(raised.exception.code, 2)
                    connect.assert_not_called()


class SerialBytes:
    def __init__(self, chunks): self.chunks, self.reads = list(chunks), 0
    @property
    def in_waiting(self): return len(self.chunks[0]) if self.chunks else 0
    def read(self, count):
        self.reads += 1
        if not self.chunks: return b""
        result = self.chunks[0][:count]
        self.chunks[0] = self.chunks[0][count:]
        if not self.chunks[0]: self.chunks.pop(0)
        return result


class BufferedSerialTests(unittest.TestCase):
    def test_batched_reads_preserve_multiple_lines_without_per_byte_reads(self):
        serial = SerialBytes([b"a" * 700 + b"\nsecond\n"])
        reader = BufferedSerial(serial)
        self.assertEqual(reader.read_until(), b"a" * 700 + b"\n")
        self.assertEqual(reader.read_until(), b"second\n")
        self.assertEqual(serial.reads, 1)

    def test_size_limit_and_partial_timeout(self):
        reader = BufferedSerial(SerialBytes([b"abcdef\n", b"part", b"", b"ial\n"]))
        self.assertEqual(reader.read_until(size=4), b"abcd")
        self.assertEqual(reader.read_until(), b"ef\n")
        self.assertEqual(reader.read_until(), b"part")
        self.assertEqual(reader.read_until(), b"ial\n")


class Transport:
    """In-memory peer used to exercise host framing, identity, and checksums."""
    def __init__(self):
        self.writes = []
        self.pixels = bytearray()
        self.frame_offset = 0
        self.frame_rle = False
        self.frame_zlib = False
        self.compressed = bytearray()
        self.pcm = bytearray()
        self.audio = {"ready": True, "armed": False, "playing": False, "stopped": False, "volume": 60, "error": 0}
        self.pending = []
        self.override = {}

    def write(self, data):
        if data == b"\n": return 1  # Empty lines resynchronize interrupted uploads.
        request = json.loads(data)
        self.writes.append(request)
        op = request["op"]
        if op == "frame.begin":
            self.pixels.clear()
            self.frame_offset = 0
            self.frame_rle = request.get("encoding") == "rle565"
            self.frame_zlib = request.get("encoding") == "zlib565"
            self.compressed.clear()
        elif op == "frame.chunk":
            assert request["offset"] == self.frame_offset
            chunk = base64.b64decode(request["data"], validate=True)
            self.frame_offset += len(chunk)
            if self.frame_zlib:
                self.compressed.extend(chunk)
            elif self.frame_rle:
                assert len(chunk) % 4 == 0
                for i in range(0, len(chunk), 4):
                    self.pixels.extend(chunk[i+2:i+4] * int.from_bytes(chunk[i:i+2], "big"))
            else:
                self.pixels.extend(chunk)
        elif op == "frame.end" and self.frame_zlib:
            self.pixels[:] = zlib.decompress(self.compressed)
        elif op == "audio.begin":
            self.pcm.clear()
            self.audio.update(armed=False, playing=False, stopped=False)
        elif op == "audio.chunk":
            assert request["offset"] == len(self.pcm)
            self.pcm.extend(base64.b64decode(request["data"], validate=True))
        elif op == "audio.end":
            self.audio.update(armed=request.get("onTap", False), playing=not request.get("onTap", False))
        elif op == "audio.stop":
            self.audio.update(armed=False, playing=False, stopped=True)
        elif op == "audio.volume":
            self.audio["volume"] = request["volume"]
        self.pending.append(json.dumps({
            "id": request["id"], "product": "shark-display", "protocol": 1,
            "ok": True, "ready": True, "width": 466, "height": 466,
            "offset": self.frame_offset, "crc32": f"{zlib.crc32(self.pixels):08x}",
            "audio": {**self.audio, "offset": len(self.pcm), "played": len(self.pcm) if not self.audio["playing"] else 0,
                      "crc32": f"{zlib.crc32(self.pcm):08x}"},
            **self.override,
        }).encode() + b"\n")
        return len(data)

    def read_until(self, *args):
        return self.pending.pop(0)


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.transport = Transport()
        self.display = Display(self.transport)

    def test_requires_own_identity_before_commands(self):
        with self.assertRaises(ProtocolError):
            self.display.request("clear")
        self.assertEqual(self.transport.writes, [])
        self.transport.override = {"product": "other-device"}
        with self.assertRaises(ProtocolError):
            self.display.request("hello")
        self.assertFalse(self.display.identified)

    def test_serial_timeouts_preserve_partial_replies(self):
        original = self.transport.read_until
        chunks = []
        def fragmented(*args):
            if not chunks:
                line = original(*args)
                chunks.extend([line[:49], b"", line[49:135], line[135:]])
            return chunks.pop(0)
        self.transport.read_until = fragmented
        self.assertTrue(self.display.request("hello")["ready"])
        self.assertTrue(self.display.request("status")["ok"])

    def test_upload_reconstructs_exact_big_endian_frame(self):
        self.display.request("hello")
        pixels = (b"\xf8\x00\x07\xe0\x00\x1f" * (FRAME_BYTES // 6 + 1))[:FRAME_BYTES]
        result = self.display.send_frame(pixels, 30)
        self.assertEqual(self.transport.pixels, pixels)
        self.assertEqual(result["crc32"], f"{zlib.crc32(pixels):08x}")
        self.assertEqual(self.transport.writes[-1]["seconds"], 30)

    def test_fails_on_wrong_offset_or_rejected_command(self):
        self.display.request("hello")
        self.transport.override = {"offset": 1}
        with self.assertRaisesRegex(ProtocolError, "offset"):
            self.display.send_frame(bytes(FRAME_BYTES))
        self.transport.override = {"ok": False, "error": "display_unavailable"}
        with self.assertRaisesRegex(ProtocolError, "display_unavailable"):
            self.display.request("clear")

    def test_compressed_frames_preserve_pixels_and_fall_back_for_photos(self):
        self.transport.override = {"frameRle": True}
        self.display.request("hello")
        # Includes runs larger than the 16-bit count and many chunk boundaries.
        pixels = b"\x08\x14" * 150000 + b"\x07\xe0\x01\x20" * 33578
        result = self.display.send_frame(pixels, ui_token=100)
        self.assertEqual(self.transport.pixels, pixels)
        self.assertLess(result["transferBytes"], FRAME_BYTES)
        begin = self.transport.writes[1]
        self.assertEqual((begin["encoding"], begin["uiToken"]), ("rle565", 100))
        self.transport.writes.clear()
        noisy = (b"\x08\x14\x01\x20" * (FRAME_BYTES // 4))
        self.display.send_frame(noisy)
        self.assertEqual(self.transport.pixels, noisy)
        self.assertNotIn("encoding", self.transport.writes[0])

    def test_zlib_frames_are_lossless_and_respect_advertised_limit(self):
        self.transport.override = {"frameZlibMaxBytes": 65536}
        self.display.request("hello")
        pixels = (b"\xf8\x00" * 16 + b"\x07\xe0" * 16) * (FRAME_BYTES // 64 + 1)
        pixels = pixels[:FRAME_BYTES]
        result = self.display.send_frame(pixels)
        self.assertEqual(self.transport.pixels, pixels)
        self.assertLess(result["transferBytes"], 65536)
        self.assertEqual(self.transport.writes[1]["encoding"], "zlib565")
        self.display.frame_zlib_max = 5
        self.transport.writes.clear()
        self.display.send_frame(pixels)
        self.assertEqual(self.transport.pixels, pixels)
        self.assertNotIn("encoding", self.transport.writes[0])

    def test_native_pixel_conversion_matches_rgb565_bit_layout(self):
        from PIL import Image
        raw = bytes(v for g in range(256) for r in range(256) for v in (r, g, (r + g) % 256))
        expected = bytearray()
        for i in range(0, len(raw), 3):
            r, g, b = raw[i:i+3]
            expected.extend(((r >> 3) << 11 | (g >> 2) << 5 | b >> 3).to_bytes(2, "big"))
        self.assertEqual(rgb565(Image.frombytes("RGB", (256, 256), raw)), expected)

    def test_large_frame_batches_require_negotiation_and_preserve_pixels(self):
        self.transport.override = {"frameZlibMaxBytes": 65536, "frameChunkBytes": 16384}
        self.display.request("hello")
        block = random.Random(17).randbytes(16384)
        pixels = (block * (FRAME_BYTES // len(block) + 1))[:FRAME_BYTES]
        result = self.display.send_frame(pixels)
        self.assertEqual(self.transport.pixels, pixels)
        chunks = [r for r in self.transport.writes if r["op"] == "frame.chunk"]
        self.assertGreater(len(base64.b64decode(chunks[0]["data"])), 2048)
        self.assertEqual(self.display.frame_chunk_bytes, MAX_FRAME_CHUNK_BYTES)
        self.assertEqual(len(chunks), (result["transferBytes"] + MAX_FRAME_CHUNK_BYTES - 1) // MAX_FRAME_CHUNK_BYTES)
        self.assertTrue(all(len(json.dumps(r).encode()) < 8192 for r in chunks))
        self.display.frame_chunk_bytes = 2048
        with self.assertRaisesRegex(ValueError, "line limit"):
            self.display.request("frame.chunk", offset=0, data=chunks[0]["data"])

    def test_interrupted_frame_restarts_with_smaller_chunks_and_same_controls(self):
        for operation, override in [("frame.chunk", {"offset": -1}),
                                    ("frame.chunk", {"ok": False, "error": "invalid_chunk"}),
                                    ("frame.end", {"crc32": "bad"})]:
            with self.subTest(operation=operation, override=override):
                transport = Transport()
                transport.override = {"frameZlibMaxBytes": 65536, "frameChunkBytes": 16384}
                display = Display(transport)
                display.request("hello")
                write, interrupted = transport.write, False
                def interrupt_once(data):
                    nonlocal interrupted
                    count = write(data)
                    if data == b"\n": return count
                    if json.loads(data)["op"] == operation and not interrupted:
                        interrupted = True
                        reply = json.loads(transport.pending[-1])
                        transport.pending[-1] = json.dumps({**reply, **override}).encode() + b"\n"
                    return count
                transport.write = interrupt_once
                block = random.Random(9).randbytes(32768)
                pixels = (block * (FRAME_BYTES // len(block) + 1))[:FRAME_BYTES]
                result = display.send_frame(pixels, ui_token=77, hold_to_talk=False,
                                            service=lambda: display.request("status"))
                self.assertEqual(transport.pixels, pixels)
                self.assertEqual(result["crc32"], f"{zlib.crc32(pixels):08x}")
                begins = [r for r in transport.writes if r["op"] == "frame.begin"]
                self.assertEqual(len(begins), 2)
                for begin in begins:
                    self.assertEqual(begin["uiToken"], 77)
                    self.assertIs(begin["holdToTalk"], False)
                self.assertEqual(display.frame_chunk_bytes, 2048)
                self.assertEqual({r["op"] for r in transport.writes},
                                 {"hello", "status", "frame.begin", "frame.chunk", "frame.end"})

    def test_short_usb_write_is_delimited_before_restarting_the_frame(self):
        class ShortWriteTransport(Transport):
            def __init__(self):
                super().__init__()
                self.partial = bytearray()
                self.cut = True
                self.parse_errors = 0

            def write(self, data):
                accepted = len(data)
                if self.cut and data != b"\n" and json.loads(data)["op"] == "frame.chunk":
                    self.cut = False
                    accepted = 27  # Firmware has only the beginning of this JSON command.
                self.partial.extend(data[:accepted])
                while b"\n" in self.partial:
                    line, rest = self.partial.split(b"\n", 1)
                    self.partial[:] = rest
                    if not line: continue
                    try:
                        super().write(line + b"\n")
                    except ValueError:
                        self.parse_errors += 1
                        self.pending.append(json.dumps({"product": "shark-display", "protocol": 1,
                            "id": 0, "ok": False, "error": "invalid_json"}).encode() + b"\n")
                return accepted

        transport = ShortWriteTransport()
        transport.override = {"frameZlibMaxBytes": 65536, "frameChunkBytes": 16384}
        display = Display(transport)
        display.request("hello")
        pixels = b"\x07\xe0" * (FRAME_BYTES // 2)
        display.send_frame(pixels, ui_token=77, hold_to_talk=False)
        self.assertEqual(transport.pixels, pixels)
        self.assertEqual(transport.parse_errors, 1)
        self.assertEqual(sum(r["op"] == "frame.begin" for r in transport.writes), 2)

    def test_frame_recovery_is_bounded_and_does_not_retry_service_errors(self):
        self.display.request("hello")
        self.transport.override = {"offset": 1}
        with self.assertRaises(ProtocolError):
            self.display.send_frame(bytes(FRAME_BYTES))
        self.assertEqual(sum(r["op"] == "frame.begin" for r in self.transport.writes), 2)
        self.transport.override = {}
        self.transport.writes.clear()
        def failed_voice_poll(): raise ProtocolError("No reply to mic.configure")
        with self.assertRaisesRegex(ProtocolError, "mic.configure"):
            self.display.send_frame(bytes(FRAME_BYTES), service=failed_voice_poll)
        self.assertEqual([r["op"] for r in self.transport.writes], ["frame.begin"])

    def test_input_limits_before_upload(self):
        self.display.request("hello")
        for pixels, seconds in [(b"short", 20), (bytes(FRAME_BYTES), 0),
                                (bytes(FRAME_BYTES), 601), (bytes(FRAME_BYTES), True)]:
            with self.assertRaises(ValueError):
                self.display.send_frame(pixels, seconds)
        self.assertEqual(len(self.transport.writes), 1)

    def test_expired_upload_never_becomes_visible(self):
        self.display.request("hello")
        result = self.display.send_frame(bytes(FRAME_BYTES), expires_at=1)
        self.assertEqual(result["skipped"], "expired_during_upload")
        self.assertEqual(self.transport.writes[-1]["op"], "clear")
        self.assertNotIn("frame.end", [request["op"] for request in self.transport.writes])

    def test_pixels_and_transparency(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Install requirements-device.txt for image checks")
        for color, expected in [("red", b"\xf8\x00"), ("lime", b"\x07\xe0"),
                                ("blue", b"\x00\x1f"), ((255, 255, 255, 0), b"\x00\x00")]:
            source = io.BytesIO()
            Image.new("RGBA", (32, 16), color).save(source, "PNG")
            source.seek(0)
            self.assertEqual(image_pixels(source), expected * (466 * 466))

    def test_audio_reconstructs_pcm_and_checks_acknowledgments(self):
        self.display.request("hello")
        pcm = b"\x00\x80\xff\x7f\x01\x00" * 1200
        list(self.display.audio_chunks(pcm))
        self.assertEqual(self.transport.pcm, pcm)
        self.assertEqual(self.transport.writes[-1]["op"], "audio.end")
        self.transport.override = {"audio": {"offset": 99}}
        with self.assertRaisesRegex(ProtocolError, "offset"):
            list(self.display.audio_chunks(pcm))

    def test_audio_validation_and_interrupted_generator_never_play(self):
        from speech import MAX_BYTES
        self.display.request("hello")
        for pcm in (b"", b"\x00", bytes(MAX_BYTES + 2)):
            with self.assertRaises(ValueError):
                list(self.display.audio_chunks(pcm))
        upload = self.display.audio_chunks(bytes(8192))
        next(upload)
        next(upload)
        self.display.request("audio.stop")
        upload.close()
        self.assertNotIn("audio.end", [r["op"] for r in self.transport.writes])


if __name__ == "__main__":
    unittest.main()
