"""Lightweight HTTP server for the Plex Compress Web UI.

Uses only stdlib (ThreadingHTTPServer) with Server-Sent Events for
real-time updates and a clean REST API.
"""

import json
import mimetypes
import os
import queue
import re
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

from plex_compress.intelligence import generate_report
from plex_compress.state import StateDB

from .config_store import ConfigStore
from plex_compress.webui.extensions import ExtensionManager
from plex_compress.webui.runner import JobRunner


STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765


class SSEQueue:
    """Per-client event queue for Server-Sent Events."""

    def __init__(self, maxlen: int = 100):
        self._q: queue.Queue = queue.Queue(maxsize=maxlen)

    def put(self, data: Dict[str, Any]):
        try:
            self._q.put_nowait(data)
        except queue.Full:
            # Drop oldest event
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(data)
            except queue.Full:
                pass

    def get(self, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None


class WebUIApp:
    """Application context shared across handlers, runner, and extensions."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self.config_store = ConfigStore()
        self.runner = JobRunner(self)
        self.event_queues: List[SSEQueue] = []
        self.event_lock = __import__("threading").Lock()
        self.extensions = ExtensionManager(self)
        self._routes: List[Tuple[str, str, Callable]] = []
        self._event_listeners: Dict[str, List[Callable]] = {}
        self._build_routes()

    # ------------------------------------------------------------------
    # Route / event extensibility
    # ------------------------------------------------------------------

    def add_route(self, method: str, path: str, handler: Callable):
        """Extensions can register additional routes."""
        self._routes.append((method.upper(), path, handler))

    def add_event_listener(self, event_type: str, callback: Callable):
        self._event_listeners.setdefault(event_type, []).append(callback)

    def publish_event(self, event_type: str, data: Dict[str, Any]):
        payload = {"type": event_type, "data": data, "time": time.time()}
        # Notify extension listeners
        for cb in self._event_listeners.get(event_type, []):
            try:
                cb(payload)
            except Exception:
                pass
        # Broadcast to SSE queues
        with self.event_lock:
            dead = []
            for q in self.event_queues:
                q.put(payload)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _state_db(self) -> StateDB:
        return StateDB(self.config_store.get("state_db_path"))

    def _build_routes(self):
        self._routes = [
            ("GET", "/", self._serve_index),
            ("GET", "/api/config", self._api_get_config),
            ("POST", "/api/config", self._api_post_config),
            ("GET", "/api/status", self._api_status),
            ("GET", "/api/queue", self._api_queue),
            ("GET", "/api/recent", self._api_recent),
            ("GET", "/api/failed", self._api_failed),
            ("GET", "/api/report", self._api_report),
            ("GET", "/api/logs", self._api_logs),
            ("GET", "/api/events", self._api_events),
            ("POST", "/api/health-check", self._api_health_check),
            ("POST", "/api/scan", self._api_scan),
            ("POST", "/api/transcode", self._api_transcode),
            ("POST", "/api/watch", self._api_watch),
            ("POST", "/api/stop", self._api_stop),
            ("POST", "/api/reset-failed", self._api_reset_failed),
            ("GET", "/api/extensions", self._api_extensions),
            ("GET", "/static/", self._serve_static),
        ]

    def _match_route(self, method: str, path: str) -> Tuple[Optional[Callable], Optional[re.Match]]:
        # Strip query string before matching
        path_stripped = path.split("?", 1)[0]
        for rm, rp, handler in self._routes:
            if rm != method:
                continue
            if rp == path_stripped:
                return handler, None
            # Support simple /static/<path> patterns
            if rp.startswith("/static/") and path_stripped.startswith("/static/"):
                return handler, re.match(r"/static/(.*)", path_stripped)
        return None, None

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _serve_index(self, handler, path, match):
        index_path = os.path.join(STATIC_DIR, "index.html")
        if os.path.exists(index_path):
            handler._send_file(index_path, "text/html")
        else:
            handler._send_json({"error": "Dashboard UI not found"}, 404)

    def _serve_static(self, handler, path, match):
        # Security: prevent path traversal
        rel = match.group(1)
        safe = os.path.normpath(rel)
        if safe.startswith("..") or safe.startswith("/"):
            handler._send_json({"error": "Invalid path"}, 403)
            return
        file_path = os.path.join(STATIC_DIR, safe)
        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            handler._send_json({"error": "Not found"}, 404)
            return
        ctype, _ = mimetypes.guess_type(file_path)
        handler._send_file(file_path, ctype or "application/octet-stream")

    def _api_get_config(self, handler, path, match):
        handler._send_json(self.config_store.to_dict())

    def _api_post_config(self, handler, path, match):
        body = handler._read_json_body()
        if not isinstance(body, dict):
            handler._send_json({"error": "Expected JSON object"}, 400)
            return
        # Validate/sanitize known paths
        for key in ("library_path", "temp_dir", "state_db_path", "log_path", "output_dir", "single_file"):
            if key in body and body[key]:
                body[key] = os.path.expanduser(str(body[key]))
        self.config_store.update(body)
        handler._send_json({"ok": True, "config": self.config_store.to_dict()})

    def _api_status(self, handler, path, match):
        state = self._state_db()
        stats = state.get_stats()
        session = state.get_session_stats()
        runner_status = self.runner.get_status()
        recent = state.get_pending()  # Actually we want recent completed
        # Let's query recent completed directly
        conn = state._init_db  # Hmm, StateDB doesn't expose connection directly.
        # Workaround: use sqlite3 directly
        import sqlite3
        db_path = self.config_store.get("state_db_path")
        recent_rows: List[Dict] = []
        failed_rows: List[Dict] = []
        currently_running = None
        shows: List[Dict] = []
        try:
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                c.execute(
                    "SELECT path, status, original_size, output_size, completed_at FROM files "
                    "WHERE status='completed' ORDER BY completed_at DESC LIMIT 20"
                )
                recent_rows = [dict(r) for r in c.fetchall()]
                c.execute(
                    "SELECT path, status, original_size, output_size, reason FROM files "
                    "WHERE status='failed' ORDER BY id DESC LIMIT 20"
                )
                failed_rows = [dict(r) for r in c.fetchall()]
                c.execute(
                    "SELECT path, original_size, started_at FROM files WHERE status='in_progress' "
                    "ORDER BY started_at DESC LIMIT 1"
                )
                row = c.fetchone()
                if row:
                    currently_running = dict(row)
                # Shows breakdown
                c.execute("SELECT path, status, original_size, output_size FROM files WHERE status='completed'")
                shows_data: Dict[str, Dict] = {}
                for r in c.fetchall():
                    parts = r["path"].split("/")
                    show = parts[-3] if len(parts) >= 3 else "unknown"
                    if show not in shows_data:
                        shows_data[show] = {"total": 0, "completed": 0, "saved": 0}
                    shows_data[show]["completed"] += 1
                    shows_data[show]["saved"] += (r["original_size"] or 0) - (r["output_size"] or 0)
                c.execute("SELECT path FROM files")
                for r in c.fetchall():
                    parts = r["path"].split("/")
                    show = parts[-3] if len(parts) >= 3 else "unknown"
                    if show not in shows_data:
                        shows_data[show] = {"total": 0, "completed": 0, "saved": 0}
                    shows_data[show]["total"] += 1
                shows = sorted([{"name": k, **v} for k, v in shows_data.items()], key=lambda x: -x["saved"])
        except Exception:
            pass

        handler._send_json({
            "stats": stats,
            "session": session,
            "recent": recent_rows,
            "failed": failed_rows,
            "shows": shows,
            "currently_running": currently_running,
            "runner": runner_status,
        })

    def _api_queue(self, handler, path, match):
        state = self._state_db()
        top = state.get_top_candidates(limit=100)
        handler._send_json({"queue": top})

    def _api_recent(self, handler, path, match):
        import sqlite3
        db_path = self.config_store.get("state_db_path")
        rows: List[Dict] = []
        try:
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                c.execute(
                    "SELECT path, status, original_size, output_size, completed_at FROM files "
                    "WHERE status='completed' ORDER BY completed_at DESC LIMIT 50"
                )
                rows = [dict(r) for r in c.fetchall()]
        except Exception:
            pass
        handler._send_json({"recent": rows})

    def _api_failed(self, handler, path, match):
        import sqlite3
        db_path = self.config_store.get("state_db_path")
        rows: List[Dict] = []
        try:
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                c.execute(
                    "SELECT path, status, original_size, output_size, reason, error_count FROM files "
                    "WHERE status='failed' ORDER BY id DESC LIMIT 50"
                )
                rows = [dict(r) for r in c.fetchall()]
        except Exception:
            pass
        handler._send_json({"failed": rows})

    def _api_report(self, handler, path, match):
        state = self._state_db()
        report = generate_report(state)
        handler._send_json(report)

    def _api_logs(self, handler, path, match):
        # Support both `limit` and `lines` query params for backward compat
        raw_limit = handler._query_param("limit", None)
        if raw_limit is None:
            raw_limit = handler._query_param("lines", 100)
        try:
            limit = int(raw_limit)
        except (ValueError, TypeError):
            limit = 100
        if limit < 1:
            limit = 100
        level = handler._query_param("level", None)
        if level == "":
            level = None
        lines = self.runner.log_handler.get_lines(limit=limit, level=level)
        handler._send_json({"logs": lines})

    def _api_events(self, handler, path, match):
        """Server-Sent Events endpoint."""
        q = SSEQueue()
        with self.event_lock:
            self.event_queues.append(q)
        try:
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Cache-Control", "no-cache")
            handler.send_header("Connection", "keep-alive")
            handler.send_header("Access-Control-Allow-Origin", "*")
            handler.end_headers()
            # Send initial connection ack
            handler.wfile.write(b"event: connected\ndata: {}\n\n")
            handler.wfile.flush()
            last_heartbeat = time.time()
            while True:
                try:
                    payload = q.get(timeout=1.0)
                    if payload is not None:
                        line = f"data: {json.dumps(payload)}\n\n"
                        handler.wfile.write(line.encode("utf-8"))
                        handler.wfile.flush()
                    # Heartbeat to detect dead connections
                    if time.time() - last_heartbeat >= 15:
                        handler.wfile.write(b":heartbeat\n\n")
                        handler.wfile.flush()
                        last_heartbeat = time.time()
                except (BrokenPipeError, ConnectionResetError):
                    break
        finally:
            with self.event_lock:
                if q in self.event_queues:
                    self.event_queues.remove(q)

    def _api_health_check(self, handler, path, match):
        cfg = self.config_store.to_config()
        ok, msg = self.runner.start_job("health_check", {"cfg": cfg})
        handler._send_json({"ok": ok, "message": msg})

    def _api_scan(self, handler, path, match):
        body = handler._read_json_body() or {}
        cfg = self.config_store.to_config()
        ok, msg = self.runner.start_job(
            "scan",
            {
                "cfg": cfg,
                "intelligent": body.get("intelligent", self.config_store.get("intelligent_scan", True)),
                "force": body.get("force", False),
            },
        )
        handler._send_json({"ok": ok, "message": msg})

    def _api_transcode(self, handler, path, match):
        body = handler._read_json_body() or {}
        cfg = self.config_store.to_config()
        # Only override dry_run if explicitly provided; respect config default
        if "dry_run" in body:
            cfg.dry_run = bool(body["dry_run"])
        # Override from request if present
        if "limit" in body:
            cfg.limit = body["limit"]
        if "force" in body:
            cfg.force = body["force"]
        if "include_pattern" in body:
            cfg.include_pattern = body["include_pattern"] or None
        if "output_dir" in body:
            cfg.output_dir = body["output_dir"] or None
        ok, msg = self.runner.start_job(
            "transcode",
            {
                "cfg": cfg,
                "limit": cfg.limit,
                "force": cfg.force,
            },
        )
        handler._send_json({"ok": ok, "message": msg})

    def _api_watch(self, handler, path, match):
        body = handler._read_json_body() or {}
        action = body.get("action", "start")
        if action == "stop":
            ok, msg = self.runner.stop()
            handler._send_json({"ok": ok, "message": msg})
            return
        cfg = self.config_store.to_config()
        ok, msg = self.runner.start_job(
            "watch",
            {
                "cfg": cfg,
                "intelligent": body.get("intelligent", self.config_store.get("intelligent_scan", True)),
                "interval": body.get("interval", self.config_store.get("watch_interval", 60.0)),
            },
        )
        handler._send_json({"ok": ok, "message": msg})

    def _api_stop(self, handler, path, match):
        ok, msg = self.runner.stop()
        handler._send_json({"ok": ok, "message": msg})

    def _api_reset_failed(self, handler, path, match):
        state = self._state_db()
        state.reset_failed()
        handler._send_json({"ok": True, "message": "Failed entries reset to pending."})

    def _api_extensions(self, handler, path, match):
        handler._send_json({"extensions": self.extensions.extensions})


# ------------------------------------------------------------------
# RequestHandler
# ------------------------------------------------------------------

class RequestHandler(BaseHTTPRequestHandler):
    app: WebUIApp

    def log_message(self, format, *args):
        # Suppress noisy access logs unless verbose mode desired
        pass

    def _send_json(self, data: Dict[str, Any], status: int = 200):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, file_path: str, content_type: str):
        with open(file_path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> Optional[Dict[str, Any]]:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _query_param(self, name: str, default=None):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        val = params.get(name, [default])[0]
        if val == "":
            return default
        return val

    def do_GET(self):
        handler, match = self.app._match_route("GET", self.path)
        if handler:
            handler(self, self.path, match)
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        handler, match = self.app._match_route("POST", self.path)
        if handler:
            handler(self, self.path, match)
        else:
            self.send_error(404, "Not Found")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def make_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
    app = WebUIApp(host=host, port=port)
    # Bind app to handler class via a closure or attribute injection
    class BoundHandler(RequestHandler):
        pass
    BoundHandler.app = app
    server = ThreadingHTTPServer((host, port), BoundHandler)
    return server, app


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Plex Compress Web UI")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port")
    args = parser.parse_args()

    server, app = make_server(args.host, args.port)
    print(f"Plex Compress Web UI running at http://{args.host}:{args.port}/")
    print(f"Config: {app.config_store.path}")
    print(f"State DB: {app.config_store.get('state_db_path')}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        app.runner.stop()
        server.shutdown()


if __name__ == "__main__":
    main()
