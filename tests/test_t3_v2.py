from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from bridge import read_status
from t3_session import T3Session


NOW = datetime(2026, 10, 4, 16, tzinfo=timezone.utc).timestamp()


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / 'statev2.sqlite'
        self.db = sqlite3.connect(self.database)
        self.db.executescript((Path(__file__).parent / 'fixtures/t3_v2.sql').read_text())
        self.db.commit()
        self.session = T3Session(self.database)

    def tearDown(self):
        self.db.close()
        self.directory.cleanup()

    def test_current_database_takes_precedence_over_leftover_legacy_state(self):
        from t3_session import default_database
        userdata = self.database.parent
        legacy = userdata / 'state.sqlite'
        legacy.touch()
        self.assertEqual(default_database(userdata), self.database)
        self.assertTrue(read_status(default_database(userdata), NOW)['connected'])
        self.assertEqual(default_database(userdata / 'missing'), userdata / 'missing/state.sqlite')
        self.database.unlink()
        self.assertEqual(default_database(userdata), legacy)

    def test_latest_desktop_run_and_timestamps_drive_session_status(self):
        status = read_status(self.database, NOW + 10)
        self.assertTrue(status['connected'], status.get('message'))
        self.assertEqual(len(status['threads']), 1)
        thread = status['threads'][0]
        self.assertEqual(thread['title'], 'Current desktop session')
        self.assertEqual(thread['turnId'], 'current')
        self.assertEqual(thread['elapsedSeconds'], 10)
        self.assertEqual(thread['state'], 'working')
        self.db.execute("UPDATE orchestration_v2_projection_runs SET status='completed', completed_at='2026-10-04T16:00:00Z' WHERE run_id='current'")
        self.db.commit()
        self.assertEqual(read_status(self.database, NOW)['state'], 'done')
        self.assertEqual(read_status(self.database, NOW + 600)['state'], 'idle')

    def test_desktop_preparation_queue_and_waiting_are_visible(self):
        for state, expected in [('preparing', 'working'), ('starting', 'working'),
                                ('queued', 'working'), ('waiting', 'waiting'), ('failed', 'error')]:
            with self.subTest(state=state):
                self.db.execute("UPDATE orchestration_v2_projection_runs SET status=? WHERE run_id='current'", (state,))
                self.db.commit()
                self.assertEqual(read_status(self.database, NOW)['state'], expected)

    def test_current_approval_and_question_counts_come_from_pending_requests(self):
        for kind, field in [('command', 'approvals'), ('user_input', 'questions')]:
            with self.subTest(kind=kind):
                self.db.execute('INSERT INTO orchestration_v2_projection_runtime_requests VALUES (?,?,?,?)',
                                (kind, 't', kind, 'pending'))
                self.db.commit()
                status = read_status(self.database, NOW)
                self.assertEqual(status['state'], 'waiting')
                self.assertEqual(status['threads'][0][field], 1)
                self.db.execute('DELETE FROM orchestration_v2_projection_runtime_requests')
                self.db.commit()
                self.assertEqual(read_status(self.database, NOW)['state'], 'working')

    def test_archiving_current_session_does_not_restore_leftover_legacy_threads(self):
        self.db.execute("UPDATE orchestration_v2_projection_threads SET archived_at='now'")
        self.db.commit()
        self.assertEqual(read_status(self.database, NOW)['threads'], [])
        with self.assertRaises(ValueError):
            self.session.describe('t')

    def test_voice_uses_current_message_protocol_and_keeps_model_options_and_modes(self):
        before = list(self.db.iterdump())
        with patch.object(self.session, 'rpc', return_value={'sequence': 7}) as rpc:
            self.assertEqual(self.session.send('t', 'Spoken words', 'cmd', 'msg'), 7)
        method, command = rpc.call_args.args
        self.assertEqual(method, 'orchestration.dispatchCommand')
        self.assertEqual(command['type'], 'message.dispatch')
        self.assertEqual(command['text'], 'Spoken words')
        self.assertEqual(command['threadId'], 't')
        self.assertEqual(command['commandId'], 'cmd')
        self.assertEqual(command['messageId'], 'msg')
        self.assertEqual(command['createdBy'], 'user')
        self.assertEqual(command['deliveryIntent'], 'auto')
        self.assertEqual(command['modelSelection']['options'], [{'id': 'reasoningEffort', 'value': 'max'}])
        row = self.session.describe('t')
        self.assertEqual((row['runtime_mode'], row['interaction_mode']), ('approval-required', 'plan'))
        self.assertEqual(list(self.db.iterdump()), before)

    def test_desktop_draft_launches_in_selected_project_with_one_initial_message(self):
        before = list(self.db.iterdump())
        draft = self.session.draft('t')
        receipt = {'threadId': draft['thread_id']}
        with patch.object(self.session, 'rpc', return_value=receipt) as rpc:
            self.session.send(draft['thread_id'], 'Start here', 'cmd', 'msg', draft=draft)
        rpc.assert_called_once()
        method, command = rpc.call_args.args
        self.assertEqual(method, 'orchestration.launchThread')
        self.assertEqual(command['threadId'], draft['thread_id'])
        self.assertEqual(command['projectId'], 'p')
        self.assertEqual(command['workspaceStrategy'], {'type': 'root'})
        self.assertEqual(command['initialMessage'], {'messageId': 'msg', 'text': 'Start here', 'attachments': []})
        self.assertEqual(command['modelSelection']['model'], 'project-model')
        self.assertEqual(command['runtimeMode'], 'approval-required')
        self.assertEqual(command['interactionMode'], 'plan')
        self.assertEqual(list(self.db.iterdump()), before)

    def test_desktop_worktree_draft_preserves_the_chosen_base_branch(self):
        draft = dict(self.session.draft('t'), baseBranch='feature/base')
        with patch.object(self.session, 'rpc', return_value={'threadId': draft['thread_id']}) as rpc:
            self.session.send(draft['thread_id'], 'Start here', 'cmd', 'msg', draft=draft)
        strategy = rpc.call_args.args[1]['workspaceStrategy']
        self.assertEqual(strategy, {'type': 'worktree', 'baseRef': 'feature/base', 'startFromOrigin': False})

    def test_unrecognized_draft_acknowledgement_is_not_reported_as_sent(self):
        draft = self.session.draft('t')
        for receipt in ({'threadId': 'another-thread'}, None):
            with self.subTest(receipt=receipt), patch.object(self.session, 'rpc', return_value=receipt):
                with self.assertRaisesRegex(RuntimeError, 'acknowledgement'):
                    self.session.send(draft['thread_id'], 'Start here', 'cmd', 'msg', draft=draft)
