import base64
from concurrent.futures import Future
import json
import threading
import time
import unittest
from unittest.mock import patch
import zlib
from urllib.error import HTTPError

from device import Display, ProtocolError
from listen import ListenStore, ListenPump, LocalTranscriber, MAX_CAPTURE, MAX_PROMPT, prompt_text
from t3_session import DispatchRejected
from test_device import Transport


class Session:
    def __init__(self):
        self.sent = []
        self.fail = None

    def describe(self, thread_id):
        if thread_id not in {"one", "two", "three"}:
            raise ValueError("Unknown session")
        return {"thread_id": thread_id, "title": f"Session {thread_id}",
                "project_id": "q" if thread_id == "three" else "p"}

    def draft(self, thread_id=None, project_id=None):
        if thread_id: self.describe(thread_id)
        return {"thread_id": "draft", "title": "New voice session", "projectTitle": "Project",
                "project_id": ("q" if thread_id == "three" else "p") if thread_id else project_id}

    def send(self, *args, **kwargs):
        self.sent.append(args)
        if self.fail:
            raise self.fail
        return 42


class ListenTests(unittest.TestCase):
    def setUp(self):
        self.session = Session()
        self.store = ListenStore(self.session)

    def test_injected_clocks_expire_recordings_and_device_heartbeats_independently(self):
        now, heartbeat = [100], [50]
        store = ListenStore(self.session, "one", True, clock=lambda: now[0], monotonic=lambda: heartbeat[0])
        self.assertFalse(store.get()["device"]["connected"])
        store.hardware({"mic": {"ready": True}})
        self.assertTrue(store.get()["device"]["connected"])
        job = store.begin({"captureId": 1})["job"]
        store.progress({"id": job["id"], "state": "transcribing"})
        store.progress({"id": job["id"], "state": "review", "text": "Keep this private"})
        heartbeat[0] += 4
        self.assertFalse(store.get()["device"]["connected"])
        self.assertEqual(store.get()["job"]["state"], "review")
        now[0] += 601
        expired = store.get()["job"]
        self.assertEqual(expired["state"], "error")
        self.assertEqual(expired["text"], "")
        self.assertEqual(self.session.sent, [])

    def test_new_session_requires_the_displayed_project_when_supplied(self):
        self.store.configure({"threadId": "one", "enabled": True})
        with self.assertRaisesRegex(ValueError, "project changed"):
            self.store.new_session({"threadId": "one", "projectId": "q"})
        self.assertEqual(self.store.get()["target"]["id"], "one")
        created = self.store.new_session({"threadId": "one", "projectId": "p"})
        self.assertEqual(created["target"]["projectId"], "p")
        self.assertEqual(self.session.sent, [])

    def review(self):
        self.store.configure({"enabled": True, "threadId": "one"})
        job = self.store.begin({"captureId": 1})["job"]
        self.store.progress({"id": job["id"], "state": "transcribing"})
        self.store.progress({"id": job["id"], "state": "review", "text": "Check the tests."})
        return {"id": job["id"], "threadId": "one", "text": "Check the tests.", "confirm": True}

    def test_off_until_destination_selected_and_enabled(self):
        self.assertFalse(self.store.get()["enabled"])
        self.assertIsNone(self.store.get()["target"])
        with self.assertRaises(ValueError):
            self.store.configure({"enabled": True})
        with self.assertRaises(ValueError):
            self.store.begin({"captureId": 1})

    def test_release_mode_sends_transcription_once_to_capture_destination(self):
        self.store.configure({"enabled": True, "threadId": "one", "autoSend": True})
        job = self.store.begin({"captureId": 4})["job"]
        self.store.progress({"id": job["id"], "state": "transcribing"})
        self.assertEqual(self.session.sent, [])
        value = {"id": job["id"], "state": "review", "text": "Please run the tests."}
        result = self.store.progress(value)
        self.assertEqual(result["job"]["state"], "sent")
        self.store.progress(value)
        self.assertEqual(len(self.session.sent), 1)
        self.assertEqual(self.session.sent[0][:2], ("one", "Please run the tests."))

    def test_new_session_is_a_draft_until_the_first_prompt(self):
        self.store.configure({"enabled": True, "threadId": "one", "autoSend": True})
        result = self.store.new_session({"threadId": "one"})
        self.assertTrue(result["target"]["new"])
        self.assertEqual(self.session.sent, [])
        # Pausing a draft preserves its bootstrap, and never creates a thread.
        result = self.store.configure({"threadId": "draft", "enabled": False})
        self.assertTrue(result["target"]["new"])
        self.store.configure({"threadId": "draft", "enabled": True})
        job = self.store.begin({"captureId": 1})["job"]
        self.assertNotIn("_draft", job)
        self.store.progress({"id": job["id"], "state": "transcribing"})
        result = self.store.progress({"id": job["id"], "state": "review", "text": "New task"})
        self.assertEqual(result["job"]["state"], "sent")
        self.assertNotIn("new", result["target"])
        self.assertEqual(self.session.sent[0][0], "draft")

    def test_cancelled_auto_recording_and_silence_never_send(self):
        self.store.configure({"enabled": True, "threadId": "one", "autoSend": True})
        job = self.store.begin({"captureId": 1})["job"]
        self.store.progress({"id": job["id"], "state": "empty"})
        job = self.store.begin({"captureId": 2})["job"]
        self.store.progress({"id": job["id"], "state": "transcribing"})
        self.store.discard()
        self.store.progress({"id": job["id"], "state": "review", "text": "Late words"})
        self.assertEqual(self.session.sent, [])

    def test_explicit_confirmation_and_at_most_one_send(self):
        value = self.review()
        self.assertEqual(self.session.sent, [])
        for invalid in [{**value, "confirm": False}, {**value, "id": "stale"}, {**value, "threadId": "two"}]:
            with self.assertRaises(ValueError):
                self.store.confirm(invalid)
        self.assertEqual(self.store.confirm(value)["job"]["state"], "sent")
        with self.assertRaises(ValueError):
            self.store.confirm(value)
        self.assertEqual(len(self.session.sent), 1)

    def test_destination_change_keeps_review_attached_to_original_session(self):
        value = self.review()
        result = self.store.configure({"threadId": "two", "enabled": True})
        self.assertEqual(result["job"]["text"], value["text"])
        self.assertEqual(result["job"]["threadId"], "one")
        with self.assertRaises(ValueError): self.store.confirm({**value, "threadId": "two"})
        self.store.confirm({**value, "text": "Edited prompt"})
        self.assertEqual(self.session.sent[0][:2], ("one", "Edited prompt"))
        self.assertEqual(self.store.begin({"captureId": 2})["job"]["threadId"], "two")

    def test_switch_after_release_preserves_pending_auto_send(self):
        self.store.configure({"threadId": "one", "enabled": True, "autoSend": True})
        job = self.store.begin({"captureId": 1})["job"]
        self.store.progress({"id": job["id"], "state": "transcribing"})
        self.store.configure({"threadId": "two", "enabled": True})
        self.store.progress({"id": job["id"], "state": "review", "text": "Original session"})
        self.assertEqual(self.session.sent[0][:2], ("one", "Original session"))
        self.assertEqual(self.store.get()["target"]["id"], "two")

    def test_switch_between_transcription_and_auto_confirmation_cannot_retarget(self):
        self.store.configure({"threadId": "one", "enabled": True, "autoSend": True})
        job = self.store.begin({"captureId": 1})["job"]
        self.store.progress({"id": job["id"], "state": "transcribing"})
        confirm = self.store.confirm
        def switch_then_confirm(value):
            self.store.configure({"threadId": "two", "enabled": True})
            return confirm(value)
        with patch.object(self.store, "confirm", side_effect=switch_then_confirm):
            result = self.store.progress({"id": job["id"], "state": "review", "text": "Finish this thought"})
        self.assertEqual(result["job"]["state"], "sent")
        self.assertEqual(self.session.sent[0][:2], ("one", "Finish this thought"))

    def test_long_hold_does_not_expire_while_listening(self):
        self.store.configure({"threadId": "one", "enabled": True, "autoSend": True})
        with patch("listen.time.time", return_value=100):
            job = self.store.begin({"captureId": 1})["job"]
        with patch("listen.time.time", return_value=10000):
            self.assertEqual(self.store.get()["job"]["state"], "listening")
            self.store.progress({"id": job["id"], "state": "transcribing"})
            result = self.store.progress({"id": job["id"], "state": "review", "text": "A long thought"})
        self.assertEqual(result["job"]["state"], "sent")

    def test_draft_completion_does_not_replace_next_selected_session(self):
        self.store.configure({"threadId": "one", "enabled": True, "autoSend": True})
        self.store.new_session({"threadId": "one"})
        job = self.store.begin({"captureId": 1})["job"]
        self.store.progress({"id": job["id"], "state": "transcribing"})
        self.store.configure({"threadId": "two", "enabled": True})
        self.store.progress({"id": job["id"], "state": "review", "text": "Create the original draft"})
        self.assertEqual(self.session.sent[0][0], "draft")
        self.assertEqual(self.store.get()["target"]["id"], "two")

    def test_cancellation_and_expiry_cannot_be_resurrected(self):
        value = self.review()
        self.store.discard()
        self.store.progress({"id": value["id"], "state": "review", "text": "late"})
        self.assertEqual(self.store.get()["job"]["text"], "")
        value = self.review()
        self.store.job["expiresAt"] = 0
        with self.assertRaises(ValueError):
            self.store.confirm(value)
        self.assertEqual(self.session.sent, [])

    def test_lost_ack_is_uncertain_and_not_retried(self):
        value = self.review()
        self.session.fail = TimeoutError()
        self.assertEqual(self.store.confirm(value)["job"]["state"], "uncertain")
        with self.assertRaises(ValueError):
            self.store.confirm(value)
        self.assertEqual(len(self.session.sent), 1)

    def test_rejection_and_simultaneous_confirmation(self):
        value = self.review()
        started, release = threading.Event(), threading.Event()
        def send(*args):
            started.set()
            release.wait(2)
            raise DispatchRejected()
        self.session.send = send
        worker = threading.Thread(target=self.store.confirm, args=(value,))
        worker.start()
        self.assertTrue(started.wait(1))
        try:
            with self.assertRaises(ValueError): self.store.confirm(value)
            with self.assertRaises(ValueError): self.store.discard()
            with self.assertRaises(ValueError): self.store.configure({"enabled": True, "threadId": "two"})
        finally:
            release.set(); worker.join()
        self.assertEqual(self.store.get()["job"]["state"], "error")

    def test_text_and_capture_bounds(self):
        for text in ["", " ", "a" * (MAX_PROMPT + 1), "a\x00", [], None]:
            with self.assertRaises(ValueError): prompt_text(text)
        self.assertEqual(prompt_text("a" * 3000), "a" * 3000)
        for pcm in [b"", bytes(7999), bytes(8001), "no"]:
            with self.assertRaises(ValueError): LocalTranscriber().transcribe(pcm)
        with self.assertRaises(ValueError):
            self.store.control({"action": "record"})


class CaptureTransport(Transport):
    def __init__(self):
        super().__init__()
        self.capture = b"\x01\x02" * 5000
        self.mic = {"available": True, "recording": False, "id": 7, "bytes": len(self.capture),
                    "sampleRate": 16000, "crc32": f"{zlib.crc32(self.capture):08x}"}

    def write(self, data):
        request = json.loads(data)
        if request["op"] == "mic.read":
            offset = request["offset"]
            self.override = {"captureId": 7, "captureOffset": offset,
                             "captureData": base64.b64encode(self.capture[offset:offset + 2048]).decode()}
        return super().write(data)


class StreamingTransport(Transport):
    def __init__(self):
        super().__init__()
        self.capture = bytes(range(256)) * 5000
        self.mic = {"streaming": True, "available": False, "recording": True, "id": 7,
                    "bytes": len(self.capture), "sampleRate": 16000}
        self.corrupt = False

    def write(self, data):
        request = json.loads(data)
        if request["op"] == "mic.read":
            offset, count = request["offset"], request["count"]
            chunk = self.capture[offset:offset + count]
            self.override = {"captureId": 7, "captureOffset": offset,
                             "captureData": base64.b64encode(chunk).decode(),
                             "captureCrc32": "00000000" if self.corrupt else f"{zlib.crc32(chunk):08x}"}
        return super().write(data)


class CaptureTests(unittest.TestCase):
    def test_exact_audio_reassembly_and_corruption(self):
        transport = CaptureTransport()
        display = Display(transport)
        display.request("hello")
        self.assertEqual(display.capture_audio(transport.mic), transport.capture)
        with self.assertRaisesRegex(ProtocolError, "checksum"):
            display.capture_audio({**transport.mic, "crc32": "00000000"})
        for field in [{"bytes": MAX_CAPTURE + 2}, {"sampleRate": 22050}, {"recording": True}, {"id": True}]:
            with self.assertRaises(ProtocolError): display.capture_audio({**transport.mic, **field})

    def test_streaming_reads_absolute_offsets_past_the_old_limit_and_checks_every_chunk(self):
        transport = StreamingTransport()
        display = Display(transport)
        display.request("hello")
        offset = MAX_CAPTURE + 100
        mic = {**transport.mic, "bytes": offset + 100}
        self.assertEqual(display.capture_chunk(mic, offset), transport.capture[offset:offset + 100])
        self.assertEqual(transport.writes[-1]["count"], 100)
        transport.corrupt = True
        with self.assertRaisesRegex(ProtocolError, "checksum"):
            display.capture_chunk(mic, offset)
        for fields in [{"bytes": -2}, {"bytes": 0x100000000}, {"bytes": True},
                       {"sampleRate": 22050}, {"streaming": False}, {"id": True}]:
            with self.assertRaises(ProtocolError): display.capture_chunk({**mic, **fields}, offset)
        for invalid_offset in [-2, True, offset + 1, mic["bytes"]]:
            with self.assertRaises(ProtocolError): display.capture_chunk(mic, invalid_offset)


class PumpDisplay:
    def __init__(self):
        self.mic = {"id": 0, "ready": True, "recording": False, "available": False,
                    "error": 0, "bytes": 0, "level": 0, "sampleRate": 16000}
        self.commands = []

    def request(self, op, **fields):
        self.commands.append((op, fields))
        if op == "mic.clear": self.mic.update(available=False, recording=False)
        if op == "mic.start": self.mic.update(recording=True, id=self.mic["id"] + 1)
        if op == "mic.stop": self.mic.update(recording=False, available=True, bytes=16000)
        return {"mic": dict(self.mic), "audio": {"playing": False}}

    def capture_audio(self, mic):
        return bytes(16000)


class StreamingDisplay(PumpDisplay):
    def __init__(self):
        super().__init__()
        self.mic["streaming"] = True
        self.capture = bytearray()

    def append(self, data):
        self.capture.extend(data)
        self.mic.update(bytes=len(self.capture), crc32=f"{zlib.crc32(self.capture):08x}")

    def capture_chunk(self, mic, offset):
        return bytes(self.capture[offset:min(offset + 2048, mic["bytes"])])

    def request(self, op, **fields):
        result = super().request(op, **fields)
        if op == "mic.stop":
            self.mic["bytes"] = len(self.capture)
            result["mic"] = dict(self.mic)
        return result


class IdlePumpDisplay(PumpDisplay):
    def __init__(self):
        super().__init__()
        self.ui = {"idleMode": True, "idle": True, "wakePending": False}
        self.start_error = None

    def request(self, op, **fields):
        if op == "mic.start" and self.start_error:
            self.commands.append((op, fields))
            error, self.start_error = self.start_error, None
            raise error
        result = super().request(op, **fields)
        result["ui"] = dict(self.ui)
        return result


class PumpTests(unittest.TestCase):
    def idle_setup(self):
        store = ListenStore(Session(), "one", True)
        routes = {"": store.get, "/begin": store.begin, "/progress": store.progress, "/device": store.hardware}
        def api(port, path, value=None, method=None, timeout=3):
            handler = routes[path.removeprefix("/api/listen")]
            return handler(value) if value is not None else handler()
        patcher = patch("listen.bridge_request", side_effect=api)
        patcher.start()
        self.addCleanup(patcher.stop)
        display = IdlePumpDisplay()
        pump = ListenPump(display, 1)
        self.addCleanup(pump.close)
        pump.tick()  # Report a ready microphone before the explicit browser action.
        return store, display, pump

    def test_browser_record_waits_for_wake_release_and_fresh_display_then_starts_once(self):
        store, display, pump = self.idle_setup()
        store.control({"action": "record"})
        serial = pump.serial
        pump.next_tick = 0; pump.tick()
        self.assertTrue(pump.pending_record)
        self.assertEqual(pump.serial, serial)
        self.assertIsNone(store.get()["job"])
        display.ui.update(idle=False, wakePending=True)
        for _ in range(3):  # Includes service callbacks while the UI uploads.
            pump.next_tick = 0; pump.tick()
        self.assertNotIn("mic.start", [op for op, _ in display.commands])
        self.assertTrue(pump.pending_record)
        display.ui["wakePending"] = False
        pump.next_tick = 0; pump.tick()
        self.assertFalse(pump.pending_record)
        self.assertEqual(store.get()["job"]["threadId"], "one")
        self.assertEqual(store.get()["job"]["state"], "listening")
        pump.next_tick = 0; pump.tick()
        self.assertEqual([op for op, _ in display.commands].count("mic.start"), 1)
        self.assertEqual(store.session.sent, [])

    def test_cancel_or_pause_during_wake_discards_pending_browser_record(self):
        for cancel in (True, False):
            with self.subTest(cancel=cancel):
                store, display, pump = self.idle_setup()
                store.control({"action": "record"})
                pump.next_tick = 0; pump.tick()
                self.assertTrue(pump.pending_record)
                if cancel:
                    store.discard()
                else:
                    store.configure({"threadId": "one", "enabled": False})
                display.ui.update(idle=False, wakePending=False)
                pump.next_tick = 0; pump.tick()
                self.assertFalse(pump.pending_record)
                self.assertNotIn("mic.start", [op for op, _ in display.commands])
                self.assertIsNone(store.get()["job"])
                self.assertEqual(store.session.sent, [])
                self.doCleanups()

    def test_explicit_start_racing_idle_defers_only_a_confirmed_busy_rejection(self):
        store, display, pump = self.idle_setup()
        store.control({"action": "record"})
        display.ui["idle"] = False
        serial = pump.serial
        display.start_error = ProtocolError("Device rejected mic.start: ui_idle_busy")
        pump.next_tick = 0; pump.tick()
        self.assertTrue(pump.pending_record)
        self.assertEqual(pump.serial, serial)
        self.assertIsNone(store.get()["job"])
        pump.next_tick = 0; pump.tick()
        self.assertFalse(pump.pending_record)
        self.assertEqual(store.get()["job"]["state"], "listening")
        self.assertEqual(store.session.sent, [])

    def test_uncertain_browser_capture_start_is_not_automatically_retried(self):
        store, display, pump = self.idle_setup()
        store.control({"action": "record"})
        display.ui["idle"] = False
        display.start_error = ProtocolError("No reply to mic.start. Check the port and installed firmware.")
        pump.next_tick = 0
        with self.assertRaisesRegex(ProtocolError, "No reply"):
            pump.tick()
        pump.next_tick = 0; pump.tick()
        self.assertFalse(pump.pending_record)
        self.assertEqual([op for op, _ in display.commands].count("mic.start"), 1)
        self.assertIsNone(store.get()["job"])
        self.assertEqual(store.session.sent, [])

    def test_long_stream_stays_listening_and_only_submits_complete_audio_after_release(self):
        store = ListenStore(Session(), "one", True)
        store.configure({"threadId": "one", "enabled": True, "autoSend": True})
        routes = {"": store.get, "/begin": store.begin, "/progress": store.progress, "/device": store.hardware}
        def api(port, path, value=None, method=None, timeout=3):
            handler = routes[path.removeprefix("/api/listen")]
            return handler(value) if value is not None else handler()
        display = StreamingDisplay()
        with patch("listen.bridge_request", side_effect=api):
            pump = ListenPump(display, 1)
            future = Future()
            try:
                with patch.object(pump.pool, "submit", return_value=future) as submit:
                    def tick(): pump.next_tick = 0; pump.tick()
                    display.request("mic.start")
                    # Simulate three minutes of recording, including quiet pauses.
                    for second in range(180):
                        display.append(bytes([second % 256, 0]) * 16000)
                        tick()
                        self.assertEqual(store.get()["job"]["state"], "listening")
                        self.assertFalse(submit.called)
                        self.assertEqual(len(pump.pcm), len(display.capture))
                    display.request("mic.stop"); tick()
                    self.assertEqual(submit.call_count, 1)
                    self.assertEqual(submit.call_args.args[1], bytes(display.capture))
                    self.assertEqual(store.get()["job"]["state"], "transcribing")
                    store.configure({"threadId": "two", "enabled": True})
                    future.set_result("A complete, long thought."); tick(); tick()
                    self.assertEqual(store.session.sent[0][:2], ("one", "A complete, long thought."))
                    self.assertEqual(store.get()["target"]["id"], "two")
                    self.assertEqual(len(store.session.sent), 1)
                    self.assertFalse(pump.pcm)
            finally: pump.close()

    def test_stream_capture_error_and_cancellation_never_transcribe_partial_audio(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                store = ListenStore(Session(), "one", True)
                routes = {"": store.get, "/begin": store.begin, "/progress": store.progress, "/device": store.hardware}
                def api(port, path, value=None, method=None, timeout=3):
                    handler = routes[path.removeprefix("/api/listen")]
                    return handler(value) if value is not None else handler()
                display = StreamingDisplay()
                with patch("listen.bridge_request", side_effect=api):
                    pump = ListenPump(display, 1)
                    try:
                        with patch.object(pump.pool, "submit") as submit:
                            display.request("mic.start"); display.append(bytes(16000)); pump.tick()
                            if cancelled:
                                store.discard()
                            else:
                                display.mic.update(recording=False, available=False, error=1)
                            pump.next_tick = 0; pump.tick()
                            self.assertEqual(store.get()["job"]["state"], "discarded" if cancelled else "error")
                            self.assertFalse(pump.pcm)
                            submit.assert_not_called()
                    finally: pump.close()

    def test_restart_invalidates_inflight_capture_and_ignores_old_record_action(self):
        store = ListenStore(Session(), "one", True)
        store.hardware({"mic": {"ready": True}})
        store.control({"action": "record"})
        store.begin({"captureId": 1})
        routes = {"": store.get, "/progress": store.progress, "/device": store.hardware}
        def api(port, path, value=None, method=None, timeout=3):
            handler = routes[path.removeprefix("/api/listen")]
            return handler(value) if value is not None else handler()
        d = PumpDisplay()
        with patch("listen.bridge_request", side_effect=api):
            pump = ListenPump(d, 1)
            try:
                pump.tick()
                self.assertEqual(store.get()["job"]["state"], "error")
                self.assertNotIn("mic.start", [op for op, _ in d.commands])
            finally:
                pump.close()

    def test_disabling_during_begin_discards_without_stopping_the_sender(self):
        store = ListenStore(Session(), "one", True)
        routes = {"": store.get, "/device": store.hardware}
        def api(port, path, value=None, method=None, timeout=3):
            suffix = path.removeprefix("/api/listen")
            if suffix == "/begin":
                raise HTTPError("http://localhost", 400, "disabled", {}, None)
            handler = routes[suffix]
            return handler(value) if value is not None else handler()
        d = PumpDisplay()
        with patch("listen.bridge_request", side_effect=api):
            pump = ListenPump(d, 1)
            try:
                d.request("mic.start")
                pump.tick()
                self.assertFalse(d.mic["recording"])
                self.assertIsNone(pump.job_id)
            finally:
                pump.close()

    def test_hold_capture_transcribes_once_and_cancel_discards_late_result(self):
        store = ListenStore(Session(), "one", True)
        routes = {"": store.get, "/begin": store.begin, "/progress": store.progress, "/device": store.hardware}
        def api(port, path, value=None, method=None, timeout=3):
            handler = routes[path.removeprefix("/api/listen")]
            return handler(value) if value is not None else handler()
        d = PumpDisplay()
        with patch("listen.bridge_request", side_effect=api):
            pump = ListenPump(d, 1)
            future = Future()
            try:
                with patch.object(pump.pool, "submit", return_value=future) as submit:
                    def tick(): pump.next_tick = 0; pump.tick()
                    tick()
                    d.request("mic.start"); tick()
                    self.assertEqual(store.get()["job"]["state"], "listening")
                    d.request("mic.stop"); tick(); tick()
                    self.assertEqual(store.get()["job"]["state"], "transcribing")
                    self.assertEqual(submit.call_count, 1)
                    store.discard(); tick()
                    future.set_result("Late transcript"); tick()
                    self.assertEqual(store.get()["job"]["state"], "discarded")
                    self.assertEqual(store.session.sent, [])
            finally: pump.close()
