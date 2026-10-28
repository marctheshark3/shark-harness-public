import json
from pathlib import Path
import tempfile
import unittest

from device import ProtocolError, idle_seconds
from device_ui import DeviceUI, lines, render
from listen import ListenStore
from test_listen import Session


class ScreenDevice:
    def __init__(self): self.paints, self.commands = [], []
    def send_frame(self, pixels, **kwargs): self.paints.append(kwargs); return {"crc32": "12345678"}
    def request(self, op, **kwargs): self.commands.append(op)


class IdleScreenDevice(ScreenDevice):
    """Synthetic board with independently controlled frame presentation."""
    def __init__(self):
        super().__init__()
        self.calls = []
        self.auto_present = True
        self.reject_idle = False
        self.health = {"imageActive": False, "ui": {"active": True, "idleMode": True, "idle": False, "wake": 0, "wakePending": False,
            "eventQueue": True, "events": [], "event": 0, "contacts": 0, "releases": 0,
            "imageVersion": 0, "presentedVersion": 0}, "touch": {"pressed": False}}

    def present(self):
        status = self.health["ui"]
        status["presentedVersion"] = status["imageVersion"]
        if not self.health["touch"].get("pressed") and not status.get("buttonPressed"):
            status["wakePending"] = False

    def send_frame(self, pixels, **kwargs):
        result = super().send_frame(pixels, **kwargs)
        self.health["imageActive"] = True
        self.health["ui"]["imageVersion"] += 1
        if self.auto_present: self.present()
        return result

    def request(self, op, **kwargs):
        super().request(op, **kwargs)
        self.calls.append((op, kwargs))
        status = self.health["ui"]
        if op == "ui.idle":
            if kwargs["enabled"] and self.reject_idle:
                self.reject_idle = False
                raise ProtocolError("Device rejected ui.idle: ui_idle_busy")
            status["idle"] = kwargs["enabled"]
            if not kwargs["enabled"]: status["wakePending"] = True
        elif op == "ui.ack":
            status["events"] = [e for e in status["events"] if e["event"] > kwargs["event"]]
        return json.loads(json.dumps(self.health))


class DeviceUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ListenStore(Session(), "one", True)
        self.snapshot = {"connected": True, "threads": [
            {"id": "one", "title": "First session", "project": "Project", "projectId": "p", "state": "idle"},
            {"id": "two", "title": "Second session", "project": "Project", "projectId": "p", "state": "working"}], "overlay": None}
        self.current_session = {"threadId": "two", "projectId": "p", "title": "Second session"}
        self.device = ScreenDevice()
        def api(path, value=None, method=None):
            if path == "/api/status": return self.snapshot
            if path == "/api/current-session":
                if isinstance(self.current_session, Exception): raise self.current_session
                return self.current_session
            if path == "/api/listen": return self.store.discard() if method == "DELETE" else self.store.get()
            return {"/api/listen/configure": self.store.configure, "/api/listen/new": self.store.new_session,
                    "/api/listen/confirm": self.store.confirm}[path](value)
        self.ui = DeviceUI(self.device, 1, lambda: None,
                           selection=Path(self.temp.name) / "selected.json", api=api)
        self.ui.choosing_project = False  # Most tests exercise an already-open project.

    def tearDown(self): self.temp.cleanup()

    def tick(self, event=0, gesture=0, token=None, x=330, y=365):
        self.ui.last_refresh = 0
        self.ui.tick({"ui": {"event": event, "gesture": gesture, "eventFrame": token, "x": x, "y": y}})

    def test_swipe_selects_session_and_stale_screen_cannot_act(self):
        self.tick()
        old = self.ui.presented_token
        self.tick(1, 2, old)
        self.assertEqual(self.store.get()["target"]["id"], "two")
        self.assertNotEqual(old, self.ui.presented_token)
        self.tick(2, 3, old)
        self.assertEqual(self.store.get()["target"]["id"], "two")
        self.tick(3, 3, self.ui.presented_token)
        self.assertEqual(self.store.get()["target"]["id"], "one")

    def test_new_pause_and_resume_do_not_dispatch_prompts(self):
        self.tick()
        self.tick(1, 1, self.ui.presented_token, x=130)
        self.assertTrue(self.store.get()["target"]["new"])
        self.tick(2, 1, self.ui.presented_token)
        self.assertFalse(self.store.get()["enabled"])
        self.tick(3, 1, self.ui.presented_token)
        self.assertTrue(self.store.get()["enabled"])
        self.assertEqual(self.store.session.sent, [])

    def test_visible_arrows_accept_taps_and_speech_tap_does_not_navigate(self):
        self.tick()
        self.tick(1, 1, self.ui.presented_token, x=433, y=226)
        self.assertEqual(self.store.get()["target"]["id"], "two")
        self.tick(2, 4, self.ui.presented_token, x=26, y=226)
        self.assertEqual(self.store.get()["target"]["id"], "two")
        self.tick(3, 1, self.ui.presented_token, x=26, y=226)
        self.assertEqual(self.store.get()["target"]["id"], "one")

    def test_boot_selects_open_session_without_dispatching(self):
        self.tick()
        self.tick(1, 5, self.ui.presented_token, x=0, y=0)
        self.assertTrue(self.store.get()["enabled"])
        self.assertEqual(self.store.get()["target"]["id"], "two")
        self.assertEqual(self.store.session.sent, [])
        self.assertEqual(self.device.commands[:2], ["mic.configure", "mic.clear"])

    def test_boot_selects_across_projects_from_chooser(self):
        self.snapshot["threads"].append(dict(id="three", title="Other", project="Other", projectId="q", state="idle"))
        self.current_session = dict(threadId="three", projectId="q")
        self.ui.choosing_project = True
        self.tick()
        self.tick(1, 5, self.ui.presented_token)
        self.assertEqual(self.store.get()["target"]["id"], "three")
        self.assertFalse(self.ui.choosing_project)
        self.assertEqual(self.store.session.sent, [])

    def test_boot_failed_lookup_keeps_target_and_disarms_screen(self):
        self.current_session = ValueError("Focus one T3 window")
        self.tick()
        self.tick(1, 5, self.ui.presented_token)
        self.assertEqual(self.store.get()["target"]["id"], "one")
        self.assertIsNotNone(self.ui.control_error)
        self.assertFalse(self.device.paints[-1]["hold_to_talk"])
        self.assertEqual(self.store.session.sent, [])

    def test_multiple_windows_show_unarmed_choices_then_arm_the_chosen_title(self):
        self.current_session = dict(choices=[dict(threadId='one', projectId='p', title='First session'),
                                            dict(threadId='two', projectId='p', title='Second session')])
        self.ui.choosing_project = True
        self.tick()
        self.tick(1, 5, self.ui.presented_token)
        self.assertEqual(self.ui.presented['kind'], 't3-choices')
        self.assertFalse(self.device.paints[-1]['hold_to_talk'])
        self.assertEqual(self.store.get()['target']['id'], 'one')
        self.tick(2, 1, self.ui.presented_token, x=433, y=226)
        self.assertEqual(self.ui.presented['title'], 'Second session')
        self.assertEqual(self.store.get()['target']['id'], 'one')
        self.tick(3, 1, self.ui.presented_token)
        self.assertEqual(self.ui.presented['kind'], 'home')
        self.assertTrue(self.device.paints[-1]['hold_to_talk'])
        self.assertEqual(self.store.get()['target']['id'], 'two')
        self.assertEqual(self.store.session.sent, [])
        # A subsequent hold is pinned to the title the user explicitly selected.
        job = self.store.begin({'captureId':12})['job']
        self.assertEqual(job['threadId'], 'two')

    def test_old_choice_card_cannot_select_a_new_candidate(self):
        self.current_session = dict(choices=[dict(threadId='one', projectId='p', title='First session'),
                                            dict(threadId='two', projectId='p', title='Second session')])
        self.queued([])
        self.tick(1, 5, self.ui.presented_token)
        token = self.ui.presented_token
        self.queued([dict(event=2, gesture=2, eventFrame=token, x=433, y=226),
                     dict(event=3, gesture=1, eventFrame=token, x=330, y=365)])
        self.assertEqual(self.ui.presented['title'], 'Second session')
        self.assertEqual(self.ui.presented['kind'], 't3-choices')
        self.assertEqual(self.store.get()['target']['id'], 'one')

    def test_failed_lookup_retry_returns_from_projects_to_recordable_home(self):
        from urllib.error import URLError
        for error in [ValueError('Focus T3'), TimeoutError('timeout'), URLError('connection reset')]:
            with self.subTest(error=error):
                self.ui.open_projects('p')
                self.current_session = error
                self.tick()
                self.tick(1, 5, self.ui.presented_token)
                self.assertEqual(self.ui.presented['kind'], 'current-error')
                self.assertFalse(self.device.paints[-1]['hold_to_talk'])
                self.current_session = dict(threadId='two', projectId='p', title='Second session')
                self.tick(2, 1, self.ui.presented_token)
                self.assertEqual(self.ui.presented['kind'], 'home')
                self.assertTrue(self.device.paints[-1]['hold_to_talk'])
                self.assertEqual(self.store.get()['target']['id'], 'two')
        self.assertEqual(self.store.session.sent, [])

    def test_boot_cannot_retarget_recording_or_review(self):
        self.ui.auto_send = False
        self.tick()
        self.store.begin({"captureId": 7})
        self.tick(1, 5, self.ui.presented_token)
        self.assertEqual(self.store.get()["target"]["id"], "one")
        job = self.store.get()["job"]["id"]
        self.store.progress({"id": job, "state": "transcribing"})
        self.store.progress({"id": job, "state": "review", "text": "A prompt"})
        self.tick(2, 5, self.ui.presented_token)
        self.assertEqual(self.store.get()["target"]["id"], "one")
        self.assertEqual(self.store.session.sent, [])

    def test_browser_destination_survives_refresh_without_local_project(self):
        original = self.store.session.describe
        self.store.session.describe = lambda tid: (dict(thread_id="voice-selected", title="Remote conversation",
            project_id="not-local", projectTitle="Remote project", environment="Spark", browser=True, new=True)
            if tid == "voice-selected" else original(tid))
        self.current_session = dict(threadId="voice-selected", projectId="not-local", browser=True)
        self.tick()
        self.tick(1, 5, self.ui.presented_token)
        self.tick()
        self.assertEqual(self.store.get()["target"]["id"], "voice-selected")
        self.assertEqual(self.ui.screen()["kind"], "home")
        self.assertEqual(self.ui.screen()["project"], "Spark · Remote project")
        self.tick(2, 1, self.ui.presented_token, x=330, y=365)
        self.assertFalse(self.store.get()["enabled"])
        self.tick(3, 1, self.ui.presented_token, x=130, y=365)
        self.assertTrue(self.ui.choosing_project)
        self.assertEqual(self.store.session.sent, [])

    def test_browser_edge_taps_cannot_select_invisible_local_session_arrows(self):
        original = self.store.session.describe
        self.store.session.describe = lambda tid: (dict(thread_id=tid, title='Browser conversation',
            project_id='p', projectTitle='Project', environment='Spark', browser=True, new=False)
            if tid == 'voice-selected' else original(tid))
        self.current_session = dict(threadId='voice-selected', projectId='p', browser=True)
        self.queued([])
        self.tick(1, 5, self.ui.presented_token)
        for event, gesture, x in [(2, 1, 433), (3, 1, 26), (4, 2, 233), (5, 3, 233)]:
            self.tick(event, gesture, self.ui.presented_token, x=x, y=226)
            self.assertEqual(self.store.get()['target']['id'], 'voice-selected')
        self.assertEqual(self.ui.presented['total'], 1)

    def test_browser_disconnect_disarms_and_boot_binds_a_reloaded_browser(self):
        original = self.store.session.describe
        ready = True
        def describe(tid):
            if tid.startswith('voice-'):
                return dict(thread_id=tid, title='Browser conversation', project_id='p',
                    projectTitle='Project', environment='Spark', browser=True, new=False,
                    ready=ready or tid == 'voice-new', error='Browser disconnected. Press BOOT.')
            return original(tid)
        self.store.session.describe = describe
        self.current_session = dict(threadId='voice-old', projectId='p', browser=True)
        self.tick()
        self.tick(1, 5, self.ui.presented_token)
        self.assertTrue(self.device.paints[-1]['hold_to_talk'])
        ready = False
        self.tick()
        self.assertEqual(self.ui.presented['kind'], 'voice-offline')
        self.assertFalse(self.device.paints[-1]['hold_to_talk'])
        with self.assertRaises(ValueError): self.store.begin({'captureId':4})
        self.current_session = dict(threadId='voice-new', projectId='p', browser=True)
        self.tick(2, 5, self.ui.presented_token)
        self.assertEqual(self.ui.presented['kind'], 'home')
        self.assertTrue(self.device.paints[-1]['hold_to_talk'])
        self.assertEqual(self.store.get()['target']['id'], 'voice-new')

    def queued(self, events):
        self.ui.last_refresh = 0
        self.ui.tick({"ui": {"eventQueue": True, "events": events,
                            "event": events[-1]["event"] if events else 0}})

    def test_rapid_controls_survive_card_changes_and_paint_once_per_batch(self):
        self.queued([])
        token = self.ui.presented_token
        def event(i, gesture=2, x=410, y=240):
            return dict(event=i, gesture=gesture, eventFrame=token, x=x, y=y)
        self.queued([event(1), event(2), event(3, 3), event(4, 1, 330, 365)])
        self.assertEqual(self.store.get()["target"]["id"], "two")
        self.assertFalse(self.store.get()["enabled"])
        self.assertEqual(len(self.device.paints), 2)
        self.assertEqual(self.ui.presented_token, token)
        self.queued([event(5)])
        self.assertEqual(self.store.get()["target"]["id"], "one")
        self.assertFalse(self.store.get()["enabled"])  # Navigation preserves Pause.
        self.assertEqual(self.store.session.sent, [])
        self.assertIn("ui.ack", self.device.commands)

    def test_queued_home_input_cannot_act_on_voice_or_new_session_screen(self):
        self.queued([])
        token = self.ui.presented_token
        event = dict(event=1, gesture=1, eventFrame=token, x=130, y=365)
        self.queued([event, {**event, "event": 2}])
        self.assertTrue(self.store.get()["target"]["new"])  # Second tap cannot cancel new.
        self.assertNotEqual(self.ui.presented_token, token)
        self.queued([{**event, "event": 3, "gesture": 2}])
        self.assertTrue(self.store.get()["target"]["new"])
        self.store.begin({"captureId": 7})
        self.queued([{**event, "event": 4}])
        self.assertEqual(self.store.get()["job"]["state"], "listening")
        self.assertEqual(self.store.session.sent, [])

    def test_queue_startup_acks_old_events_without_replaying_them(self):
        self.queued([dict(event=99, gesture=2, eventFrame="old", x=410, y=240)])
        self.assertEqual(self.store.get()["target"]["id"], "one")
        self.assertIn("ui.ack", self.device.commands)

    def test_background_session_activity_does_not_repaint_home(self):
        self.queued([])
        self.snapshot["threads"][1]["state"] = "waiting"
        self.queued([])
        self.assertEqual(len(self.device.paints), 1)

    def test_selected_session_state_updates_character_without_changing_destination(self):
        self.queued([])
        self.snapshot["threads"][0]["state"] = "working"
        self.queued([])
        self.assertEqual(self.ui.presented["activity"], "working")
        self.assertEqual(self.store.get()["target"]["id"], "one")

    def test_capture_start_between_polls_freezes_destination_before_bridge_claim(self):
        self.queued([])
        for i, mic in enumerate([{"recording": True}, {"available": True}], 1):
            event = dict(event=i, gesture=2, eventFrame=self.ui.presented_token, x=410, y=240)
            self.ui.tick({"mic": mic, "ui": {"eventQueue": True, "event": i, "events": [event]}})
            self.assertEqual(self.store.get()["target"]["id"], "one")
            self.assertIsNone(self.store.get()["job"])
        self.assertEqual(self.store.session.sent, [])

    def add_second_project(self):
        # Identical display names must never combine two distinct project IDs.
        self.snapshot["threads"].append({"id": "three", "projectId": "q", "project": "Project",
                                         "title": "Other project session", "state": "idle"})

    def test_project_first_flow_gates_voice_and_scopes_navigation_and_new(self):
        self.add_second_project()
        self.ui.choosing_project = True
        self.queued([])
        self.assertEqual(self.ui.presented["kind"], "projects")
        self.assertFalse(self.device.paints[-1]["hold_to_talk"])
        self.tick(1, 2, self.ui.presented_token)
        self.assertEqual(self.ui.presented["projectId"], "q")
        self.assertEqual(self.store.get()["target"]["id"], "one")  # Browsing is not selecting.
        self.tick(2, 1, self.ui.presented_token, x=330, y=365)
        self.assertEqual(self.store.get()["target"]["id"], "three")
        self.assertTrue(self.device.paints[-1]["hold_to_talk"])
        self.assertEqual(self.ui.presented["total"], 1)
        self.tick(3, 2, self.ui.presented_token)
        self.assertEqual(self.store.get()["target"]["id"], "three")
        self.tick(4, 1, self.ui.presented_token, x=130, y=365)
        self.assertTrue(self.store.get()["target"]["new"])
        self.assertEqual(self.store.get()["target"]["projectId"], "q")
        self.assertEqual(self.store.session.sent, [])
        draft_id = self.store.get()["target"]["id"]
        self.tick(5, 1, self.ui.presented_token, x=233, y=80)
        self.assertEqual(self.ui.presented["kind"], "projects")
        self.tick(6, 1, self.ui.presented_token, x=330, y=365)
        self.assertEqual(self.ui.presented["kind"], "home")
        self.assertEqual(self.store.get()["target"]["id"], draft_id)
        self.assertTrue(self.store.get()["target"]["new"])

    def test_project_title_and_open_sessions_button_select_visible_project(self):
        self.add_second_project()
        # Includes the user's reported Open taps, previously interpreted as Back.
        for x, y in [(252, 171), (261, 180), (330, 365), (290, 400), (275, 428)]:
            with self.subTest(x=x, y=y):
                self.ui.select("one") if self.ui.listen else self.queued([])
                self.ui.open_projects("q")
                self.queued([])
                self.ui.event({"gesture": 1, "x": x, "y": y}, self.ui.screen())
                self.assertEqual(self.store.get()["target"]["projectId"], "q")
                self.assertFalse(self.ui.choosing_project)
                self.ui.event({"gesture": 1, "x": 130, "y": 365}, self.ui.screen())
                self.assertEqual(self.store.get()["target"]["projectId"], "q")
                self.assertTrue(self.store.get()["target"]["new"])
                self.assertEqual(self.store.session.sent, [])

    def test_curved_action_ends_and_project_label_match_the_firmware_hit_regions(self):
        self.tick()
        self.tick(1, 1, self.ui.presented_token, x=288, y=435)
        self.assertFalse(self.store.get()["enabled"])
        self.tick(2, 1, self.ui.presented_token, x=178, y=435)
        self.assertTrue(self.store.get()["target"]["new"])
        self.tick(3, 1, self.ui.presented_token, x=178, y=435)
        self.assertFalse(self.store.get()["target"].get("new"))
        self.tick(4, 1, self.ui.presented_token, x=233, y=119)
        self.assertEqual(self.ui.presented["kind"], "projects")
        self.assertEqual(self.store.session.sent, [])

    def test_project_switch_restores_last_session_and_rejects_old_home_input(self):
        self.add_second_project()
        self.queued([])
        self.tick(1, 2, self.ui.presented_token)  # Second session in p.
        self.assertEqual(self.store.get()["target"]["id"], "two")

        home_token = self.ui.presented_token
        self.tick(2, 1, home_token, x=233, y=80)
        self.assertEqual(self.ui.presented["kind"], "projects")
        self.tick(3, 2, home_token)
        self.assertEqual(self.ui.presented["projectId"], "p")  # Stale home swipe ignored.
        self.tick(4, 2, self.ui.presented_token)
        self.tick(5, 1, self.ui.presented_token, x=330, y=365)
        self.assertEqual(self.store.get()["target"]["id"], "three")
        self.tick(6, 1, self.ui.presented_token, x=233, y=80)
        self.tick(7, 3, self.ui.presented_token)
        self.tick(8, 1, self.ui.presented_token, x=330, y=365)
        self.assertEqual(self.store.get()["target"]["id"], "two")

    def test_empty_project_can_open_start_and_cancel_a_draft(self):
        self.snapshot['projects'] = [{'id': 'empty', 'title': 'Empty', 'path': '/projects/empty'}]
        self.snapshot['threads'] = []
        self.store.configure({'threadId': None, 'enabled': False})
        self.ui.choosing_project = True
        self.queued([])
        self.assertEqual(self.ui.presented['kind'], 'projects')
        self.assertEqual(self.ui.presented['sessions'], 0)
        self.tick(1, 1, self.ui.presented_token)
        self.assertEqual(self.ui.presented['kind'], 'empty-project')
        self.assertFalse(self.device.paints[-1]['hold_to_talk'])
        self.tick(2, 1, self.ui.presented_token)
        self.assertTrue(self.store.get()['target']['new'])
        self.assertEqual(self.store.get()['target']['projectId'], 'empty')
        self.assertTrue(self.store.get()['autoSend'])
        self.tick(3, 1, self.ui.presented_token, x=130)
        self.assertIsNone(self.store.get()['target'])
        self.assertEqual(self.ui.presented['kind'], 'projects')
        self.assertEqual(self.store.session.sent, [])

    def test_picker_new_is_direct_and_swipes_cannot_cancel_draft(self):
        self.add_second_project()
        self.ui.open_projects('q')
        self.queued([])
        self.tick(1, 1, self.ui.presented_token, x=130)
        self.assertEqual(self.ui.presented['projectId'], 'q')
        self.assertTrue(self.ui.presented['new'])
        self.tick(2, 2, self.ui.presented_token)
        self.assertTrue(self.ui.presented['new'])
        self.tick(3, 1, self.ui.presented_token, x=130)
        self.assertEqual(self.store.get()['target']['id'], 'three')
        self.assertEqual(self.store.session.sent, [])

    def test_project_swipe_batch_does_not_drop_navigation_or_open_unseen_project(self):
        self.add_second_project()
        self.ui.choosing_project = True
        self.queued([])
        token = self.ui.presented_token
        def event(i, gesture=2):
            return dict(event=i, gesture=gesture, eventFrame=token, x=330, y=365)
        self.queued([event(1), event(2), event(3), event(4, 1)])
        self.assertEqual(self.ui.presented['projectId'], 'q')
        self.assertEqual(self.ui.presented['kind'], 'projects')
        self.assertEqual(self.store.get()['target']['id'], 'one')
        self.tick(5, 1, self.ui.presented_token)
        self.assertEqual(self.store.get()['target']['id'], 'three')

    def test_open_project_dismisses_visual_and_hold_targets_its_session(self):
        self.add_second_project()
        self.ui.open_projects('q')
        self.queued([])
        self.snapshot['overlay'] = {'kind': 'message', 'title': 'Old visual', 'expiresAt': 123}
        self.tick(1, 1, self.ui.presented_token)
        self.assertEqual(self.ui.presented['kind'], 'home')
        job = self.store.begin({'captureId': 9})['job']
        self.assertEqual(job['threadId'], 'three')
        self.store.progress({'id': job['id'], 'state': 'transcribing'})
        self.store.progress({'id': job['id'], 'state': 'review', 'text': 'Run the checks'})
        self.assertEqual(self.store.session.sent[0][:2], ('three', 'Run the checks'))

    def test_missing_last_session_does_not_silently_jump_to_other_project(self):
        self.add_second_project()
        self.queued([])
        self.snapshot["threads"] = [t for t in self.snapshot["threads"] if t["projectId"] == "q"]
        self.queued([])
        self.assertEqual(self.ui.presented["kind"], "projects")
        self.assertFalse(self.device.paints[-1]["hold_to_talk"])
        self.assertEqual(self.store.get()["target"]["id"], "one")

    def test_no_remaining_sessions_disables_hold_instead_of_showing_stale_target(self):
        self.queued([])
        self.snapshot["threads"] = []
        self.queued([])
        self.assertEqual(self.ui.presented["kind"], "empty-home")
        self.assertFalse(self.device.paints[-1]["hold_to_talk"])

    def test_new_session_failure_stays_on_device_instead_of_crashing_sender(self):
        self.queued([])
        from unittest.mock import patch
        with patch.object(self.store.session, "draft", side_effect=ValueError("Project unavailable")):
            self.tick(1, 1, self.ui.presented_token, x=130, y=365)
        self.assertEqual(self.ui.presented["kind"], "control-error")
        self.assertFalse(self.device.paints[-1]["hold_to_talk"])
        self.assertEqual(self.store.get()["target"]["id"], "one")
        self.tick(2, 1, self.ui.presented_token)
        self.assertEqual(self.ui.presented["kind"], "home")

    def test_long_review_contains_all_words_and_requires_destination_confirmation(self):
        self.ui.auto_send = False
        self.tick()
        job = self.store.begin({"captureId": 1})["job"]
        self.snapshot["threads"][0]["title"] = "Renamed after recording began"
        phrase = "Check all the tests and explain the errors. " * 15
        self.store.progress({"id": job["id"], "state": "transcribing"})
        self.store.progress({"id": job["id"], "state": "review", "text": phrase})
        self.tick()
        collected, event = [], 1
        while self.ui.presented["kind"] == "review":
            self.assertEqual(self.ui.presented["title"], job["threadTitle"])
            collected += self.ui.presented["lines"]
            self.tick(event, 1, self.ui.presented_token)
            event += 1
        self.assertEqual(" ".join(collected).split(), phrase.split())
        self.assertEqual(self.ui.presented["kind"], "confirm")
        self.assertEqual(self.ui.presented["title"], job["threadTitle"])
        self.assertEqual(self.store.session.sent, [])
        self.tick(event, 1, self.ui.presented_token)
        self.assertEqual(len(self.store.session.sent), 1)

    def test_long_code_tokens_wrap_and_screens_render(self):
        text = "identifier" * 80
        self.assertEqual("".join(lines(text)), text)
        for screen in [dict(kind="listening", title="A session", autoSend=True),
                       dict(kind="sent", title="A session"), dict(kind="empty-home")]:
            self.assertEqual(render(screen).size, (466, 466))

    def test_recording_failure_explains_the_error_and_tap_restores_controls(self):
        self.tick()
        job = self.store.begin({"captureId": 7})["job"]
        error = "USB sender restarted. Hold to record again."
        self.store.progress({"id": job["id"], "state": "error", "error": error})
        self.tick()
        self.assertEqual(self.ui.presented["error"], error)
        self.assertFalse(self.device.paints[-1]["hold_to_talk"])
        self.tick(1, 1, self.ui.presented_token, x=233, y=250)
        self.assertEqual(self.ui.presented["kind"], "home")
        self.assertTrue(self.device.paints[-1]["hold_to_talk"])
        self.assertEqual(self.store.session.sent, [])


class IdleUITests(unittest.TestCase):
    def setUp(self):
        DeviceUITests.setUp(self)
        self.now = 100.0
        self.ui.clock = lambda: self.now
        self.ui.last_activity = self.ui.state_since = self.now
        self.device = IdleScreenDevice()
        self.ui.display = self.device
        self.speech = {"job": None}
        api = self.ui.api
        self.ui.api = lambda path, value=None, method=None: (
            self.speech if path == "/api/speech" else api(path, value, method))

    tearDown = DeviceUITests.tearDown

    def tick(self, after=0):
        self.now += after
        self.ui.last_refresh = 0
        # The reply is a snapshot, independent from later USB acknowledgements.
        self.ui.tick(json.loads(json.dumps(self.device.health)))

    def event(self, gesture=1, token=None):
        status = self.device.health["ui"]
        status["event"] += 1
        status["events"].append({"event": status["event"], "gesture": gesture,
            "eventFrame": token or self.ui.presented_token, "x": 330, "y": 365})

    def rest(self):
        self.tick()
        self.tick(after=60)
        self.assertTrue(self.ui.idle_active)
        self.assertTrue(self.device.health["ui"]["idle"])

    def test_dim_after_sixty_seconds_and_polling_does_not_delay_it(self):
        self.tick()
        self.tick(after=30)
        self.snapshot["threads"][0]["state"] = "working"
        self.tick(after=29.99)
        self.assertNotIn("ui.idle", self.device.commands)
        self.tick(after=.01)
        self.assertTrue(self.ui.idle_active)
        self.assertEqual(self.device.calls[-1], ("ui.idle", {"enabled": True}))
        paints = len(self.device.paints)
        self.tick(after=600)
        self.assertTrue(self.ui.idle_active)
        self.assertEqual(len(self.device.paints), paints)
        self.assertNotIn("ui.configure", self.device.commands)

    def test_zero_disables_idle(self):
        self.ui.idle_seconds = 0
        self.tick()
        self.tick(after=600)
        self.assertFalse(self.ui.idle_active)
        self.assertNotIn("ui.idle", self.device.commands)

    def test_old_firmware_keeps_controls_and_never_receives_idle_commands(self):
        del self.device.health["ui"]["idleMode"]
        self.tick()
        self.tick(after=600)
        self.event()
        self.tick()
        self.assertFalse(self.store.get()["enabled"])
        self.assertNotIn("ui.idle", self.device.commands)

    def test_first_touch_or_boot_only_wakes_and_preserves_pause_and_destination(self):
        for enabled, gesture in [(True, 1), (False, 2), (True, 5)]:
            with self.subTest(enabled=enabled, gesture=gesture):
                self.store.configure({"threadId": "one", "enabled": enabled})
                self.tick()
                self.rest()
                old_token = self.ui.presented_token
                status = self.device.health["ui"]
                status.update(idle=False, wake=status["wake"] + 1, wakePending=True)
                self.device.health["touch"]["pressed"] = True
                self.event(gesture, old_token)
                self.tick(after=.1)
                self.assertFalse(self.ui.idle_active)
                self.assertNotEqual(self.ui.presented_token, old_token)
                self.assertEqual(self.store.get()["target"]["id"], "one")
                self.assertEqual(self.store.get()["enabled"], enabled)
                self.assertEqual(self.device.paints[-1]["hold_to_talk"], enabled)
                self.assertEqual(status["events"], [])
                self.device.health["touch"]["pressed"] = False
                self.device.present()
                self.tick()
        self.assertEqual(self.store.session.sent, [])
        self.assertNotIn("mic.clear", self.device.commands)

    def test_restoring_frame_blocks_input_until_presented_and_rejects_old_tokens(self):
        self.rest()
        old_token = self.ui.presented_token
        status = self.device.health["ui"]
        status.update(idle=False, wake=1, wakePending=True)
        self.device.auto_present = False
        self.tick()
        fresh_token = self.ui.presented_token
        self.event(token=fresh_token)
        self.tick()
        self.assertTrue(self.store.get()["enabled"])
        self.device.present()
        self.event(token=old_token)
        self.tick()
        self.assertTrue(self.store.get()["enabled"])
        self.event(token=fresh_token)
        self.tick()
        self.assertFalse(self.store.get()["enabled"])
        self.assertEqual(self.store.session.sent, [])

    def test_physical_activity_resets_idle_without_needing_a_valid_action(self):
        self.tick()
        self.tick(after=59)
        self.device.health["ui"]["contacts"] += 1
        self.tick()
        self.tick(after=59)
        self.assertFalse(self.ui.idle_active)
        self.tick(after=1)
        self.assertTrue(self.ui.idle_active)

    def test_hardware_work_and_held_inputs_restart_the_idle_delay(self):
        self.tick()
        for area, field in [("touch", "pressed"), ("ui", "buttonPressed"), ("ui", "wakePending"),
                            ("mic", "recording"), ("mic", "available"),
                            ("audio", "uploading"), ("audio", "armed"), ("audio", "playing")]:
            with self.subTest(area=area, field=field):
                self.device.health.setdefault(area, {})[field] = True
                self.tick(after=60)
                self.assertFalse(self.ui.idle_active)
                self.device.health[area][field] = False
                self.tick(after=59)
                self.assertFalse(self.ui.idle_active)
        self.tick(after=1)
        self.assertTrue(self.ui.idle_active)

    def test_unpresented_frame_cannot_enter_idle(self):
        self.device.auto_present = False
        self.tick()
        self.tick(after=60)
        self.assertFalse(self.ui.idle_active)
        self.device.present()
        self.tick(after=59)
        self.assertFalse(self.ui.idle_active)
        self.tick(after=1)
        self.assertTrue(self.ui.idle_active)

    def test_pending_synthesis_and_speech_stay_awake_and_wake_a_sleeping_display(self):
        self.tick()
        for state in ("queued", "synthesizing", "uploading", "armed", "playing"):
            self.speech["job"] = {"id": "synthetic-speech", "state": state}
            self.tick(after=60)
            self.assertFalse(self.ui.idle_active, state)
        self.speech["job"]["state"] = "done"
        self.tick(after=59)
        self.assertFalse(self.ui.idle_active)
        self.tick(after=1)
        self.assertTrue(self.ui.idle_active)
        self.speech["job"] = {"id": "next-speech", "state": "queued"}
        self.tick(after=.2)
        self.assertFalse(self.ui.idle_active)

    def test_voice_flow_and_actionable_errors_never_dim(self):
        self.tick()
        self.store.begin({"captureId": 12})
        for state in ("listening", "transcribing", "review", "sending", "sent", "empty", "error", "uncertain"):
            with self.subTest(state=state):
                self.store.job.update(state=state, text="A synthetic transcript", error="Check this result.")
                self.tick(after=60)
                self.assertFalse(self.ui.idle_active)
                self.assertEqual(self.ui.presented["kind"], state)
        self.assertEqual(self.store.session.sent, [])

    def test_draft_window_choice_and_control_error_stay_visible(self):
        self.tick()
        self.ui.start_new("p", "one")
        self.tick(after=60)
        self.assertFalse(self.ui.idle_active)
        self.assertTrue(self.ui.presented["new"])
        self.ui.select("one")
        self.ui.t3_choices = [{"threadId": "two", "title": "Second session"}]
        self.tick(after=60)
        self.assertFalse(self.ui.idle_active)
        self.assertEqual(self.ui.presented["kind"], "t3-choices")
        self.ui.t3_choices = []
        self.ui.control_error = "Choose a destination."
        self.snapshot["connected"] = False
        self.tick(after=60)
        self.assertFalse(self.ui.idle_active)

    def test_new_visual_wakes_and_stays_visible_until_expired(self):
        self.rest()
        self.snapshot["overlay"] = {"kind": "message", "title": "Synthetic visual", "expiresAt": 9999999999999}
        self.tick(after=.2)
        self.assertFalse(self.ui.idle_active)
        self.assertEqual(self.ui.presented["kind"], "scene")
        self.tick(after=60)
        self.assertFalse(self.ui.idle_active)
        self.snapshot["overlay"] = None  # Bridge expires scenes authoritatively.
        self.tick(after=59)
        self.assertFalse(self.ui.idle_active)
        self.tick(after=1)
        self.assertTrue(self.ui.idle_active)

    def test_explicit_browser_record_wakes_before_capture_is_retried(self):
        self.rest()
        self.ui.record_pending = lambda: True
        self.tick(after=.2)
        self.assertFalse(self.ui.idle_active)
        self.assertEqual(self.device.calls[-1], ("ui.idle", {"enabled": False}))
        self.assertEqual(self.ui.presented["kind"], "home")
        self.assertEqual(self.store.get()["target"]["id"], "one")
        self.assertEqual(self.store.session.sent, [])

    def test_busy_at_sleep_boundary_defers_without_restarting_or_changing_target(self):
        self.tick()
        self.device.reject_idle = True
        self.tick(after=60)
        self.assertFalse(self.ui.idle_active)
        self.assertEqual(self.store.get()["target"]["id"], "one")
        self.assertTrue(self.store.get()["enabled"])
        self.assertEqual(self.store.session.sent, [])
        self.tick(after=60)
        self.assertTrue(self.ui.idle_active)

    def test_connecting_to_an_already_sleeping_device_restores_a_fresh_frame(self):
        self.device.health["ui"].update(idle=True, wake=37)
        self.tick()
        self.assertFalse(self.ui.idle_active)
        self.assertEqual(self.device.calls[0], ("ui.idle", {"enabled": False}))
        self.assertEqual(self.ui.presented["kind"], "home")

    def test_expired_lease_restores_unchanged_paused_session_without_waiting_for_refresh(self):
        self.store.configure({"threadId": "one", "enabled": False})
        self.tick()
        old_token = self.ui.presented_token
        paints = len(self.device.paints)
        self.device.health["imageActive"] = False
        self.device.health["ui"]["frame"] = "00000000"
        self.event(5, old_token)
        self.tick(after=6)
        self.assertEqual(len(self.device.paints), paints + 1)
        self.assertNotEqual(self.ui.presented_token, old_token)
        self.assertFalse(self.device.paints[-1]["hold_to_talk"])
        self.assertEqual(self.store.get()["target"]["id"], "one")
        self.assertFalse(self.store.get()["enabled"])
        self.assertEqual(self.store.session.sent, [])

    def test_idle_timeout_accepts_zero_and_rejects_nonfinite_or_negative_values(self):
        import argparse
        self.assertEqual(idle_seconds("0"), 0)
        self.assertEqual(idle_seconds("90.5"), 90.5)
        for value in ("-1", "nan", "inf", "unlimited"):
            with self.assertRaises(argparse.ArgumentTypeError): idle_seconds(value)
        for value in (-1, float("nan"), float("inf"), True):
            with self.assertRaises(ValueError): DeviceUI(self.device, 1, lambda: None, idle_seconds=value)
