import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from t3_session import T3Session, DispatchRejected


class Socket:
    def __init__(self, result): self.result, self.requests, self.closed = result, [], False
    def send(self, request): self.requests.append(json.loads(request))
    def recv(self): return json.dumps({"_tag": "Exit", "requestId": "1", "exit": self.result})
    def close(self): self.closed = True


class SessionTests(unittest.TestCase):
    def test_rpc_advertises_current_protocol_and_keeps_credentials_out_of_url(self):
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / 'token'
            token.write_text('test-only-token')
            socket = Socket({'_tag': 'Success', 'value': {'matches': []}})
            connect = Mock(return_value=socket)
            with patch.dict(sys.modules, websocket=SimpleNamespace(create_connection=connect)):
                T3Session(Path(directory) / 'absent', token=token).probe()
            self.assertEqual(connect.call_args.args[0], 'ws://127.0.0.1:3773/ws?orchestrationProtocol=2')
            self.assertEqual(connect.call_args.kwargs['header'], {'Authorization': 'Bearer test-only-token'})
            self.assertTrue(socket.closed)

    def test_project_only_drafts_use_defaults_or_archived_history_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.sqlite'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE projection_threads(thread_id,project_id,title,runtime_mode,interaction_mode,model_selection_json,deleted_at,archived_at)')
                db.execute('CREATE TABLE projection_projects(project_id,title,workspace_root,default_model_selection_json,default_thread_env_mode,deleted_at)')
                db.execute('INSERT INTO projection_projects VALUES (?,?,?,?,?,NULL)',
                           ('p', 'Empty project', directory, '{"model":"project-model"}', None))
                db.commit()
                session = T3Session(path)
                before = list(db.iterdump())
                draft = session.draft(project_id='p')
                self.assertEqual(draft['project_id'], 'p')
                self.assertEqual(draft['runtime_mode'], 'approval-required')
                self.assertEqual(json.loads(draft['model_selection_json']), {'model': 'project-model'})
                self.assertEqual(list(db.iterdump()), before)
                db.execute('UPDATE projection_projects SET default_model_selection_json=NULL')
                db.commit()
                with self.assertRaisesRegex(ValueError, 'default model'):
                    session.draft(project_id='p')
                db.execute('INSERT INTO projection_threads VALUES (?,?,?,?,?,?,NULL,?)',
                           ('archived', 'p', 'Old session', 'approval-required', 'plan', '{"model":"old-model"}', 'now'))
                db.commit()
                draft = session.draft(project_id='p')
                self.assertNotEqual(draft['thread_id'], 'archived')
                self.assertEqual(draft['interaction_mode'], 'plan')
                self.assertEqual(json.loads(draft['model_selection_json']), {'model': 'old-model'})
                with patch.object(session, 'rpc', return_value={'sequence': 1}) as rpc:
                    session.send(draft['thread_id'], 'Start here', 'cmd', 'msg', draft=draft)
                self.assertEqual(rpc.call_args.args[1]['bootstrap']['createThread']['projectId'], 'p')
                db.execute("UPDATE projection_projects SET deleted_at='now'")
                db.commit()
                with self.assertRaisesRegex(ValueError, 'no longer available'):
                    session.draft(project_id='p')

    def test_new_session_bootstrap_uses_project_default_model_without_creating_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            dbpath = Path(directory) / "state.sqlite"
            with sqlite3.connect(dbpath) as db:
                db.execute("CREATE TABLE projection_threads(thread_id,project_id,title,runtime_mode,interaction_mode,model_selection_json,deleted_at,archived_at)")
                db.execute("INSERT INTO projection_threads VALUES ('one','project','A session','approval-required','default','{\"model\":\"old\"}',NULL,NULL)")
                db.execute("CREATE TABLE projection_projects(project_id,title,workspace_root,default_model_selection_json,default_thread_env_mode,deleted_at)")
                db.execute("INSERT INTO projection_projects VALUES ('project','Project',?,'{\"model\":\"project-default\"}',NULL,NULL)", (directory,))
                db.commit()
                before = list(db.iterdump())
                session = T3Session(dbpath)
                draft = session.draft("one")
                self.assertNotEqual(draft["thread_id"], "one")
                with patch.object(session, "rpc", return_value={"sequence": 7}) as rpc:
                    session.send(draft["thread_id"], "Build a dashboard", "cmd", "message", draft=draft)
                    command = rpc.call_args.args[1]
                bootstrap = command["bootstrap"]["createThread"]
                self.assertEqual(bootstrap["projectId"], "project")
                self.assertEqual(bootstrap["modelSelection"], {"model": "project-default"})
                self.assertEqual(bootstrap["runtimeMode"], "approval-required")
                self.assertEqual(command["message"]["text"], "Build a dashboard")
                self.assertEqual(list(db.iterdump()), before)

    def test_authenticated_rpc_preserves_session_modes_and_database(self):
        with tempfile.TemporaryDirectory() as directory:
            dbpath = Path(directory) / "state.sqlite"
            token = Path(directory) / "token"
            token.write_text("test-only-token")
            with sqlite3.connect(dbpath) as db:
                db.execute("CREATE TABLE projection_threads(thread_id,project_id,title,runtime_mode,interaction_mode,model_selection_json,deleted_at,archived_at)")
                db.execute("INSERT INTO projection_threads VALUES ('one','project','A session','approval-required','plan','{\"model\":\"existing\"}',NULL,NULL)")
                db.commit()
                before = list(db.iterdump())
                socket = Socket({"_tag": "Success", "value": {"sequence": 10}})
                module = SimpleNamespace(create_connection=lambda *args, **kwargs: socket)
                with patch.dict(sys.modules, websocket=module):
                    session = T3Session(dbpath, token=token)
                    self.assertEqual(session.send("one", "Reviewed words", "cmd", "message"), 10)
                self.assertTrue(socket.closed)
                command = socket.requests[0]["payload"]
                self.assertEqual(command["runtimeMode"], "approval-required")
                self.assertEqual(command["interactionMode"], "plan")
                self.assertEqual(command["modelSelection"], {"model": "existing"})
                self.assertEqual(command["message"]["text"], "Reviewed words")
                self.assertEqual(list(db.iterdump()), before)
                db.execute("UPDATE projection_threads SET archived_at='now'")
                db.commit()
                with self.assertRaises(ValueError): session.describe("one")

    def test_server_rejection_closes_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / "token"
            token.write_text("test-only-token")
            socket = Socket({"_tag": "Failure"})
            with patch.dict(sys.modules, websocket=SimpleNamespace(create_connection=lambda *a, **k: socket)):
                with self.assertRaises(DispatchRejected):
                    T3Session(Path(directory) / "absent", token=token).probe()
            self.assertTrue(socket.closed)
