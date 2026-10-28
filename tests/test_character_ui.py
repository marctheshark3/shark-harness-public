"""Exercise the physical controller with a simulated board and deterministic time."""
import unittest

import test_device_ui as controls


class CharacterUITests(unittest.TestCase):
    setUp = controls.IdleUITests.setUp
    tearDown = controls.DeviceUITests.tearDown
    tick = controls.IdleUITests.tick
    event = controls.IdleUITests.event

    def test_animation_is_bounded_and_keeps_touch_tokens_and_destination(self):
        self.tick()
        token = self.ui.presented_token
        count = len(self.device.paints)
        self.assertEqual(self.ui.presented["character"], "reef")
        self.assertEqual(self.ui.presented["activity"], "idle")
        self.tick(after=.49)
        self.assertEqual(len(self.device.paints), count)
        self.tick(after=.01)
        self.assertEqual(len(self.device.paints), count + 1)
        self.assertEqual(self.ui.presented_token, token)
        self.event(token=token)
        self.tick()
        self.assertFalse(self.store.get()["enabled"])
        self.assertEqual(self.store.get()["target"]["id"], "one")
        self.assertEqual(self.store.session.sent, [])

    def test_motion_never_prevents_sleep_and_sleep_stops_uploads(self):
        self.tick()
        for _ in range(4):
            self.tick(after=15)
        self.assertTrue(self.ui.idle_active)
        count = len(self.device.paints)
        self.tick(after=60)
        self.assertEqual(len(self.device.paints), count)
        self.assertEqual(self.store.session.sent, [])

    def test_decorative_frame_in_flight_is_not_physical_activity(self):
        self.tick()
        self.device.auto_present = False
        last_activity = self.ui.last_activity
        self.tick(after=.5)
        count = len(self.device.paints)
        self.tick(after=.2)
        self.assertEqual(self.ui.last_activity, last_activity)
        self.assertEqual(len(self.device.paints), count)

    def test_status_update_in_flight_preserves_the_original_sleep_deadline(self):
        self.tick()
        self.device.auto_present = False
        self.snapshot["threads"][0]["state"] = "working"
        self.tick(after=59)
        self.tick(after=.1)
        self.assertEqual(self.ui.last_activity, 100)
        self.device.present()
        self.tick(after=.9)
        self.assertTrue(self.ui.idle_active)

    def test_continual_session_metadata_cannot_keep_the_display_awake(self):
        self.tick()
        for state in ("working", "idle", "working", "waiting"):
            self.snapshot["threads"][0]["state"] = state
            self.tick(after=15)
        self.assertTrue(self.ui.idle_active)

    def test_touch_pending_record_and_microphone_freeze_decoration(self):
        self.tick()
        count = len(self.device.paints)
        self.device.health["touch"]["pressed"] = True
        self.tick(after=1)
        self.assertEqual(len(self.device.paints), count)
        self.device.health["touch"]["pressed"] = False
        self.ui.record_pending = lambda: True
        self.tick(after=1)
        self.assertEqual(len(self.device.paints), count)
        self.ui.record_pending = lambda: False
        self.device.health["mic"] = {"recording": True}
        self.tick(after=1)
        self.assertEqual(len(self.device.paints), count)
        self.store.begin({"captureId": 1})
        self.tick()
        self.assertEqual(self.ui.presented["kind"], "listening")
        self.assertNotIn("character", self.ui.presented)

    def test_motion_off_and_no_character_are_supported(self):
        self.ui.character_motion = False
        self.tick()
        count = len(self.device.paints)
        self.tick(after=1)
        self.assertEqual(len(self.device.paints), count)
        self.ui.character = None
        self.tick()
        self.assertIsNone(self.ui.presented["character"])
        count = len(self.device.paints)
        self.tick(after=1)
        self.assertEqual(len(self.device.paints), count)

    def test_bad_custom_pack_falls_back_without_losing_the_controller(self):
        ui = type(self.ui)(self.device, 1, lambda: None, selection=self.ui.selection,
                           api=self.ui.api, character="/missing-character.json")
        self.assertEqual(ui.character.id, "reef")


if __name__ == "__main__":
    unittest.main()
