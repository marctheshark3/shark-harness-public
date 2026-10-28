"""Local device simulator: python -m simulator --port 8876."""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit

from simulator.core import SCENARIOS, Simulation

WEB = Path(__file__).resolve().parent / "web"
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/styles.css": ("styles.css", "text/css; charset=utf-8")}


class SimulatorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port=8876, *, scenario="projects", realtime=True):
        # Deliberately no host override: this dev tool has no remote auth model.
        super().__init__(("127.0.0.1", port), Handler)
        self.lock = threading.RLock()
        self.token = secrets.token_urlsafe(32)
        self.simulation = Simulation(scenario)
        self.realtime = realtime
        self.last_poll = time.monotonic()

    def sync(self):
        now = time.monotonic()
        if self.realtime: self.simulation.advance(min(900, max(0, now - self.last_poll)))
        self.last_poll = now

    def state(self):
        return {**self.simulation.state(), "csrfToken": self.token}

    def server_close(self):
        super().server_close()
        if hasattr(self, "simulation"): self.simulation.close()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass  # Synthetic interactions are visible in the bounded UI trace.

    def send_bytes(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Permissions-Policy", "microphone=(), camera=(), geolocation=()")
        self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError): pass

    def json(self, code, value):
        self.send_bytes(code, json.dumps(value, allow_nan=False).encode(), "application/json; charset=utf-8")

    def allowed(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host")
        origin = self.headers.get("Origin")
        if (len(self.headers.get_all("Host", [])) != 1 or len(self.headers.get_all("Origin", [])) > 1
                or host not in hosts or (origin is not None and origin != f"http://{host}")):
            self.close_connection = True
            self.json(403, {"error": "Use the simulator's local origin."})
            return False
        return True

    def do_GET(self):
        if not self.allowed(): return
        parsed = urlsplit(self.path)
        if parsed.path == "/favicon.ico":
            self.send_bytes(204, b"", "image/x-icon"); return
        if parsed.path in ASSETS:
            name, content_type = ASSETS[parsed.path]
            self.send_bytes(200, (WEB / name).read_bytes(), content_type)
            return
        if parsed.path not in {"/api/state", "/frame.png"}:
            self.json(404, {"error": "Unknown simulator resource."}); return
        with self.server.lock:
            self.server.sync()
            if parsed.path == "/api/state": self.json(200, self.server.state()); return
            version = parse_qs(parsed.query).get("v", [None])[0]
            if version is not None and version != str(self.server.simulation.display.frame_version):
                self.json(409, {"error": "The displayed frame changed. Refresh the simulator state."}); return
            self.send_bytes(200, self.server.simulation.frame_png(), "image/png")

    def do_POST(self):
        if not self.allowed(): return
        if self.path != "/api/action":
            self.close_connection = True
            self.json(404, {"error": "Unknown simulator action route."}); return
        supplied = self.headers.get("X-Simulator-Token", "")
        if len(self.headers.get_all("X-Simulator-Token", [])) != 1 or not hmac.compare_digest(supplied, self.server.token):
            self.close_connection = True
            self.json(403, {"error": "Reload the simulator before sending controls."}); return
        length = self.headers.get("Content-Length", "")
        if (len(self.headers.get_all("Content-Length", [])) != 1 or not length.isdigit()
                or not 0 < int(length) <= 8192 or self.headers.get("Transfer-Encoding") is not None):
            self.close_connection = True
            self.json(413, {"error": "Expected a JSON action of at most 8 KiB."}); return
        if self.headers.get_content_type() != "application/json":
            self.close_connection = True
            self.json(415, {"error": "Use application/json."}); return
        self.connection.settimeout(5)
        try:
            value = json.loads(self.rfile.read(int(length)), parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
            if not isinstance(value, dict): raise ValueError("Expected an action object.")
            with self.server.lock:
                self.server.sync()
                physical_start = value.get("type") in {"tap", "hold", "swipe", "boot"} or (
                    value.get("type") == "pointer" and value.get("phase") == "down") or (
                    value.get("type") == "key" and value.get("pressed") is True)
                if physical_start and (type(value.get("frameVersion")) is not int
                        or value["frameVersion"] != self.server.simulation.display.frame_version):
                    self.json(409, {"error": "The screen changed. Wait for its new image, then press again."})
                    return
                self.server.simulation.action(value)
                self.server.last_poll = time.monotonic()
                self.json(200, self.server.state())
        except (ValueError, TypeError, UnicodeError) as error:
            self.json(400, {"error": str(error)})
        except (TimeoutError, ConnectionError):
            self.close_connection = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8876)
    parser.add_argument("--scenario", choices=[id for id, _ in SCENARIOS],
                        default="projects")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535: parser.error("port must be 1–65535")
    try:
        with SimulatorServer(args.port, scenario=args.scenario) as server:
            print(f"Device simulator: http://127.0.0.1:{server.server_port} · synthetic data only", flush=True)
            server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    except (OSError, RuntimeError) as error:
        parser.exit(1, f"Simulator: {error}\n")


if __name__ == "__main__": main()
