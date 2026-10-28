#!/usr/bin/env python3
"""Independent T3 companion prototype. Reads T3; serves a round-screen simulator.

No Harness dependencies, device commands, or writes to T3's database.
Python standard library only. Run `python3 bridge.py --help`.
"""

import argparse
import base64
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from speech import SpeechStore, bridge_request
from listen import ListenStore
from t3_session import T3Session, DEFAULT_TOKEN, database_version, default_database, thread_query


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = default_database()
SCENES = {"shark", "orbit", "aurora"}
PRIORITY = {"waiting": 0, "error": 1, "working": 2, "done": 3, "idle": 4}


def demo_status():
    """A synthetic workbench that never opens T3's database or credentials."""
    examples = [
        ("field-notes", "Field notes", "A quieter kind of interface", "waiting"),
        ("shark-harness", "Shark harness", "Give the next idea a physical form", "working"),
        ("signal-lab", "Signal lab", "The little details, dialed in", "done"),
    ]
    threads = [{"id": f"demo-{i}", "projectId": project, "project": name,
                "title": title, "state": state, "turnId": None, "elapsedSeconds": 0,
                "approvals": int(state == "waiting"), "questions": 0}
               for i, (project, name, title, state) in enumerate(examples)]
    return {"version": 1, "source": "demo", "demo": True, "connected": False,
            "generatedAt": datetime.now(timezone.utc).isoformat(), "state": "working",
            "deviceConnected": False, "surface": "simulator", "threads": threads,
            "projects": [{"id": p, "title": n, "path": ""} for p, n, _, _ in examples],
            "counts": dict(Counter(t["state"] for t in threads))}


def seconds_since(value, now):
    if not value:
        return None
    try:
        return max(0, now - datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError):
        return None


def thread_state(row, now):
    if row["pending_approval_count"] or row["pending_user_input_count"] or row["turn_state"] == "waiting":
        return "waiting"
    # T3 can record completed_at while a turn is still running (checkpoint work).
    # The actual turn state is authoritative; session status can outlive the turn.
    if row["turn_state"] in {"running", "pending", "preparing", "starting", "queued"}:
        return "working"
    if row["turn_state"] in {"failed", "error"} or row["session_status"] == "error":
        return "error"
    if row["turn_state"] == "completed":
        age = seconds_since(row["completed_at"], now)
        return "done" if age is not None and age < 300 else "idle"
    if row["turn_state"] is None and row["session_status"] == "running":
        return "working"
    return "idle"


def read_status(database, now=None):
    """Read a consistent snapshot, including WAL data, without creating a database."""
    now = time.time() if now is None else now
    database = Path(database).expanduser().resolve()
    result = {
        "version": 1, "source": "t3-local", "connected": False,
        "generatedAt": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "state": "offline", "projects": [], "threads": [], "counts": {},
        "deviceConnected": None, "surface": "simulator",
    }
    if not database.is_file():
        return {**result, "message": "T3 state is unavailable. Start T3 on this machine."}
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only = ON")
            db.execute("BEGIN")
            projects = db.execute("""SELECT project_id, title, workspace_root
                FROM projection_projects WHERE deleted_at IS NULL
                ORDER BY title COLLATE NOCASE, project_id""").fetchall()
            turns = "SELECT * FROM projection_turns"
            session_status = "s.status"
            sessions = "LEFT JOIN projection_thread_sessions s ON s.thread_id = t.thread_id"
            if database_version(db) == 2:
                turns = """SELECT ordinal AS row_id, thread_id, run_id AS turn_id,
                    status AS state, requested_at, completed_at,
                    json_extract(payload_json, '$.startedAt') AS started_at
                    FROM orchestration_v2_projection_runs"""
                session_status, sessions = "NULL", ""
            # Select the newest REQUEST, including pending turns without a turn_id.
            # Joining only latest_turn_id can accidentally show the prior completion.
            rows = db.execute(f"""
                SELECT t.thread_id, t.project_id, t.title, t.updated_at,
                       t.pending_approval_count, t.pending_user_input_count,
                       {session_status} AS session_status,
                       p.title AS project,
                       u.turn_id, u.state AS turn_state, u.started_at,
                       u.requested_at, u.completed_at
                  FROM ({thread_query(db)}) t
                  LEFT JOIN projection_projects p ON p.project_id = t.project_id
                  {sessions}
                  LEFT JOIN ({turns}) u ON u.thread_id = t.thread_id AND u.row_id = (
                       SELECT row_id FROM ({turns})
                        WHERE thread_id = t.thread_id
                        ORDER BY requested_at DESC, row_id DESC LIMIT 1
                  )
                 WHERE t.deleted_at IS NULL AND t.archived_at IS NULL AND p.deleted_at IS NULL
                 ORDER BY t.updated_at DESC
            """).fetchall()
    except sqlite3.Error:
        return {**result, "message": "T3 state could not be read. Its database may be busy or its schema changed."}
    threads = []
    for row in rows:
        # A new, idle thread is still a valid destination for a spoken prompt.
        state = thread_state(row, now)
        threads.append({
            "id": row["thread_id"], "title": row["title"][:160],
            "project": (row["project"] or "T3")[:80], "projectId": row["project_id"], "state": state,
            "turnId": row["turn_id"],
            "elapsedSeconds": int(seconds_since(row["started_at"] or row["requested_at"], now) or 0),
            "approvals": row["pending_approval_count"], "questions": row["pending_user_input_count"],
        })
    threads.sort(key=lambda thread: PRIORITY[thread["state"]])
    counts = dict(Counter(thread["state"] for thread in threads))
    return {**result, "connected": True, "state": threads[0]["state"] if threads else "idle",
            "projects": [{"id": p["project_id"], "title": p["title"], "path": p["workspace_root"]} for p in projects],
            "threads": threads, "counts": counts}


class SceneStore:
    """Transient on-demand display content. Nothing is written to disk or to T3."""

    def __init__(self):
        self.lock = threading.Lock()
        self.current = None

    def put(self, value):
        if not isinstance(value, dict) or set(value) - {"title", "detail", "scene", "seconds", "image", "values"}:
            raise ValueError("Expected title, optional detail, scene, seconds, image, and values.")
        title, detail = value.get("title", ""), value.get("detail", "")
        scene, seconds = value.get("scene", "aurora"), value.get("seconds", 20)
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 60:
            raise ValueError("Title must be 1–60 characters.")
        if not isinstance(detail, str) or len(detail) > 100:
            raise ValueError("Detail must be at most 100 characters.")
        if not isinstance(scene, str) or scene not in SCENES:
            raise ValueError("Scene must be shark, orbit, or aurora.")
        if type(seconds) not in {int, float} or not 1 <= seconds <= 600:
            raise ValueError("Duration must be between 1 and 600 seconds.")
        overlay = {"title": title.strip(), "detail": detail.strip(), "scene": scene,
                   "expiresAt": int((time.time() + seconds) * 1000), "kind": "message"}
        if "image" in value and "values" in value:
            raise ValueError("Choose either an image or chart values.")
        if "image" in value:
            image = value["image"]
            if not isinstance(image, str) or len(image) > 350000:
                raise ValueError("Image must be a PNG, JPEG, or WebP data URL under 256 KiB.")
            header, sep, encoded = image.partition(",")
            if not sep or header not in {"data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64"}:
                raise ValueError("Image must be a PNG, JPEG, or WebP data URL.")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except ValueError as error:
                raise ValueError("Image base64 is invalid.") from error
            valid = (header == "data:image/png;base64" and raw.startswith(b"\x89PNG\r\n\x1a\n") or
                     header == "data:image/jpeg;base64" and raw.startswith(b"\xff\xd8\xff") or
                     header == "data:image/webp;base64" and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP")
            if not valid or len(raw) > 262144:
                raise ValueError("Image content must match its type and be under 256 KiB.")
            overlay.update(kind="image", image=image)
        if "values" in value:
            values = value["values"]
            if (not isinstance(values, list) or not 2 <= len(values) <= 48 or
                any(type(v) not in {int, float} or abs(v) > 1e12 or not math.isfinite(v) for v in values)):
                raise ValueError("Chart needs 2–48 finite numbers between -1e12 and 1e12.")
            overlay.update(kind="chart", values=values)
        with self.lock:
            self.current = overlay
        return overlay

    def get(self):
        with self.lock:
            if self.current and self.current["expiresAt"] <= time.time() * 1000:
                self.current = None
            return self.current

    def clear(self):
        with self.lock:
            self.current = None


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, database, scenes, speech=None, listen=None, demo=False, **kwargs):
        self.database, self.scenes = database, scenes
        self.demo = demo
        self.speech = speech
        self.listen = listen
        super().__init__(*args, **kwargs)

    def reply(self, status, body, content_type="application/json"):
        data = json.dumps(body).encode() if content_type == "application/json" else body
        self.send_response(status)
        if getattr(self, "voice_origin", None):
            self.send_header("Access-Control-Allow-Origin", self.voice_origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'")
        try:
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Browser navigation/aborted polling is a normal disconnect.

    def local_request(self):
        # Keep other websites from reading local thread metadata via DNS rebinding.
        expected = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        host = self.headers.get("Host", "")
        if host not in expected:
            self.reply(403, {"error": "Use the local companion URL."})
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{h}" for h in expected}:
            self.reply(403, {"error": "Cross-origin access is disabled."})
            return False
        return True

    def voice_request(self):
        from voice_relay import configured_hosts, configured_origins
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if (len(self.headers.get_all("Origin", [])) != 1 or len(self.headers.get_all("Host", [])) != 1 or
                origin not in configured_origins() or host not in configured_hosts(self.server.server_port)):
            self.reply(403, {"error": "Browser voice origin is not allowed."})
            return False
        self.voice_origin = origin
        return True

    def do_OPTIONS(self):
        if urlsplit(self.path).path != "/api/voice-client":
            self.reply(404, {"error": "Not found"})
        elif self.voice_request():
            self.reply(200, {})

    def voice_exchange(self):
        if not self.voice_request(): return
        if self.listen is None:
            self.reply(503, {"error": "Voice companion is unavailable."}); return
        try:
            authorization = self.headers.get("Authorization", "")
            if not authorization.startswith("Bearer "):
                raise ValueError("Browser voice authentication is required.")
            client = self.listen.session.relay.authorize(authorization[7:])
        except ValueError as error:
            self.reply(401, {"error": str(error)}); return
        except Exception:
            self.reply(503, {"error": "T3 authentication is unavailable."}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 16384:
                raise ValueError("Invalid browser voice request size.")
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("Send application/json.")
            self.connection.settimeout(3)
            value = json.loads(self.rfile.read(length))
            self.reply(200, self.listen.session.relay.exchange(client, value))
        except (ValueError, UnicodeDecodeError):
            self.reply(400, {"error": "Invalid browser voice request."})
        except TimeoutError:
            self.reply(408, {"error": "Request timed out."})

    def do_GET(self):
        if not self.local_request():
            return
        path = urlsplit(self.path).path
        if self.demo and path == "/api/current-session":
            self.reply(409, {"error": "Demo mode does not access T3 sessions."})
            return
        if self.demo and path == "/api/listen":
            self.reply(200, {"enabled": False, "autoSend": False, "target": None,
                            "job": None, "demo": True, "destinationReady": False,
                            "device": {"connected": False, "ready": False}})
            return
        if self.demo and path == "/api/speech":
            self.reply(200, {"job": None, "volume": 60, "demo": True})
            return
        if path == "/api/listen" and self.listen is not None:
            self.reply(200, self.listen.get())
            return
        if path == "/api/current-session" and self.listen is not None:
            try:
                choices = [{"threadId": row["thread_id"], "projectId": row["project_id"],
                            "title": row["title"], **({"browser": True,
                            "project": row["projectTitle"], "environment": row["environment"],
                            "new": row["new"]} if row.get("browser") else {})}
                           for row in self.listen.session.current_threads()]
                self.reply(200, choices[0] if len(choices) == 1 else {"choices": choices})
            except ValueError as error:
                self.reply(409, {"error": str(error)})
            except Exception:
                self.reply(503, {"error": "Cannot reach T3's active window. Reconnect T3 and try again."})
            return
        if path == "/api/speech" and self.speech is not None:
            self.reply(200, self.speech.get())
            return
        if path == "/api/status":
            status = demo_status() if self.demo else read_status(self.database)
            self.reply(200, {**status, "overlay": self.scenes.get()})
            return
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/styles.css": ("styles.css", "text/css; charset=utf-8"),
                  "/icon.svg": ("../assets/icon.svg", "image/svg+xml")}
        if path not in assets:
            self.reply(404, {"error": "Not found"})
            return
        filename, content_type = assets[path]
        self.reply(200, (ROOT / "web" / filename).read_bytes(), content_type)

    def do_POST(self):
        if self.demo and urlsplit(self.path).path != "/api/scene":
            if self.local_request():
                self.reply(409, {"error": "Demo mode only supports browser visuals. Start the bridge without --demo for voice."})
            return
        if urlsplit(self.path).path == "/api/voice-client":
            self.voice_exchange(); return
        if not self.local_request():
            return
        path = urlsplit(self.path).path
        speech_routes = {} if self.speech is None else {
            "/api/speech": self.speech.put, "/api/speech/volume": self.speech.set_volume,
            "/api/speech/claim": self.speech.claim, "/api/speech/progress": self.speech.update,
        }
        if self.listen is not None:
            speech_routes.update({"/api/listen/configure": self.listen.configure,
                "/api/listen/control": self.listen.control, "/api/listen/device": self.listen.hardware,
                "/api/listen/begin": self.listen.begin, "/api/listen/progress": self.listen.progress,
                "/api/listen/new": self.listen.new_session,
                "/api/listen/confirm": self.listen.confirm})
        if path != "/api/scene" and path not in speech_routes:
            self.reply(404, {"error": "Not found"})
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.reply(415, {"error": "Send application/json."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            limit = 355000 if path == "/api/scene" else (131072 if path.startswith("/api/listen") else 4096)
            if not 1 <= length <= limit:
                raise ValueError(f"Request body must be 1–{limit} bytes.")
            self.connection.settimeout(3)
            value = json.loads(self.rfile.read(length))
            if path in speech_routes:
                self.reply(200, speech_routes[path](value))
            else:
                self.reply(200, {"ok": True, "surface": "simulator", "overlay": self.scenes.put(value)})
        except (ValueError, UnicodeDecodeError) as error:
            self.reply(400, {"error": str(error)})
        except TimeoutError:
            self.reply(408, {"error": "Request timed out."})

    def do_DELETE(self):
        if not self.local_request():
            return
        path = urlsplit(self.path).path
        if self.demo and path != "/api/scene":
            self.reply(409, {"error": "Voice is disabled in demo mode."})
            return
        if path == "/api/listen" and self.listen is not None:
            try:
                self.reply(200, self.listen.discard())
            except ValueError as error:
                self.reply(409, {"error": str(error)})
            return
        if path == "/api/speech" and self.speech is not None:
            self.reply(200, self.speech.stop())
            return
        if path != "/api/scene":
            self.reply(404, {"error": "Not found"})
            return
        self.scenes.clear()
        self.reply(200, {"ok": True})

    def log_message(self, fmt, *args):
        # Polling is intentionally quiet, and display content is never logged.
        if len(args) < 2 or str(args[1]) not in {"200", "304"}:
            super().log_message(fmt, *args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--port", type=int, default=8874)
    parser.add_argument("--snapshot", action="store_true", help="Print current T3 status and exit.")
    parser.add_argument("--demo", action="store_true", help="Explore with sample sessions; no T3 access, USB, or voice actions.")
    parser.add_argument("--thread", help="Initial T3 session for reviewed voice prompts.")
    parser.add_argument("--stt", action="store_true", help="Arm push-to-talk for --thread on startup.")
    parser.add_argument("--t3-port", type=int, default=3773)
    parser.add_argument("--t3-token", type=Path, default=DEFAULT_TOKEN)
    sub = parser.add_subparsers(dest="command")
    scene = sub.add_parser("scene", help="Put a temporary message on the running simulator.")
    scene.add_argument("title")
    scene.add_argument("--detail", default="")
    scene.add_argument("--scene", choices=sorted(SCENES), default="aurora")
    scene.add_argument("--seconds", type=float, default=20)
    content = scene.add_mutually_exclusive_group()
    content.add_argument("--image", type=Path, help="PNG/JPEG/WebP, at most 256 KiB. Square images work best.")
    content.add_argument("--values", help="Comma-separated chart values.")
    sub.add_parser("clear", help="Return the simulator to its ambient screen.")
    say = sub.add_parser("say", help="Speak a local message through the running USB sender.")
    say.add_argument("text")
    say.add_argument("--volume", type=int, help="0–80; keeps the current volume when omitted.")
    say.add_argument("--wait", action="store_true", help="Wait for playback completion or an error.")
    say.add_argument("--on-tap", action="store_true", help="Arm once: the next display tap speaks (expires in one hour).")
    sub.add_parser("stop", help="Cancel speech, including synthesis or upload.")
    sub.add_parser("speech-status", help="Show the current speech request and volume.")
    volume = sub.add_parser("volume", help="Set speaker volume (0 mutes, maximum 80).")
    volume.add_argument("level", type=int)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    if args.demo and (args.command or args.thread or args.stt):
        parser.error("--demo starts an isolated preview; it cannot send commands or arm a voice destination.")
    if args.snapshot:
        print(json.dumps(demo_status() if args.demo else read_status(args.database), indent=2))
        return
    if args.command in {"say", "stop", "volume", "speech-status"}:
        try:
            if args.command == "say":
                value = {"text": args.text, "onTap": args.on_tap}
                if args.volume is not None:
                    value["volume"] = args.volume
                SpeechStore().put(value)
                result = bridge_request(args.port, value=value)
                request_id = result["job"]["id"]
                if args.wait or args.on_tap:
                    while result["job"] and result["job"]["id"] == request_id:
                        state = result["job"]["state"]
                        if state in {"done", "stopped", "error"}:
                            break
                        if args.on_tap and not args.wait and state in {"armed", "playing"}:
                            break
                        time.sleep(0.25)
                        result = bridge_request(args.port)
                    job = result["job"]
                    if not job or job["id"] != request_id:
                        raise ValueError("Speech was replaced by another request.")
                    if job["state"] in {"error", "stopped"}:
                        raise ValueError(job.get("error") or "Speech was stopped.")
                print("Speech completed." if args.wait else
                      f"Speech {'armed for the next display tap' if args.on_tap else 'queued'}: {request_id}")
            elif args.command == "stop":
                bridge_request(args.port, method="DELETE")
                print("Speech stop requested.")
            elif args.command == "volume":
                SpeechStore().set_volume({"volume": args.level})
                bridge_request(args.port, "/api/speech/volume", {"volume": args.level})
                print(f"Speaker volume: {args.level}")
            else:
                print(json.dumps(bridge_request(args.port), indent=2))
        except (OSError, ValueError) as error:
            parser.exit(1, f"Speech: {error}\n")
        return
    if args.command in {"scene", "clear"}:
        try:
            if args.command == "clear":
                request = Request(f"http://127.0.0.1:{args.port}/api/scene", method="DELETE")
            else:
                value = {"title": args.title, "detail": args.detail, "scene": args.scene, "seconds": args.seconds}
                if args.image:
                    raw = args.image.read_bytes()
                    if len(raw) > 262144:
                        raise ValueError("Resize/compress the image to under 256 KiB first.")
                    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(args.image.suffix.lower())
                    if not mime:
                        raise ValueError("Use a PNG, JPEG, or WebP file.")
                    value["image"] = f"data:{mime};base64," + base64.b64encode(raw).decode()
                if args.values:
                    value["values"] = [float(v) for v in args.values.split(",")]
                SceneStore().put(value)
                request = Request(f"http://127.0.0.1:{args.port}/api/scene", data=json.dumps(value).encode(),
                                  headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=5) as response:
                response.read()
            print("Scene accepted. The USB sender forwards it when running." if args.command == "scene" else "Canvas returned to ambient.")
        except (OSError, ValueError) as error:
            parser.exit(1, f"Could not update simulator: {error}\n")
        return
    listen = None if args.demo else ListenStore(
        T3Session(args.database, args.t3_port, args.t3_token), args.thread, args.stt)
    server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                 partial(Handler, database=args.database, scenes=SceneStore(),
                                         speech=None if args.demo else SpeechStore(), listen=listen, demo=args.demo))
    mode = "demo · sample sessions, browser visuals only" if args.demo else "USB sender runs separately"
    print(f"Shark Harness: http://127.0.0.1:{args.port} ({mode})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
