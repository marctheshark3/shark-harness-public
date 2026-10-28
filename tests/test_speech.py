from concurrent.futures import Future
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from bridge import Handler, SceneStore
from device import Display
from speech import SpeechStore, SpeechPump, bridge_request, validate_text, validate_volume
from test_device import Transport


class SpeechTests(unittest.TestCase):
    def test_limits(self):
        for value in (None, {}, "", " ", "x" * 401, "bad\x00text"):
            with self.assertRaises(ValueError):
                validate_text(value)
        for value in (True, 1.5, -1, 81, None):
            with self.assertRaises(ValueError):
                validate_volume(value)
        self.assertEqual(validate_text(" hello "), "hello")
        self.assertEqual(validate_volume(0), 0)
        with self.assertRaises(ValueError):
            SpeechStore().put({"text": "hello", "onTap": "true"})

    def test_preloaded_request_has_an_hour_and_tap_starts_playback_deadline(self):
        store = SpeechStore()
        with patch("speech.time.time", return_value=1000):
            job = store.put({"text": "hello", "onTap": True})["job"]
            self.assertEqual(job["expiresAt"], 1090)
            store.claim(job)
            store.update({"id": job["id"], "state": "uploading"})
            store.update({"id": job["id"], "state": "armed"})
            self.assertEqual(store.get()["job"]["expiresAt"], 4600)
        with patch("speech.time.time", return_value=2000):
            store.update({"id": job["id"], "state": "playing"})
            self.assertEqual(store.get()["job"]["expiresAt"], 2030)

    def test_claim_once_expire_and_do_not_overwrite_replacement(self):
        store = SpeechStore()
        first = store.put({"text": "first"})["job"]["id"]
        self.assertTrue(store.claim({"id": first})["claimed"])
        self.assertFalse(store.claim({"id": first})["claimed"])
        second = store.put({"text": "second"})["job"]["id"]
        self.assertFalse(store.update({"id": first, "state": "error"})["updated"])
        self.assertEqual(store.get()["job"]["id"], second)
        with patch("speech.time.time", return_value=store.get()["job"]["expiresAt"] + 1):
            self.assertFalse(store.claim({"id": second})["claimed"])
            self.assertEqual(store.get()["job"]["state"], "error")
            self.assertEqual(store.get()["job"]["text"], "")

    def test_late_progress_cannot_undo_stop_and_volume_persists(self):
        store = SpeechStore()
        job = store.put({"text": "hello", "volume": 45})["job"]
        store.claim(job)
        store.stop()
        self.assertFalse(store.update({"id": job["id"], "state": "playing"})["updated"])
        self.assertEqual(store.get()["job"]["text"], "")
        store.put({"text": "again"})
        self.assertEqual(store.get()["volume"], 45)

    def test_http_speech_lifecycle_and_origin_guard(self):
        store = SpeechStore()
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(
            Handler, database=Path("/no/t3.sqlite"), scenes=SceneStore(), speech=store))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_port
            result = bridge_request(port, value={"text": "A test", "volume": 0})
            self.assertEqual(result["volume"], 0)
            job = result["job"]
            claim = bridge_request(port, "/api/speech/claim", {"id": job["id"]})
            self.assertTrue(claim["claimed"])
            for state in ("uploading", "playing", "done"):
                self.assertTrue(bridge_request(port, "/api/speech/progress",
                                {"id": job["id"], "state": state})["updated"])
            self.assertEqual(bridge_request(port)["job"]["text"], "")
            bridge_request(port, value={"text": "Stop me"})
            self.assertEqual(bridge_request(port, method="DELETE")["job"]["state"], "stopped")
            for value in ([], {"text": ""}, {"text": "ok", "volume": True}):
                with self.assertRaises(HTTPError) as caught:
                    bridge_request(port, value=value)
                self.assertEqual(caught.exception.code, 400)
            from urllib.request import Request, urlopen
            request = Request(f"http://127.0.0.1:{port}/api/speech", data=b'{"text":"bad origin"}',
                              headers={"Origin": "https://example.com", "Content-Type": "application/json"})
            with self.assertRaises(HTTPError) as caught:
                urlopen(request)
            self.assertEqual(caught.exception.code, 403)
        finally:
            server.shutdown(); server.server_close(); thread.join()


class DeferredExecutor:
    def __init__(self):
        self.futures = []

    def submit(self, *args):
        future = Future()
        future.set_running_or_notify_cancel()
        self.futures.append(future)
        return future

    def shutdown(self, **kwargs):
        pass


class PumpTests(unittest.TestCase):
    def setUp(self):
        self.store = SpeechStore()
        self.transport = Transport()
        self.display = Display(self.transport)
        self.display.request("hello")
        def api(path="/api/speech", value=None):
            if path.endswith("/claim"):
                return self.store.claim(value)
            if path.endswith("/progress"):
                return self.store.update(value)
            return self.store.get()
        self.pump = SpeechPump(self.display, 0, api=api)
        self.pump.executor.shutdown()
        self.executor = self.pump.executor = DeferredExecutor()

    def tearDown(self):
        self.pump.close()

    def tick(self):
        self.pump.next_poll = 0
        self.pump.tick()

    def test_completion_and_no_replay(self):
        self.store.put({"text": "hello"})
        self.tick()
        self.executor.futures[0].set_result(bytes(4000))
        self.tick()
        self.assertEqual(self.store.get()["job"]["state"], "playing")
        self.transport.audio["playing"] = False
        self.tick(); self.tick()
        self.assertEqual(self.store.get()["job"]["state"], "done")
        self.assertEqual(sum(r["op"] == "audio.end" for r in self.transport.writes), 1)

    def test_armed_speech_waits_for_a_new_tap_and_plays_once(self):
        self.transport.override = {"touch": {"taps": 7}}
        self.store.put({"text": "When ready", "onTap": True})
        self.tick()
        self.executor.futures[0].set_result(bytes(4000))
        self.tick()
        self.assertEqual(self.store.get()["job"]["state"], "armed")
        self.assertTrue(self.transport.audio["armed"])
        self.assertFalse(self.transport.audio["playing"])
        self.transport.override = {"touch": {"taps": 8}}
        self.transport.audio.update(armed=False, playing=True)  # Firmware handles the first tap.
        self.tick()
        self.assertEqual(self.store.get()["job"]["state"], "playing")
        self.assertFalse(self.transport.audio["stopped"])
        self.transport.audio["playing"] = False
        self.tick()
        self.transport.override = {"touch": {"taps": 9}}
        self.tick()
        self.assertEqual(sum(r["op"] == "audio.end" for r in self.transport.writes), 1)

    def test_stop_during_synthesis_discards_late_result(self):
        self.store.put({"text": "cancelled"})
        self.tick()
        self.store.stop()
        self.tick()
        self.executor.futures[0].set_result(bytes(4000))
        self.tick()
        self.assertNotIn("audio.begin", [r["op"] for r in self.transport.writes])
        self.assertEqual(self.store.get()["job"]["state"], "stopped")

    def test_upload_cancel_and_latest_replacement_only(self):
        self.store.put({"text": "first"})
        self.tick()
        self.executor.futures[0].set_result(bytes(40000))
        self.tick()
        self.assertEqual(self.store.get()["job"]["state"], "uploading")
        self.store.put({"text": "second"})
        self.tick(); self.tick()
        self.executor.futures[1].set_result(b"\x01\x00" * 100)
        self.tick()
        self.assertEqual(self.transport.pcm, b"\x01\x00" * 100)
        self.assertEqual(sum(r["op"] == "audio.end" for r in self.transport.writes), 1)

    def test_volume_mute_and_physical_tap_stop(self):
        self.store.put({"text": "hello"})
        self.tick()
        self.executor.futures[0].set_result(bytes(4000))
        self.tick()
        self.store.set_volume({"volume": 0})
        self.tick()
        self.assertEqual(self.transport.audio["volume"], 0)
        self.transport.override = {"touch": {"taps": 1}}
        self.tick()
        self.assertTrue(self.transport.audio["stopped"])
        self.assertEqual(self.store.get()["job"]["state"], "stopped")

    def test_restarted_sender_fails_claimed_work_without_replaying(self):
        job = self.store.put({"text": "hello"})["job"]
        self.store.claim(job)
        self.tick()
        self.assertEqual(self.store.get()["job"]["state"], "error")
        self.assertEqual(len(self.executor.futures), 0)

    def test_voice_failure_does_not_start_audio(self):
        self.store.put({"text": "hello"})
        self.tick()
        self.executor.futures[0].set_exception(ValueError("Missing model"))
        self.tick()
        self.assertEqual(self.store.get()["job"]["state"], "error")
        self.assertNotIn("audio.begin", [r["op"] for r in self.transport.writes])

    def test_reboot_during_playback_is_not_reported_as_success(self):
        self.store.put({"text": "hello"})
        self.tick()
        self.executor.futures[0].set_result(bytes(4000))
        self.tick()
        self.transport.audio["playing"] = False
        self.transport.pcm.clear()  # Boot reset the device's playback counters.
        self.tick()
        self.assertEqual(self.store.get()["job"]["state"], "error")


if __name__ == "__main__":
    unittest.main()
