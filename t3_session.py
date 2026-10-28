"""Adapter to T3's legacy and current command APIs. SQLite is read-only metadata."""

from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
import subprocess

DEFAULT_TOKEN = Path.home() / ".local/share/shark-harness/t3-token"


def default_database(userdata=None):
    if userdata is None:
        userdata = Path(os.environ.get("T3CODE_HOME", Path.home() / ".t3")) / "userdata"
    userdata = Path(userdata)
    current = userdata / "statev2.sqlite"
    return current if current.is_file() else userdata / "state.sqlite"


def database_version(db):
    return 2 if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                           "AND name='orchestration_v2_projection_threads'").fetchone() else 1


def thread_query(db):
    """Normalize metadata from the active projection, ignoring migrated legacy rows."""
    if database_version(db) == 1:
        return "SELECT rowid AS source_order, * FROM projection_threads"
    return """SELECT rowid AS source_order, thread_id, project_id, title,
        runtime_mode, interaction_mode, updated_at, archived_at, deleted_at,
        json_extract(payload_json, '$.modelSelection') AS model_selection_json,
        (SELECT COUNT(*) FROM orchestration_v2_projection_runtime_requests r
         WHERE r.thread_id = t.thread_id AND r.status = 'pending'
         AND r.kind <> 'user_input') AS pending_approval_count,
        (SELECT COUNT(*) FROM orchestration_v2_projection_runtime_requests r
         WHERE r.thread_id = t.thread_id AND r.status = 'pending'
         AND r.kind = 'user_input') AS pending_user_input_count
        FROM orchestration_v2_projection_threads t"""


def sequence_acknowledgement(result):
    if not isinstance(result, dict) or type(result.get("sequence")) is not int:
        raise RuntimeError("T3 returned an unrecognized acknowledgement.")
    return result["sequence"]


class DispatchRejected(RuntimeError):
    pass


class T3Session:
    def __init__(self, database, port=3773, token=DEFAULT_TOKEN):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Invalid T3 port.")
        self.database, self.port, self.token = Path(database).resolve(), port, Path(token)
        self._environment_id, self._environment_checked_at = None, None
        from voice_relay import VoiceRelay
        self.relay = VoiceRelay(lambda: self.rpc("server.getBackgroundPolicy", {}, timeout=2))

    def describe(self, thread_id):
        if isinstance(thread_id, str) and thread_id.startswith("voice-"):
            row = self.relay.describe(thread_id)
            telemetry = self.browser_telemetry_thread(row)
            if telemetry:
                row["telemetryThreadId"] = telemetry
            return row
        if not isinstance(thread_id, str) or not 1 <= len(thread_id) <= 200:
            raise ValueError("Choose a T3 session.")
        try:
            with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as db:
                db.row_factory = sqlite3.Row
                row = db.execute(f"""SELECT thread_id, project_id, title, runtime_mode, interaction_mode,
                    model_selection_json FROM ({thread_query(db)}) WHERE thread_id=?
                    AND deleted_at IS NULL AND archived_at IS NULL""", (thread_id,)).fetchone()
                version = database_version(db)
            if row is None:
                raise ValueError("That T3 session is no longer available.")
            return {**dict(row), "protocolVersion": version}
        except sqlite3.Error as error:
            raise ValueError("Cannot read T3 session metadata.") from error

    def browser_telemetry_thread(self, row):
        """A browser route can use local state only in the verified same environment.

        This is display metadata. The authenticated browser route remains the
        prompt destination, including after navigation or a missing status read.
        """
        if row.get("new") or not row.get("nativeThreadId") or not row.get("environmentId"):
            return None
        try:
            with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as db:
                local = db.execute(f"SELECT thread_id FROM ({thread_query(db)}) "
                    "WHERE thread_id=? AND project_id=? AND deleted_at IS NULL AND archived_at IS NULL",
                    (row["nativeThreadId"], row.get("project_id"))).fetchone()
        except sqlite3.Error:
            return None
        if not local:
            return None
        now = time.monotonic()
        if self._environment_checked_at is None or now - self._environment_checked_at >= 60:
            self._environment_id, self._environment_checked_at = None, now
            try:
                config = self.rpc("server.getConfig", {}, timeout=2)
                identity = config.get("environment", {}).get("environmentId")
                if isinstance(identity, str):
                    self._environment_id = identity
            except Exception:
                # Optional telemetry failure must not disable a selected voice route.
                pass
        return local[0] if self._environment_id == row["environmentId"] else None

    def rpc(self, method, payload, timeout=8):
        # Never place the token in a URL, browser response, command argument or log.
        from websocket import create_connection
        key = self.token.read_text().strip()
        if not key or "\n" in key or "\r" in key:
            raise ValueError("T3 companion credentials are missing or invalid.")
        ws = create_connection(f"ws://127.0.0.1:{self.port}/ws?orchestrationProtocol=2", timeout=timeout,
                               suppress_origin=True, header={"Authorization": f"Bearer {key}"})
        try:
            ws.send(json.dumps({"_tag": "Request", "id": "1", "tag": method,
                                "payload": payload, "headers": []}))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                messages = json.loads(ws.recv())
                for message in messages if isinstance(messages, list) else [messages]:
                    if message.get("_tag") == "Ping":
                        ws.send('{"_tag":"Pong"}')
                    elif message.get("_tag") == "Exit" and str(message.get("requestId")) == "1":
                        result = message.get("exit", {})
                        if result.get("_tag") != "Success":
                            raise DispatchRejected("T3 rejected the request. Check the session in T3.")
                        return result.get("value")
            raise TimeoutError("T3 did not acknowledge the request.")
        finally:
            ws.close()

    def probe(self):
        return self.rpc("orchestration.searchThreads", {"query": "shark-voice-connection-check", "limit": 1})

    def current_threads(self):
        """Resolve explicit client selection; never infer it from thread activity.

        The client helper must publish one thread scope on the existing
        authenticated activity channel at least every two seconds. Other scopes
        (including VCS subscriptions for cached tabs) are not destinations.
        """
        snapshot = self.rpc("server.getBackgroundPolicy", {}, timeout=2)
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("leases"), list):
            raise ValueError("T3 did not report its active window. Reconnect the T3 helper.")

        def timestamp(value):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return parsed.timestamp() if parsed.tzinfo is not None else None
            except (AttributeError, TypeError, ValueError, OverflowError):
                return None

        now = timestamp(snapshot.get("updatedAt"))
        if now is None:
            raise ValueError("T3's active-window report is unavailable.")
        # A closed/reloaded window can retain its last focused lease for 45s.
        # Filter freshness BEFORE counting windows, and coalesce duplicate
        # leases from the same client using its newest (including blur) report.
        latest = {}
        for index, lease in enumerate(snapshot["leases"]):
            if not isinstance(lease, dict):
                continue
            updated = timestamp(lease.get("updatedAt"))
            if updated is None or not 0 <= now - updated <= 5:
                continue
            key = lease.get("clientId") or index
            if key not in latest or updated >= latest[key][0]:
                latest[key] = (updated, lease)
        foreground = []
        for _, lease in latest.values():
            expires = timestamp(lease.get("expiresAt"))
            if (lease.get("visible") is True and lease.get("focused") is True
                    and lease.get("appState") == "active" and expires is not None and expires > now):
                foreground.append(lease)
        if not foreground:
            raise ValueError("Open and focus a T3 conversation on your computer, then press BOOT again.")
        choices, errors = [], []
        for lease in foreground:
            try:
                if str(lease.get("clientId", "")).startswith("shark-voice:"):
                    row = self.relay.current(lease["clientId"])
                else:
                    scopes = lease.get("scopes")
                    ids = {scope["threadId"] for scope in scopes if isinstance(scope, dict)
                           and scope.get("type") == "thread" and isinstance(scope.get("threadId"), str)
                           and scope["threadId"]} if isinstance(scopes, list) else set()
                    if len(ids) != 1:
                        raise ValueError("T3 is not reporting its open conversation. Load the matching voice helper, restart T3, then press BOOT again.")
                    row = self.describe(next(iter(ids)))
                if not any(c["thread_id"] == row["thread_id"] for c in choices):
                    choices.append(row)
            except ValueError as error:
                errors.append(str(error))
        if not choices:
            raise ValueError(errors[0])
        return choices

    def current_thread(self):
        """Convenience for callers that require exactly one destination."""
        choices = self.current_threads()
        if len(choices) != 1:
            raise ValueError("Several T3 conversations are open. Choose one on the device.")
        return choices[0]

    def draft(self, source_thread_id=None, project_id=None):
        source = self.describe(source_thread_id) if source_thread_id else None
        if source:
            if project_id is not None and source["project_id"] != project_id:
                raise ValueError("The selected project changed. Choose the project again.")
            project_id = source["project_id"]
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("Choose a T3 project first.")
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            version = database_version(db)
            project = db.execute("""SELECT project_id, title, workspace_root,
                default_model_selection_json, default_thread_env_mode FROM projection_projects
                WHERE project_id=? AND deleted_at IS NULL""", (project_id,)).fetchone()
            if source is None:
                # Archived sessions can supply defaults without becoming destinations.
                row = db.execute(f"""SELECT runtime_mode, interaction_mode, model_selection_json
                    FROM ({thread_query(db)}) WHERE project_id=? AND deleted_at IS NULL
                    ORDER BY archived_at IS NULL DESC, source_order DESC LIMIT 1""", (project_id,)).fetchone()
                source = dict(row) if row else {"runtime_mode": "approval-required",
                    "interaction_mode": "default", "model_selection_json": None}
        if project is None:
            raise ValueError("The selected project is no longer available.")
        model = project["default_model_selection_json"] or source["model_selection_json"]
        if not model:
            raise ValueError("Choose a default model for this project in T3, then try New session again.")
        result = {**source, "project_id": project_id, "thread_id": str(uuid.uuid4()), "title": "New voice session",
                  "projectTitle": project["title"], "model_selection_json": model,
                  "new": True, "projectCwd": project["workspace_root"], "protocolVersion": version}
        if project["default_thread_env_mode"] == "worktree":
            branch = subprocess.run(["git", "symbolic-ref", "--short", "HEAD"],
                                    cwd=project["workspace_root"], capture_output=True, text=True, timeout=5)
            if branch.returncode or not branch.stdout.strip():
                raise ValueError("Select a project with a branch before creating a worktree session.")
            result["baseBranch"] = branch.stdout.strip()
        return result

    def send(self, thread_id, text, command_id, message_id, draft=None):
        row = draft or self.describe(thread_id)
        if row.get("browser"):
            return self.relay.send(thread_id, text, command_id, message_id)
        if draft and draft["thread_id"] != thread_id:
            raise ValueError("The draft destination changed.")
        if row.get("protocolVersion") == 2:
            return self.send_v2(row, text, command_id, message_id, draft=draft)
        command = {
            "type": "thread.turn.start", "commandId": command_id, "threadId": thread_id,
            "message": {"messageId": message_id, "role": "user", "text": text, "attachments": []},
            "runtimeMode": row["runtime_mode"], "interactionMode": row["interaction_mode"],
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        if row["model_selection_json"]:
            command["modelSelection"] = json.loads(row["model_selection_json"])
        if draft:
            command["bootstrap"] = {"createThread": {
                "projectId": draft["project_id"], "title": text[:60],
                "modelSelection": command["modelSelection"], "runtimeMode": row["runtime_mode"],
                "interactionMode": row["interaction_mode"], "branch": None, "worktreePath": None,
                "createdAt": command["createdAt"],
            }}
            if draft.get("baseBranch"):
                command["bootstrap"]["prepareWorktree"] = {"projectCwd": draft["projectCwd"],
                    "baseBranch": draft["baseBranch"], "requireWorktree": True}
        return sequence_acknowledgement(self.rpc("orchestration.dispatchCommand", command))

    def send_v2(self, row, text, command_id, message_id, draft=None):
        model = json.loads(row["model_selection_json"]) if row["model_selection_json"] else None
        if draft:
            strategy = {"type": "root"}
            if draft.get("baseBranch"):
                strategy = {"type": "worktree", "baseRef": draft["baseBranch"], "startFromOrigin": False}
            result = self.rpc("orchestration.launchThread", {
                "commandId": command_id, "threadId": row["thread_id"],
                "projectId": row["project_id"], "title": text[:60],
                "modelSelection": model, "runtimeMode": row["runtime_mode"],
                "interactionMode": row["interaction_mode"], "creationSource": "server",
                "workspaceStrategy": strategy,
                "initialMessage": {"messageId": message_id, "text": text, "attachments": []},
            })
            if not isinstance(result, dict) or result.get("threadId") != row["thread_id"]:
                raise RuntimeError("T3 returned an unrecognized launch acknowledgement.")
            # Launch receipts acknowledge the thread ID, rather than an event sequence.
            return None
        command = {
            "type": "message.dispatch", "commandId": command_id, "threadId": row["thread_id"],
            "messageId": message_id, "text": text, "attachments": [],
            "createdBy": "user", "creationSource": "server",
            "dispatchMode": {"type": "start_immediately"}, "deliveryIntent": "auto",
        }
        if model:
            command["modelSelection"] = model
        return sequence_acknowledgement(self.rpc("orchestration.dispatchCommand", command))
