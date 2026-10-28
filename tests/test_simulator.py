"""End-to-end synthetic panel inputs, production controller, and HTTP boundary."""
from contextlib import ExitStack, redirect_stdout
import http.client
import io
import json
import threading
import unittest
from unittest.mock import patch

from simulator.core import SCENARIOS, Simulation
from simulator.__main__ import SimulatorServer


class SimulatorTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        for target in ("device.connect", "speech.bridge_request", "sqlite3.connect"):
            self.stack.enter_context(patch(target, side_effect=AssertionError("Simulator touched live I/O")))
        self.sim = self.stack.enter_context(Simulation())

    def tearDown(self): self.stack.close()

    def tap(self, x, y): return self.sim.action({"type": "tap", "x": x, "y": y})

    def test_raw_physical_left_and_right_reach_correct_visible_project(self):
        self.sim.gesture((433, 211), raw=True)
        self.assertEqual(self.sim.state()["screen"]["index"], 2)
        self.assertIn("Next project", self.sim.state()["lastAction"])
        self.sim.gesture((35, 218), raw=True)
        self.assertEqual(self.sim.state()["screen"]["index"], 1)
        self.assertIn("Previous project", self.sim.state()["lastAction"])
        self.assertEqual(self.sim.recordings, 0)
        self.assertEqual(self.sim.session.attempts, [])

    def test_arrows_and_swipes_share_documented_order_and_wrap(self):
        for scenario in ("projects", "sessions"):
            with self.subTest(scenario=scenario):
                self.sim.reset(scenario)
                self.assertEqual(self.tap(423, 233)["screen"]["index"], 2)
                self.assertEqual(self.tap(43, 233)["screen"]["index"], 1)
                self.assertEqual(self.tap(43, 233)["screen"]["index"], 3)
                self.assertIn("wrapped", self.sim.state()["lastAction"])
                self.assertEqual(self.tap(423, 233)["screen"]["index"], 1)
                self.assertEqual(self.sim.action({"type": "swipe", "direction": "left"})["screen"]["index"], 2)
                self.assertEqual(self.sim.action({"type": "swipe", "direction": "right"})["screen"]["index"], 1)

    def test_every_scenario_uses_real_renderer_without_live_io(self):
        for name, _ in SCENARIOS:
            self.sim.reset(name)
            self.assertTrue(self.sim.frame_png().startswith(b"\x89PNG\r\n\x1a\n"), name)
            self.assertTrue(self.sim.state()["synthetic"])
            self.assertIn("shark-simulator-", str(self.sim.ui.selection))

    def test_character_animation_preserves_loaded_controls_and_idle_clock(self):
        self.sim.reset("sessions")
        self.sim.ui.character_motion = True
        before = self.sim.state()
        pixels = self.sim.display.pixels
        last_activity = self.sim.ui.last_activity
        self.sim.advance(.7)
        after = self.sim.state()
        self.assertNotEqual(self.sim.display.pixels, pixels)
        self.assertEqual(after["frameVersion"], before["frameVersion"])
        self.assertGreater(after["imageVersion"], before["imageVersion"])
        self.assertEqual(self.sim.ui.last_activity, last_activity)
        self.assertEqual(after["input"]["ui"]["frame"], before["input"]["ui"]["frame"])
        self.sim.pointer_event("down")
        held_version = self.sim.display.version
        self.sim.advance(.6)
        self.assertEqual(self.sim.display.version, held_version)
        self.sim.pointer_event("cancel")

    def test_idle_at_sixty_seconds_and_whole_waking_hold_is_consumed(self):
        self.sim.reset("sessions")
        before = self.sim.state()["destination"]
        self.sim.advance(59.8)
        self.assertFalse(self.sim.state()["idle"])
        self.sim.advance(.3)
        self.assertTrue(self.sim.state()["idle"])
        self.sim.gesture((233, 233), duration=2)
        after = self.sim.state()
        self.assertFalse(after["idle"])
        self.assertFalse(after["wakePending"])
        self.assertEqual(after["destination"], before)
        self.assertEqual(after["recordings"], 0)
        self.assertEqual(after["submissions"], 0)
        self.sim.gesture((233, 233), duration=1)
        self.sim.advance(1)
        self.assertEqual(self.sim.state()["submissions"], 1)

    def test_waking_on_resume_does_not_enable_paused_voice(self):
        self.sim.reset("paused")
        self.sim.advance(60.1)
        self.sim.gesture((335, 390), duration=1.2)
        self.assertTrue(self.sim.state()["paused"])
        self.assertEqual(self.sim.recordings, 0)
        self.assertFalse(self.tap(335, 390)["paused"])

    def test_navigation_control_holds_never_capture(self):
        self.sim.reset("sessions")
        self.sim.gesture((423, 233), duration=1.2)
        self.assertEqual(self.sim.state()["screen"]["index"], 2)
        self.assertEqual(self.sim.recordings, 0)
        self.sim.gesture((233, 85), duration=1.2)
        self.assertEqual(self.sim.state()["screen"]["kind"], "projects")
        self.assertEqual(self.sim.recordings, 0)

    def test_capture_destination_stays_pinned_when_next_target_changes(self):
        self.sim.reset("sessions")
        first = self.sim.store.target["id"]
        self.sim.pointer_event("down")
        self.sim.advance(1)
        self.assertTrue(self.sim.state()["recording"])
        self.sim.store.configure({"threadId": "atlas-3", "enabled": True, "autoSend": True})
        self.sim.pointer_event("up")
        self.sim.advance(1)
        self.assertEqual(len(self.sim.session.attempts), 1)
        self.assertEqual(self.sim.session.attempts[0]["threadId"], first)
        self.assertEqual(self.sim.store.target["id"], "atlas-3")

    def test_release_outside_glass_and_cancellation_do_not_stick(self):
        for phase in ("up", "cancel"):
            self.sim.reset("sessions")
            self.sim.pointer_event("down")
            self.sim.advance(1)
            self.assertTrue(self.sim.state()["recording"])
            self.sim.pointer_event(phase, 600, -50)
            self.sim.advance(1)
            self.assertFalse(self.sim.state()["recording"])
            self.assertIsNone(self.sim.pointer)
            self.assertFalse(self.sim.state()["input"]["touch"]["pressed"])
            self.assertEqual(self.sim.state()["submissions"], 0 if phase == "cancel" else 1)

    def test_invalid_move_cancels_new_hold_until_real_release(self):
        self.sim.reset("sessions")
        self.sim.pointer_event("down")
        self.sim.advance(.3)
        self.sim.pointer_event("move", 600, 233)
        self.sim.pointer_event("move", 233, 233)
        self.sim.advance(1)
        self.sim.pointer_event("up")
        self.assertEqual(self.sim.recordings, 0)
        self.assertEqual(self.sim.session.attempts, [])

    def test_review_paging_and_confirmation_send_exactly_once(self):
        self.sim.reset("review")
        target = self.sim.store.job["threadId"]
        self.sim.advance(61)
        self.assertFalse(self.sim.state()["idle"])
        while self.sim.state()["screen"]["kind"] == "review": self.tap(335, 390)
        self.assertEqual(self.sim.state()["screen"]["kind"], "confirm")
        self.assertEqual(self.sim.session.attempts, [])
        self.tap(335, 390); self.tap(335, 390)
        self.assertEqual(len(self.sim.session.attempts), 1)
        self.assertEqual(self.sim.session.attempts[0]["threadId"], target)

    def test_uncertain_send_never_retries(self):
        self.sim.reset("uncertain")
        self.assertEqual(self.sim.state()["job"]["state"], "uncertain")
        self.sim.advance(60)
        self.assertFalse(self.sim.state()["idle"])
        self.tap(233, 233)
        self.sim.advance(65)
        self.assertEqual(len(self.sim.session.attempts), 1)

    def test_two_completed_new_sessions_have_distinct_destinations(self):
        self.sim.reset("sessions")
        for _ in range(2):
            self.tap(128, 390)
            self.assertTrue(self.sim.state()["screen"]["new"])
            self.sim.gesture((233, 233), duration=1)
            self.sim.advance(2.5)
        ids = [item["threadId"] for item in self.sim.session.attempts]
        self.assertEqual(len(ids), 2)
        self.assertNotEqual(*ids)

    def test_simulated_clock_expires_review_using_production_store(self):
        self.sim.reset("review")
        self.sim.advance(601)
        self.assertEqual(self.sim.state()["job"]["state"], "error")
        self.assertEqual(self.sim.state()["job"]["text"], "")
        self.assertEqual(self.sim.session.attempts, [])


class SimulatorHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.quiet = redirect_stdout(io.StringIO()); cls.quiet.__enter__()
        cls.server = SimulatorServer(0, realtime=False)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
        cls.quiet.__exit__(None, None, None)

    def setUp(self):
        with self.server.lock: self.server.simulation.reset("projects")

    def request(self, method="GET", path="/api/state", body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, response.read(), dict(response.headers)
        connection.close()
        return result

    def post(self, action, **headers):
        return self.request("POST", "/api/action", json.dumps(action),
            {"Content-Type": "application/json", "X-Simulator-Token": self.server.token, **headers})

    def test_local_page_state_and_images_with_security_headers(self):
        status, body, headers = self.request(path="/")
        self.assertEqual(status, 200)
        self.assertIn("microphone=()", headers["Permissions-Policy"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        state = json.loads(self.request()[1])
        self.assertTrue(state["synthetic"])
        self.assertEqual(self.request(path=state["frameUrl"])[0], 200)
        self.assertEqual(self.request(path="/favicon.ico")[0], 204)

    def test_write_requires_token_and_json(self):
        self.assertEqual(self.request("POST", "/api/action", "{}", {"Content-Type": "application/json"})[0], 403)
        self.assertEqual(self.post({"type": "reset"}, **{"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.post({"type": "advance", "seconds": float("nan")})[0], 400)
        self.assertEqual(self.post({"type": "advance", "seconds": -1})[0], 400)

    def test_foreign_or_duplicate_origins_and_hosts_are_rejected(self):
        host = f"127.0.0.1:{self.server.server_port}"
        self.assertEqual(self.request(headers={"Host": "foreign.example"})[0], 403)
        self.assertEqual(self.request(headers={"Origin": "https://foreign.example"})[0], 403)
        for duplicate in ("Host", "Origin"):
            c = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
            c.putrequest("GET", "/api/state", skip_host=True)
            c.putheader("Host", host)
            if duplicate == "Origin": c.putheader("Origin", f"http://{host}")
            c.putheader(duplicate, "http://foreign.example")
            c.endheaders(); response = c.getresponse()
            self.assertEqual(response.status, 403)
            response.read(); c.close()

    def test_static_paths_cannot_read_repository_or_private_state(self):
        for path in ("/../listen.py", "/%2e%2e/listen.py", "/.git/config", "/api/listen", "/api/voice-client"):
            self.assertEqual(self.request(path=path)[0], 404)

    def test_stale_frame_cannot_trigger_action_or_disguise_image(self):
        old = json.loads(self.request()[1])
        result = self.post({"type": "tap", "x": 423, "y": 233, "frameVersion": old["frameVersion"]})
        self.assertEqual(result[0], 200)
        self.assertEqual(json.loads(result[1])["screen"]["index"], 2)
        self.assertEqual(self.post({"type": "tap", "x": 423, "y": 233, "frameVersion": old["frameVersion"]})[0], 409)
        self.assertEqual(self.request(path=old["frameUrl"])[0], 409)
        self.assertEqual(json.loads(self.request()[1])["screen"]["index"], 2)
        self.post({"type": "reset"})
        self.assertGreater(json.loads(self.request()[1])["frameVersion"], old["frameVersion"])

    def test_animation_keeps_loaded_frame_valid_until_its_controls_change(self):
        self.post({"type": "scenario", "scenario": "sessions"})
        with self.server.lock:
            self.server.simulation.ui.character_motion = True
        old = json.loads(self.request()[1])
        self.post({"type": "advance", "seconds": .7})
        current = json.loads(self.request()[1])
        self.assertEqual(current["frameVersion"], old["frameVersion"])
        self.assertGreater(current["imageVersion"], old["imageVersion"])
        self.assertEqual(self.request(path=old["frameUrl"])[0], 200)
        value = {"type": "tap", "x": 423, "y": 233, "frameVersion": old["frameVersion"]}
        response = self.post(value)
        self.assertEqual(response[0], 200)
        self.assertEqual(json.loads(response[1])["screen"]["index"], 2)
        self.assertEqual(self.post(value)[0], 409)
        self.assertEqual(self.request(path=old["frameUrl"])[0], 409)

    def test_release_remains_allowed_after_frame_changed(self):
        self.post({"type": "scenario", "scenario": "sessions"})
        state = json.loads(self.request()[1])
        self.assertEqual(self.post({"type": "pointer", "phase": "down", "x": 233, "y": 233,
                                  "frameVersion": state["frameVersion"]})[0], 200)
        self.post({"type": "advance", "seconds": 1})
        self.assertTrue(json.loads(self.request()[1])["recording"])
        self.assertEqual(self.post({"type": "pointer", "phase": "cancel", "x": 600, "y": -50,
                                  "frameVersion": state["frameVersion"]})[0], 200)
        self.assertFalse(json.loads(self.request()[1])["recording"])
        self.assertEqual(json.loads(self.request()[1])["submissions"], 0)

    def test_confirmation_replay_rejected_at_http_and_store_boundaries(self):
        self.post({"type": "scenario", "scenario": "confirm"})
        state = json.loads(self.request()[1])
        value = {"type": "tap", "x": 335, "y": 390, "frameVersion": state["frameVersion"]}
        self.assertEqual(self.post(value)[0], 200)
        self.assertEqual(self.post(value)[0], 409)
        self.assertEqual(json.loads(self.request()[1])["submissions"], 1)


if __name__ == "__main__": unittest.main()
