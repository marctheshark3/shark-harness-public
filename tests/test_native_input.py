import unittest

from simulator.native import NativeInput, control_region, coordinates, render_ring


class NativeInputTests(unittest.TestCase):
    def setUp(self):
        self.native = NativeInput()
        self.addCleanup(self.native.close)
        self.native.configure(True, 0)
        self.native.frame(42)

    def tap(self, now, x, y, raw=False):
        self.native.report(now, 1, x, y, raw=raw)
        return self.native.report(now + 80, 0)

    def test_raw_calibration_preserves_physical_arrow_sides(self):
        left = self.tap(100, 35, 465 - 247, raw=True)
        self.assertEqual(left["ui"]["events"][0], {
            "event": 1, "gesture": 1, "x": 35, "y": 247,
            "eventFrame": "0000002a", "startedMs": 100, "releasedMs": 180})
        right = self.tap(300, 433, 465 - 254, raw=True)
        self.assertEqual([(event["x"], event["y"]) for event in right["ui"]["events"]],
                         [(35, 247), (433, 254)])
        self.assertEqual(coordinates(0, 0), (0, 465))
        self.assertEqual(coordinates(465, 465), (465, 0))
        self.assertIsNone(coordinates(466, 0))
        self.assertIsNone(coordinates(0, 65535))

    def test_native_swipe_directions_are_finger_directions(self):
        for start, end, expected, now in [(340, 120, 2, 100), (120, 340, 3, 500)]:
            self.native.report(now, 1, start, 235)
            self.native.report(now + 100, 1, end, 235)
            result = self.native.report(now + 200, 0)
            self.assertEqual(result["ui"]["events"][-1]["gesture"], expected)

    def test_upload_preserves_navigation_but_defers_new_voice_holds(self):
        self.native.report(100, 1, 433, 254)
        uploading = self.native.begin_frame(42, 120)
        self.assertEqual(uploading["ui"]["frame"], "0000002a")
        self.assertTrue(uploading["ui"]["holdToTalk"])
        self.assertFalse(uploading["ui"]["holdReady"])
        self.assertFalse(self.native.idle(True))
        released = self.native.report(180, 0)
        self.assertEqual(released["ui"]["events"][0]["gesture"], 1)
        center = self.native.report(300, 1, 233, 240)
        self.assertFalse(center["touch"]["holdable"])
        self.native.report(400, 0)
        committed = self.native.frame(42, presented=False)
        self.assertTrue(committed["ui"]["holdToTalk"])
        self.assertFalse(committed["ui"]["holdReady"])
        drawn = self.native.present(self.native.version)
        self.assertTrue(drawn["ui"]["holdToTalk"])
        self.assertTrue(drawn["ui"]["holdReady"])

    def test_new_context_rejects_release_from_old_frame(self):
        self.native.report(100, 1, 35, 247)
        self.native.begin_frame(43, 120)
        self.assertEqual(self.native.status()["ui"]["frame"], "00000000")
        self.native.frame(43)
        self.assertEqual(self.native.report(200, 0)["ui"]["events"], [])
        self.assertEqual(self.tap(400, 35, 247)["ui"]["events"][0]["eventFrame"], "0000002b")

    def test_clear_discards_frame_and_gates_without_discarding_queued_input(self):
        self.tap(100, 35, 247)
        self.native.report(300, 1, 433, 254)
        cleared = self.native.clear(320)
        self.assertFalse(cleared["imageActive"])
        self.assertTrue(cleared["ui"]["holdToTalk"])
        self.assertFalse(cleared["ui"]["holdReady"])
        self.assertEqual(cleared["ui"]["frame"], "00000000")
        self.assertEqual(len(cleared["ui"]["events"]), 1)
        self.assertEqual(len(self.native.report(400, 0)["ui"]["events"]), 1)

    def test_control_regions_cannot_become_voice_holds(self):
        for index, (x, y) in enumerate([(35, 247), (433, 254), (233, 80), (178, 435), (288, 435)]):
            self.assertTrue(control_region(x, y))
            now = index * 300
            self.assertFalse(self.native.report(now, 1, x, y)["touch"]["holdable"])
            self.native.report(now + 80, 0)
        self.assertFalse(control_region(233, 240))
        self.assertFalse(control_region(466, 240))
        self.assertFalse(control_region(233, 466))
        self.assertTrue(self.native.report(2000, 1, 233, 240)["touch"]["holdable"])
        self.native.report(2200, 0)
        self.native.frame(44, voice=False)
        self.assertFalse(self.native.report(2400, 1, 233, 240)["touch"]["holdable"])

    def test_wake_consumes_long_contact_and_overlap_until_release_and_draw(self):
        self.assertTrue(self.native.idle(True))
        wake = self.native.report(100, 1, 233, 240)
        self.assertEqual(wake["ui"]["wake"], 1)
        self.assertTrue(wake["ui"]["wakePending"])
        self.assertFalse(wake["touch"]["holdable"])
        self.native.present(1)
        self.assertTrue(self.native.status()["ui"]["wakePending"])
        self.native.frame(42)
        self.native.interrupt(1200)
        held = self.native.report(1400, 1, 233, 240)
        self.assertFalse(held["touch"]["holdable"])
        self.native.button(1420, True)
        self.native.report(1500, 0)
        self.assertTrue(self.native.status()["ui"]["wakePending"])
        result = self.native.button(1600, False)
        self.assertFalse(result["ui"]["wakePending"])
        self.assertEqual(result["ui"]["events"], [])
        self.assertEqual(result["touch"]["taps"], 0)
        self.assertEqual(self.tap(2000, 35, 247)["ui"]["events"][0]["x"], 35)

    def test_release_before_fresh_draw_stays_blocked(self):
        self.native.idle(True)
        self.tap(100, 233, 240)
        self.assertTrue(self.native.status()["ui"]["wakePending"])
        self.native.frame(42, presented=False)
        self.native.present(1)
        self.assertTrue(self.native.status()["ui"]["wakePending"])
        self.native.present(2)
        self.assertFalse(self.native.status()["ui"]["wakePending"])

    def test_invalid_or_multitouch_needs_release_before_wake(self):
        self.native.idle(True)
        self.native.report(100, 1, 466, 0, raw=True)
        self.native.report(120, 1, 233, 233)
        self.assertTrue(self.native.status()["ui"]["idle"])
        self.native.report(140, 0)
        self.native.report(160, 2, 233, 233)
        self.native.report(180, 1, 233, 233)
        self.assertTrue(self.native.status()["ui"]["idle"])
        self.native.report(200, 0)
        self.native.report(220, 1, 233, 233)
        self.assertEqual(self.native.status()["ui"]["wake"], 1)

    def test_event_queue_is_bounded_and_acknowledgements_are_specific(self):
        for index in range(18):
            self.tap(index * 200 + 20, 35, 247)
        status = self.native.status()["ui"]
        self.assertEqual(len(status["events"]), 16)
        self.assertEqual(status["dropped"], 2)
        self.assertEqual(len(self.native.ack(999)["ui"]["events"]), 16)
        remaining = self.native.ack(8)["ui"]["events"]
        self.assertEqual([event["event"] for event in remaining], list(range(9, 17)))
        self.assertEqual(len(self.native.ack(18)["ui"]["events"]), 8)

    def test_lease_expiry_clears_queue_and_renewal_requires_frame(self):
        self.tap(100, 35, 247)
        expired = self.native.status(5000)
        self.assertFalse(expired["ui"]["active"])
        self.assertFalse(expired["imageActive"])
        self.assertEqual(expired["ui"]["events"], [])
        renewed = self.native.configure(True, 5100)
        self.assertEqual(renewed["ui"]["frame"], "00000000")
        self.assertEqual(self.tap(5200, 35, 247)["ui"]["events"], [])
        self.native.frame(42)
        self.assertEqual(self.tap(5500, 35, 247)["ui"]["events"][0]["gesture"], 1)

    def test_reconnected_controls_require_a_fresh_press_even_with_the_same_token(self):
        self.native.report(100, 1, 35, 247)
        self.native.configure(False, 120)
        self.native.configure(True, 140)
        self.native.frame(42)
        self.assertEqual(self.native.report(200, 0)["ui"]["events"], [])
        self.assertEqual(len(self.tap(400, 35, 247)["ui"]["events"]), 1)
        self.native.ack(1)
        self.native.button(600, True)
        self.native.configure(False, 620)
        self.native.configure(True, 640)
        self.native.frame(42)
        self.assertEqual(self.native.button(700, False)["ui"]["events"], [])
        self.native.button(800, True)
        self.assertEqual(self.native.button(900, False)["ui"]["events"][0]["gesture"], 5)

        self.native.report(1000, 1, 233, 240)
        self.native.status(5640)  # Expire the renewed lease while a finger is held.
        self.native.configure(True, 5700)
        self.native.frame(42)
        self.assertFalse(self.native.report(5800, 1, 233, 240)["touch"]["holdable"])
        self.native.report(5900, 0)
        self.assertTrue(self.native.report(6000, 1, 233, 240)["touch"]["holdable"])

    def test_transport_error_cancels_active_gesture_but_idle_gap_does_not(self):
        self.native.interrupt(500)
        self.assertEqual(len(self.tap(800, 35, 247)["ui"]["events"]), 1)
        self.native.report(1000, 1, 35, 247)
        self.assertEqual(self.native.interrupt(1050, error=True)["touch"]["error"], 1)
        released = self.native.report(1100, 0)
        self.assertEqual(len(released["ui"]["events"]), 1)
        self.assertEqual(released["touch"]["error"], 0)

    def test_native_ring_uses_fixed_frame_and_input_time_is_monotonic(self):
        pixels = bytes(466 * 466 * 2)
        ring = render_ring(pixels, 1, 3000)
        self.assertEqual(len(ring), len(pixels))
        self.assertNotEqual(ring, pixels)
        self.assertEqual(ring[(233 * 466 + 233) * 2:(233 * 466 + 233) * 2 + 2], b"\0\0")
        with self.assertRaises(ValueError):
            render_ring(pixels, float("inf"), 3000)
        self.native.status(100)
        with self.assertRaises(ValueError):
            self.native.status(99)
        with self.assertRaises(ValueError):
            self.native.report(100, 1, -1, 20)
        self.native.close()
        with self.assertRaises(RuntimeError):
            self.native.status()


if __name__ == "__main__":
    unittest.main()
