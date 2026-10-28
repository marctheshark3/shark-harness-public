"""Local browser destinations need verified environment identity for telemetry."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from t3_session import T3Session


class BrowserTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "statev2.sqlite"
        with sqlite3.connect(self.database) as db:
            db.executescript((Path(__file__).parent / "fixtures/t3_v2.sql").read_text())
        self.session = T3Session(self.database)
        self.row = dict(thread_id="voice-selection", nativeThreadId="t", project_id="p",
                        environmentId="local-environment", title="Selected session", browser=True)
        self.session.relay.describe = Mock(side_effect=lambda _: dict(self.row))
        self.session.rpc = Mock(return_value={"environment": {"environmentId": "local-environment"}})

    def tearDown(self):
        self.temp.cleanup()

    def test_matching_local_browser_keeps_routing_id_and_exposes_separate_state_id(self):
        row = self.session.describe("voice-selection")
        self.assertEqual(row["thread_id"], "voice-selection")
        self.assertEqual(row.get("telemetryThreadId"), "t")
        self.session.describe("voice-selection")
        self.session.rpc.assert_called_once_with("server.getConfig", {}, timeout=2)

    def test_remote_environment_never_borrows_a_matching_local_thread(self):
        self.row["environmentId"] = "remote-environment"
        self.assertIsNone(self.session.describe("voice-selection").get("telemetryThreadId"))

    def test_unknown_identity_or_project_does_not_disable_voice_routing(self):
        self.session.rpc.side_effect = TimeoutError("T3 unavailable")
        self.assertEqual(self.session.describe("voice-selection")["thread_id"], "voice-selection")
        self.assertIsNone(self.session.describe("voice-selection").get("telemetryThreadId"))
        self.row["project_id"] = "other-project"
        self.session.rpc.reset_mock()
        self.assertIsNone(self.session.describe("voice-selection").get("telemetryThreadId"))
        self.session.rpc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
