import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge import Handler, SceneStore, read_status
from functools import partial
from http.server import ThreadingHTTPServer


NOW = datetime(2026, 10, 4, 16, tzinfo=timezone.utc).timestamp()


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "state.sqlite"
        self.db = sqlite3.connect(self.database)
        self.db.executescript((Path(__file__).parent / "fixtures/t3.sql").read_text())
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.directory.cleanup()

    def status(self):
        return read_status(self.database, NOW)

    def test_completed_turn_beats_stale_running_session(self):
        self.assertEqual(self.status()["state"], "done")
        self.assertEqual(read_status(self.database, NOW + 600)["state"], "idle")

    def test_idle_sessions_keep_project_identity_without_a_turn(self):
        self.db.execute("DELETE FROM projection_turns")
        self.db.execute("DELETE FROM projection_thread_sessions")
        self.db.commit()
        thread = self.status()["threads"][0]
        self.assertEqual(thread["projectId"], "p")
        self.assertEqual(thread["project"], "Example")
        self.assertEqual(thread["state"], "idle")

    def test_pending_turn_without_id_beats_old_completion_and_reads_wal(self):
        self.db.execute("INSERT INTO projection_turns VALUES (2,'t',NULL,'pending','2026-10-04T16:00:00Z',NULL,NULL)")
        self.db.commit()
        status = self.status()
        self.assertEqual(status["state"], "working")
        self.assertIsNone(status["threads"][0]["turnId"])

    def test_checkpoint_completion_timestamp_does_not_end_running_turn(self):
        self.db.execute("UPDATE projection_turns SET state='running'")
        self.db.commit()
        self.assertEqual(self.status()["state"], "working")

    def test_pending_input_and_approval_take_priority(self):
        for column in ["pending_approval_count", "pending_user_input_count"]:
            self.db.execute(f"UPDATE projection_threads SET {column}=1")
            self.db.commit()
            self.assertEqual(self.status()["state"], "waiting")
            self.db.execute(f"UPDATE projection_threads SET {column}=0")

    def test_archived_or_deleted_threads_are_absent(self):
        self.db.execute("UPDATE projection_threads SET archived_at='2026-10-04T16:00:00Z'")
        self.db.commit()
        self.assertEqual(self.status()["threads"], [])
        self.db.execute("UPDATE projection_threads SET archived_at=NULL, deleted_at='2026-10-04T16:00:00Z'")
        self.db.commit()
        self.assertEqual(self.status()["threads"], [])

    def test_project_catalog_includes_empty_and_archived_only_projects(self):
        self.db.execute("UPDATE projection_threads SET archived_at='now'")
        self.db.execute("INSERT INTO projection_projects VALUES ('empty','Empty','/projects/empty',NULL)")
        self.db.execute("INSERT INTO projection_projects VALUES ('deleted','Deleted','/gone','now')")
        self.db.commit()
        status = self.status()
        self.assertEqual({p['id'] for p in status['projects']}, {'p', 'empty'})
        self.assertEqual(status['threads'], [])
        self.assertEqual(status['projects'][0]['path'], '/projects/empty')

    def test_deleted_project_cannot_supply_voice_destinations(self):
        self.db.execute("UPDATE projection_projects SET deleted_at='now'")
        self.db.commit()
        self.assertEqual(self.status()['projects'], [])
        self.assertEqual(self.status()['threads'], [])

    def test_missing_database_is_not_created(self):
        missing = self.database.with_name("missing.sqlite")
        self.assertFalse(read_status(missing)["connected"])
        self.assertFalse(missing.exists())

    def test_schema_change_is_explicitly_offline(self):
        self.db.execute("DROP TABLE projection_turns")
        self.db.commit()
        self.assertFalse(self.status()["connected"])
        self.assertEqual(self.status()["state"], "offline")

    def test_read_does_not_change_t3_rows(self):
        before = list(self.db.iterdump())
        self.status()
        self.assertEqual(list(self.db.iterdump()), before)


class DisplayTests(unittest.TestCase):
    def test_chart_rejects_nonfinite_and_oversized_content(self):
        store = SceneStore()
        for values in [[1, float("nan")], [False, 2], [1], [1] * 49, [1, float("inf")], [1, 10 ** 500]]:
            with self.assertRaises(ValueError):
                store.put({"title": "Chart", "values": values})
        self.assertIsNone(store.get())

    def test_image_must_be_embedded_raster_and_match_mime(self):
        store = SceneStore()
        for image in ["https://example.com/a.png", "data:image/svg+xml;base64,AA==", "data:image/png;base64," + base64.b64encode(b"not a png").decode()]:
            with self.assertRaises(ValueError):
                store.put({"title": "Image", "image": image})

    def test_expiry_and_clear_restore_ambient(self):
        store = SceneStore()
        store.put({"title": "Idea", "values": [1, 2, 3]})
        self.assertEqual(store.get()["kind"], "chart")
        store.current["expiresAt"] = 1
        self.assertIsNone(store.get())
        store.put({"title": "Next"})
        store.clear()
        self.assertIsNone(store.get())

    def test_http_boundary_and_scene_round_trip(self):
        store = SceneStore()
        with tempfile.TemporaryDirectory() as temp:
            server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, database=Path(temp) / "absent.sqlite", scenes=store))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}"
            try:
                body = json.dumps({"title":"Hello T3","values":[3,2,1]}).encode()
                request = Request(url+"/api/scene", data=body, headers={"Content-Type":"application/json"})
                with urlopen(request) as response:
                    self.assertEqual(json.load(response)["surface"], "simulator")
                with urlopen(url+"/api/status") as response:
                    status = json.load(response)
                self.assertEqual(status["overlay"]["title"], "Hello T3")
                self.assertIsNone(status["deviceConnected"])
                for headers in [{"Host":"evil.example"}, {"Origin":"https://evil.example"}]:
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(Request(url+"/api/status", headers=headers))
                    self.assertEqual(raised.exception.code,403)
                    raised.exception.close()
                with urlopen(Request(url+"/api/scene", method="DELETE")) as response:
                    self.assertTrue(json.load(response)["ok"])
                self.assertIsNone(store.get())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()


class CurrentSessionHTTPTests(unittest.TestCase):
    def test_selection_endpoint_is_read_only_origin_protected_and_sanitizes_failures(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        session = Mock()
        session.current_threads.return_value = [dict(thread_id='chosen', project_id='p', title='Chosen')]
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler,
            database=Path('/unused'), scenes=SceneStore(), listen=SimpleNamespace(session=session)))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = f'http://127.0.0.1:{server.server_port}/api/current-session'
        try:
            with urlopen(url) as response:
                self.assertEqual(json.load(response), dict(threadId='chosen', projectId='p', title='Chosen'))
            session.send.assert_not_called()
            with self.assertRaises(HTTPError) as denied:
                urlopen(Request(url, headers={'Origin': 'https://unrelated.example'}))
            self.assertEqual(denied.exception.code, 403)
            session.current_threads.assert_called_once()
            session.current_threads.return_value.append(dict(thread_id='voice-other', project_id='remote',
                title='Other', projectTitle='Project', environment='Spark', browser=True, new=True,
                _key=('private-client', 'private-selection')))
            with urlopen(url) as response:
                choices = json.load(response)['choices']
            self.assertEqual(len(choices), 2)
            self.assertEqual(choices[1], dict(threadId='voice-other', projectId='remote', title='Other',
                project='Project', environment='Spark', browser=True, new=True))
            for error, code, expected in [(ValueError('Focus T3'), 409, 'Focus T3'),
                    (RuntimeError('private transport details'), 503, "Cannot reach T3's active window. Reconnect T3 and try again.")]:
                session.current_threads.side_effect = error
                with self.assertRaises(HTTPError) as failed:
                    urlopen(url)
                self.assertEqual(failed.exception.code, code)
                self.assertEqual(json.load(failed.exception)['error'], expected)
        finally:
            server.shutdown(); server.server_close(); worker.join()
