"""Demo mode must stay useful without opening a real workspace or voice route."""

import base64
from contextlib import redirect_stderr, redirect_stdout
from functools import partial
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import bridge
from bridge import Handler, SceneStore


class DemoHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "private.sqlite"
        self.database.write_bytes(b"PRIVATE WORKSPACE MUST NOT BE OPENED")
        self.read_status = patch("bridge.read_status", side_effect=AssertionError("Read a real workspace"))
        self.status_reader = self.read_status.start()
        self.logging = patch.object(Handler, "log_message")
        self.logging.start()
        self.scenes = SceneStore()
        self.speech = Mock(name="real_speech_store")
        self.listen = Mock(name="real_listen_store")
        self.listen.session.current_threads.side_effect = AssertionError("Read real active windows")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(
            Handler, database=self.database, scenes=self.scenes,
            speech=self.speech, listen=self.listen, demo=True))
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.worker.join()
        self.server.server_close()
        self.logging.stop()
        self.read_status.stop()
        self.temporary.cleanup()

    def request(self, path, method="GET", value=None, headers=None):
        body = json.dumps(value).encode() if value is not None else None
        return Request(self.url + path, data=body, method=method,
                       headers={"Content-Type": "application/json", **(headers or {})})

    def json(self, path, method="GET", value=None, headers=None):
        with urlopen(self.request(path, method, value, headers), timeout=3) as response:
            return json.load(response)

    def denied(self, code, path, method="GET", value=None, headers=None):
        with self.assertRaises(HTTPError) as raised:
            urlopen(self.request(path, method, value, headers), timeout=3)
        with raised.exception as response:
            self.assertEqual(response.code, code)
            return json.load(response)

    def test_status_and_voice_reads_use_only_synthetic_data(self):
        status = self.json("/api/status")
        self.assertTrue(status["demo"])
        self.assertEqual(status["source"], "demo")
        self.assertFalse(status["connected"])
        self.assertFalse(status["deviceConnected"])
        self.assertTrue(status["threads"])
        self.assertTrue(all(thread["id"].startswith("demo-") for thread in status["threads"]))
        self.assertTrue(all(not project["path"] for project in status["projects"]))
        listening = self.json("/api/listen")
        self.assertTrue(listening["demo"])
        self.assertFalse(listening["enabled"])
        self.assertFalse(listening["destinationReady"])
        self.assertIsNone(listening["target"])
        self.assertIsNone(listening["job"])
        speech = self.json("/api/speech")
        self.assertTrue(speech["demo"])
        self.assertIsNone(speech["job"])
        self.assertEqual(self.listen.mock_calls, [])
        self.assertEqual(self.speech.mock_calls, [])
        self.status_reader.assert_not_called()
        self.assertEqual(self.database.read_bytes(), b"PRIVATE WORKSPACE MUST NOT BE OPENED")

    def test_active_window_lookup_does_not_reach_an_injected_live_store(self):
        self.denied(409, "/api/current-session")
        self.listen.session.current_threads.assert_not_called()

    def test_every_voice_write_is_rejected_before_reaching_a_store(self):
        routes = ["/api/speech", "/api/speech/volume", "/api/speech/claim", "/api/speech/progress",
                  "/api/listen/configure", "/api/listen/control", "/api/listen/device",
                  "/api/listen/begin", "/api/listen/progress", "/api/listen/new",
                  "/api/listen/confirm", "/api/voice-client"]
        for path in routes:
            with self.subTest(path=path):
                result = self.denied(409, path, "POST", {"threadId": "private", "confirm": True})
                self.assertIn("demo", result["error"].lower())
        for path in ["/api/listen", "/api/speech"]:
            with self.subTest(path=path, method="DELETE"):
                self.denied(409, path, "DELETE")
        self.assertEqual(self.listen.mock_calls, [])
        self.assertEqual(self.speech.mock_calls, [])
        self.status_reader.assert_not_called()

    def test_message_chart_and_image_scenes_remain_available_and_can_expire(self):
        from PIL import Image
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "#ff5c35").save(buffer, format="PNG")
        image = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
        for kind, extra in [("message", {}), ("chart", {"values": [3, 2, 1]}), ("image", {"image": image})]:
            with self.subTest(kind=kind):
                result = self.json("/api/scene", "POST", {"title": "Demo visual", **extra})
                self.assertEqual(result["surface"], "simulator")
                self.assertEqual(result["overlay"]["kind"], kind)
                self.assertEqual(self.json("/api/status")["overlay"], result["overlay"])
                self.assertTrue(self.json("/api/scene", "DELETE")["ok"])
                self.assertIsNone(self.json("/api/status")["overlay"])
        with patch("bridge.time.time", return_value=100):
            self.json("/api/scene", "POST", {"title": "Temporary", "seconds": 1})
        with patch("bridge.time.time", return_value=102):
            self.assertIsNone(self.json("/api/status")["overlay"])
        self.status_reader.assert_not_called()

    def test_demo_keeps_local_host_and_origin_boundaries(self):
        for headers in [{"Host": "unrelated.example"}, {"Origin": "https://unrelated.example"}]:
            for path, method, value in [("/api/status", "GET", None),
                                        ("/api/scene", "POST", {"title": "Forbidden"}),
                                        ("/api/scene", "DELETE", None),
                                        ("/api/listen/configure", "POST", {})]:
                with self.subTest(headers=headers, path=path, method=method):
                    self.denied(403, path, method, value, headers)
        self.assertIsNone(self.scenes.get())
        self.assertTrue(self.json("/api/status", headers={"Origin": self.url})["demo"])
        self.assertEqual(self.listen.mock_calls, [])


class DemoCLITests(unittest.TestCase):
    def test_demo_server_never_initializes_t3_or_voice(self):
        server = Mock()
        server.serve_forever.side_effect = KeyboardInterrupt
        with patch("sys.argv", ["bridge.py", "--demo"]), redirect_stdout(io.StringIO()), \
                patch("bridge.ThreadingHTTPServer", return_value=server) as factory, \
                patch("bridge.T3Session") as session, patch("bridge.ListenStore") as listen, \
                patch("bridge.SpeechStore") as speech, patch("bridge.read_status") as read:
            bridge.main()
        session.assert_not_called()
        listen.assert_not_called()
        speech.assert_not_called()
        read.assert_not_called()
        self.assertEqual(factory.call_args.args[0][0], "127.0.0.1")
        server.server_close.assert_called_once()

    def test_demo_snapshot_does_not_read_a_database_or_credentials(self):
        output = io.StringIO()
        with patch("sys.argv", ["bridge.py", "--demo", "--snapshot", "--database", "/private/workspace"]), \
                redirect_stdout(output), patch("bridge.read_status") as read, patch("bridge.T3Session") as session:
            bridge.main()
        self.assertTrue(json.loads(output.getvalue())["demo"])
        read.assert_not_called()
        session.assert_not_called()

    def test_demo_rejects_real_voice_options_and_client_commands_without_side_effects(self):
        arguments = [["--thread", "real-thread"], ["--stt"], ["--thread", "real-thread", "--stt"],
                     ["say", "Hello"], ["stop"], ["volume", "20"], ["speech-status"],
                     ["scene", "Hello"], ["clear"]]
        for args in arguments:
            with self.subTest(args=args), patch("sys.argv", ["bridge.py", "--demo", *args]), \
                    redirect_stderr(io.StringIO()), patch("bridge.bridge_request") as request, \
                    patch("bridge.urlopen") as open_url, patch("bridge.T3Session") as session, \
                    patch("bridge.ThreadingHTTPServer") as server:
                with self.assertRaises(SystemExit) as raised:
                    bridge.main()
                self.assertEqual(raised.exception.code, 2)
                request.assert_not_called()
                open_url.assert_not_called()
                session.assert_not_called()
                server.assert_not_called()
