"""Lightweight HTTP server for the Plex Compress Web UI.

Serves a real-time dashboard with controls for scan, transcode, health-check,
and watch operations. Uses stdlib only (ThreadingHTTPServer) with SSE for
live updates.

Usage:
    python3 -m plex_compress.webui
    python3 -m plex_compress.webui --host 0.0.0.0 --port 8765
"""

import argparse
import json
import mimetypes
import os
import queue
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from plex_compress.config import Config
from plex_compress.health import run_health_check
from plex_compress.scanner import scan_library
from plex_compress.state import StateDB
from plex_compress.transcoder import transcode_file
from plex_compress.utils import setup_logging
from plex_compress.watch import LibraryWatcher

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765

# ------------------------------------------------------------------
# Config helpers
# ------------------------------------------------------------------

def _get_config() -> Config:
    """Build config from environment variables (same as container entrypoint)."""
    encoder = os.environ.get("PLEX_COMPRESS_ENCODER", "libx265")
    return Config(
        library_path=os.environ.get("PLEX_COMPRESS_LIBRARY_PATH", ""),
        temp_dir=os.environ.get("PLEX_COMPRESS_TEMP_DIR", Config().temp_dir),
        state_db_path=os.environ.get("PLEX_COMPRESS_STATE_DB", Config().state_db_path),
        log_path=os.environ.get("PLEX_COMPRESS_LOG", None),
        video_encoder=encoder,
        video_quality=int(os.environ.get("PLEX_COMPRESS_QUALITY", "28")),
        video_preset=os.environ.get("PLEX_COMPRESS_PRESET", "medium"),
        parallel_jobs=int(os.environ.get("PLEX_COMPRESS_PARALLEL", "1")),
        keep_backup=os.environ.get("PLEX_COMPRESS_BACKUP", "0") == "1",
        dry_run=False,
        verbose=os.environ.get("PLEX_COMPRESS_VERBOSE", "0") == "1",
        verify_checksum=os.environ.get("PLEX_COMPRESS_VERIFY_CHECKSUM", "0") == "1",
    )


# ------------------------------------------------------------------
# SSE queue
# ------------------------------------------------------------------

class SSEQueue:
    def __init__(self, maxlen: int = 200):
        self._q: queue.Queue = queue.Queue(maxsize=maxlen)

    def put(self, data: Dict[str, Any]):
        try:
            self._q.put_nowait(data)
        except queue.Full:
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


# ------------------------------------------------------------------
# Job runner
# ------------------------------------------------------------------

class JobRunner:
    """Runs scan/transcode/health-check jobs in a background thread."""

    def __init__(self, app: "WebUIApp"):
        self.app = app
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="webui-runner")
        self.future: Optional[Any] = None
        self.cancel_event = threading.Event()
        self.lock = threading.Lock()
        self.state = "idle"  # idle | scanning | transcoding | health_check
        self.progress: Dict[str, Any] = {}
        self._last_result: Optional[Dict] = None
        self._logger: Optional[Any] = None

    # -- public control ------------------------------------------------

    def start_job(self, job_type: str, kwargs: Dict[str, Any]) -> Tuple[bool, str]:
        with self.lock:
            if self.future and not self.future.done():
                return False, f"Job already running: {self.state}"
            self.cancel_event.clear()
            self.state = job_type
            self.progress = {"type": job_type, "current": 0, "total": 0, "message": "Starting..."}
            self._last_result = None

            if job_type == "scan":
                self.future = self.executor.submit(self._run_scan, **kwargs)
            elif job_type == "transcode":
                self.future = self.executor.submit(self._run_transcode, **kwargs)
            elif job_type == "health_check":
                self.future = self.executor.submit(self._run_health_check, **kwargs)
            else:
                return False, f"Unknown job type: {job_type}"
            return True, "Started"

    def stop(self) -> Tuple[bool, str]:
        self.cancel_event.set()
        with self.lock:
            if self.future and self.future.done():
                self.state = "idle"
        return True, "Stop requested"

    def get_status(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "state": self.state,
                "progress": self.progress,
                "cancel_requested": self.cancel_event.is_set(),
                "has_job": self.future is not None and not self.future.done(),
                "last_result": self._last_result,
            }

    # -- internal helpers ----------------------------------------------

    def _publish(self, event_type: str, data: Dict[str, Any]):
        self.app.publish_event(event_type, data)

    def _set_progress(self, current: int, total: int, message: str):
        self.progress = {"type": self.state, "current": current, "total": total, "message": message}
        self._publish("progress", self.progress)

    def _make_logger(self, cfg: Config):
        return setup_logging(cfg.verbose, cfg.log_path)

    def _is_cancelled(self) -> bool:
        return self.cancel_event.is_set()

    # -- job implementations -------------------------------------------

    def _run_scan(self, cfg: Config, intelligent: bool = True, force: bool = False,
                  full_scan: bool = False):
        logger = self._make_logger(cfg)
        mode = "full" if full_scan else "incremental"
        self._set_progress(0, 0, f"Scanning library ({mode})...")

        def _progress(done: int, total: int, probed: int, cached: int):
            if done % 100 == 0 or done == total:
                self._set_progress(done, total, f"Scanning ({mode}): {done}/{total} ({probed} probed, {cached} cached)")

        try:
            state = StateDB(cfg.state_db_path)
            report = scan_library(cfg, state=state, force=force, full_scan=full_scan,
                                  logger=logger, progress_cb=_progress)
            candidates = report["candidates"]
            self._set_progress(len(candidates), report["total_files"], f"Scan complete. {len(candidates)} candidates.")
            self._last_result = {
                "mode": mode,
                "total_files": report["total_files"],
                "candidates": len(candidates),
                "already_optimal": report["already_optimal"],
                "skipped": len(report["skipped"]),
                "errors": len(report.get("errors", [])),
                "probed": report.get("probed", 0),
                "cached": report.get("cached", 0),
                "estimated_savings_gb": round(report["estimated_savings_gb"], 2),
            }
            self._publish("scan_complete", self._last_result)
        except Exception as e:
            logger.error(f"Scan failed: {e}")
            self._last_result = {"error": str(e)}
            self._publish("error", {"message": str(e)})
        finally:
            self.state = "idle"

    def _run_transcode(self, cfg: Config, limit: Optional[int] = None, force: bool = False):
        logger = self._make_logger(cfg)
        state = StateDB(cfg.state_db_path)
        report = scan_library(cfg, state=state, force=force)
        candidates = report["candidates"]
        if limit:
            candidates = candidates[:limit]
        total = len(candidates)
        success = failed = skipped = 0
        self._set_progress(0, total, "Starting transcode batch...")
        for i, path in enumerate(candidates):
            if self._is_cancelled():
                logger.info("Transcode stopped by user.")
                break
            existing = state.get_status(path)
            if existing == "completed" and not force:
                skipped += 1
                continue
            state.mark_started(path)
            self._set_progress(i + 1, total, f"Transcoding: {os.path.basename(path)}")
            ok = transcode_file(path, cfg, state, logger)
            if ok:
                success += 1
            else:
                failed += 1
        stats = state.get_stats()
        self._last_result = {
            "success": success,
            "failed": failed,
            "skipped": skipped,
            "saved_mb": round(stats.get("saved_bytes", 0) / 1024 / 1024, 1),
        }
        self._set_progress(total, total, f"Batch complete: {success} ok, {failed} failed, {skipped} skipped.")
        self._publish("transcode_complete", self._last_result)
        self.state = "idle"

    def _run_health_check(self, cfg: Config):
        logger = self._make_logger(cfg)
        self._set_progress(0, 0, "Running health check...")
        try:
            ok, messages = run_health_check(cfg, logger)
            self._last_result = {"ok": ok, "messages": messages}
            self._set_progress(1, 1, "Health check complete." if ok else "Health check failed.")
            self._publish("health_check_complete", self._last_result)
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            self._last_result = {"ok": False, "messages": [str(e)]}
            self._publish("error", {"message": str(e)})
        finally:
            self.state = "idle"


# ------------------------------------------------------------------
# App
# ------------------------------------------------------------------

class WebUIApp:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self.runner = JobRunner(self)
        self.event_queues: List[SSEQueue] = []
        self.event_lock = threading.Lock()
        self._routes: List[Tuple[str, str, Callable]] = []
        self._build_routes()

    def _build_routes(self):
        self._routes = [
            ("GET", "^/$", self._serve_index),
            ("GET", "^/static/", self._serve_static),
            ("GET", "^/api/status$", self._api_status),
            ("GET", "^/api/queue$", self._api_queue),
            ("GET", "^/api/recent$", self._api_recent),
            ("GET", "^/api/failed$", self._api_failed),
            ("GET", "^/api/report$", self._api_report),
            ("GET", "^/api/logs$", self._api_logs),
            ("GET", "^/api/config$", self._api_config),
            ("GET", "^/api/events$", self._api_events),
            ("POST", "^/api/scan$", self._api_scan),
            ("POST", "^/api/transcode$", self._api_transcode),
            ("POST", "^/api/health-check$", self._api_health_check),
            ("POST", "^/api/stop$", self._api_stop),
            ("POST", "^/api/reset-failed$", self._api_reset_failed),
        ]

    def _match_route(self, method: str, path: str) -> Tuple[Optional[Callable], Optional[Any]]:
        import re
        for m, pattern, handler in self._routes:
            if m == method:
                match = re.match(pattern, path)
                if match:
                    return handler, match
        return None, None

    def publish_event(self, event_type: str, data: Dict[str, Any]):
        payload = {"type": event_type, "data": data, "time": time.time()}
        with self.event_lock:
            for q in self.event_queues:
                q.put(payload)

    # -- route handlers ------------------------------------------------

    def _serve_index(self, handler: "RequestHandler", path: str, match: Any):
        idx = STATIC_DIR / "index.html"
        if idx.exists():
            handler._send_file(str(idx), "text/html; charset=utf-8")
        else:
            handler._send_json({"error": "Dashboard UI not found."}, 404)

    def _serve_static(self, handler: "RequestHandler", path: str, match: Any):
        rel = path[len("/static/"):]
        # Security: prevent path traversal
        rel = rel.replace("..", "")
        file_path = STATIC_DIR / rel
        if file_path.exists() and file_path.is_file():
            ctype, _ = mimetypes.guess_type(str(file_path))
            handler._send_file(str(file_path), ctype or "application/octet-stream")
        else:
            handler.send_error(404, "Not Found")

    def _api_status(self, handler: "RequestHandler", path: str, match: Any):
        db_path = _get_config().state_db_path
        data = _db_query(db_path)
        data["runner"] = self.runner.get_status()
        handler._send_json(data)

    def _api_queue(self, handler: "RequestHandler", path: str, match: Any):
        db_path = _get_config().state_db_path
        rows: List[Dict] = []
        try:
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                c.execute(
                    "SELECT path, status, original_size, reason, error_count FROM files "
                    "WHERE status IN ('pending', 'failed') ORDER BY id LIMIT 200"
                )
                rows = [dict(r) for r in c.fetchall()]
        except Exception:
            pass
        handler._send_json({"queue": rows})

    def _api_recent(self, handler: "RequestHandler", path: str, match: Any):
        db_path = _get_config().state_db_path
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

    def _api_failed(self, handler: "RequestHandler", path: str, match: Any):
        db_path = _get_config().state_db_path
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

    def _api_report(self, handler: "RequestHandler", path: str, match: Any):
        db_path = _get_config().state_db_path
        report = _generate_report(db_path)
        handler._send_json(report)

    def _api_logs(self, handler: "RequestHandler", path: str, match: Any):
        limit = int(handler._query_param("limit", 100) or 100)
        log_path = _get_config().log_path
        lines: List[str] = []
        if log_path and os.path.exists(log_path):
            try:
                # Tail the last ~256 KB of the log, then keep the last N lines.
                tail_bytes = 256 * 1024
                with open(log_path, "rb") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - tail_bytes))
                    chunk = f.read()
                text = chunk.decode("utf-8", errors="replace")
                lines = text.splitlines()[-limit:]
            except Exception:
                lines = []
        handler._send_json({"logs": lines})

    def _api_config(self, handler: "RequestHandler", path: str, match: Any):
        cfg = _get_config()
        handler._send_json({
            "library_path": cfg.library_path,
            "temp_dir": cfg.temp_dir,
            "state_db_path": cfg.state_db_path,
            "log_path": cfg.log_path,
            "video_encoder": cfg.video_encoder,
            "video_quality": cfg.video_quality,
            "video_preset": cfg.video_preset,
            "parallel_jobs": cfg.parallel_jobs,
            "keep_backup": cfg.keep_backup,
            "dry_run": cfg.dry_run,
            "verbose": cfg.verbose,
            "verify_checksum": cfg.verify_checksum,
        })

    def _api_events(self, handler: "RequestHandler", path: str, match: Any):
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
            handler.wfile.write(b"event: connected\ndata: {}\n\n")
            handler.wfile.flush()
            last_heartbeat = time.time()
            while True:
                payload = q.get(timeout=1.0)
                if payload is not None:
                    line = f"data: {json.dumps(payload, default=str)}\n\n"
                    handler.wfile.write(line.encode("utf-8"))
                    handler.wfile.flush()
                if time.time() - last_heartbeat >= 15:
                    handler.wfile.write(b":heartbeat\n\n")
                    handler.wfile.flush()
                    last_heartbeat = time.time()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with self.event_lock:
                if q in self.event_queues:
                    self.event_queues.remove(q)

    def _api_scan(self, handler: "RequestHandler", path: str, match: Any):
        body = handler._read_json_body() or {}
        cfg = _get_config()
        cfg.dry_run = body.get("dry_run", True)
        ok, msg = self.runner.start_job("scan", {
            "cfg": cfg,
            "force": body.get("force", False),
            "full_scan": body.get("full", False),
        })
        handler._send_json({"ok": ok, "message": msg})

    def _api_transcode(self, handler: "RequestHandler", path: str, match: Any):
        body = handler._read_json_body() or {}
        cfg = _get_config()
        cfg.dry_run = False
        ok, msg = self.runner.start_job(
            "transcode",
            {"cfg": cfg, "limit": body.get("limit"), "force": body.get("force", False)},
        )
        handler._send_json({"ok": ok, "message": msg})

    def _api_health_check(self, handler: "RequestHandler", path: str, match: Any):
        cfg = _get_config()
        ok, msg = self.runner.start_job("health_check", {"cfg": cfg})
        handler._send_json({"ok": ok, "message": msg})

    def _api_stop(self, handler: "RequestHandler", path: str, match: Any):
        ok, msg = self.runner.stop()
        handler._send_json({"ok": ok, "message": msg})

    def _api_reset_failed(self, handler: "RequestHandler", path: str, match: Any):
        db_path = _get_config().state_db_path
        try:
            state = StateDB(db_path)
            state.reset_failed()
            handler._send_json({"ok": True, "message": "Failed entries reset to pending."})
        except Exception as e:
            handler._send_json({"ok": False, "message": str(e)}, 500)


# ------------------------------------------------------------------
# Report generator
# ------------------------------------------------------------------

def _generate_report(db_path: str) -> Dict[str, Any]:
    if not os.path.exists(db_path):
        return {"summary": {}, "by_codec": [], "by_resolution": [], "top_pending": []}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Summary
    c.execute("SELECT COUNT(*) FROM files")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM files WHERE status='completed'")
    completed = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM files WHERE status='failed'")
    failed = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM files WHERE status='pending'")
    pending = c.fetchone()[0]
    c.execute("SELECT SUM(original_size), SUM(output_size) FROM files WHERE status='completed'")
    row = c.fetchone()
    saved = (row[0] or 0) - (row[1] or 0)

    # By source video codec
    c.execute(
        "SELECT video_codec, COUNT(*) as cnt, SUM(original_size) as orig, SUM(output_size) as out "
        "FROM files WHERE status='completed' AND video_codec IS NOT NULL GROUP BY video_codec"
    )
    by_codec = []
    for r in c.fetchall():
        by_codec.append({
            "codec": r["video_codec"],
            "count": r["cnt"],
            "saved": (r["orig"] or 0) - (r["out"] or 0),
        })

    # By resolution
    c.execute(
        "SELECT video_width, video_height, COUNT(*) as cnt, SUM(original_size) as orig, SUM(output_size) as out "
        "FROM files WHERE status='completed' AND video_width IS NOT NULL GROUP BY video_width, video_height"
    )
    by_resolution = []
    for r in c.fetchall():
        by_resolution.append({
            "resolution": f"{r['video_width']}x{r['video_height']}",
            "count": r["cnt"],
            "saved": (r["orig"] or 0) - (r["out"] or 0),
        })

    # Top pending by original size (proxy for biggest savings)
    c.execute(
        "SELECT path, original_size, video_codec, video_width, video_height FROM files "
        "WHERE status='pending' AND original_size IS NOT NULL ORDER BY original_size DESC LIMIT 20"
    )
    top_pending = [dict(r) for r in c.fetchall()]

    # Unreadable / broken source files (probe errors). Tracked as status
    # 'error', plus any legacy rows that recorded the error under 'skipped'.
    c.execute(
        "SELECT path, original_size, reason FROM files "
        "WHERE status='error' OR reason LIKE 'probe_error%' ORDER BY original_size DESC LIMIT 100"
    )
    unreadable = []
    for r in c.fetchall():
        reason = (r["reason"] or "").splitlines()[0] if r["reason"] else ""
        unreadable.append({"path": r["path"], "original_size": r["original_size"], "reason": reason})

    conn.close()
    return {
        "summary": {
            "total": total,
            "completed": completed,
            "failed": failed,
            "pending": pending,
            "unreadable": len(unreadable),
            "saved_bytes": saved,
        },
        "by_codec": by_codec,
        "by_resolution": by_resolution,
        "top_pending": top_pending,
        "unreadable": unreadable,
    }


# ------------------------------------------------------------------
# Dashboard query (shared with old dashboard)
# ------------------------------------------------------------------

def _db_query(db_path: str) -> Dict[str, Any]:
    if not os.path.exists(db_path):
        return {
            "stats": {"total": 0, "completed": 0, "failed": 0, "in_progress": 0,
                      "skipped": 0, "saved_bytes": 0},
            "recent": [],
            "failed": [],
            "shows": [],
            "currently_running": None,
            "session": {"files": 0, "saved_bytes": 0, "original_size": 0},
        }

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM files")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM files WHERE status='completed'")
    completed = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM files WHERE status='failed'")
    failed = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM files WHERE status='in_progress'")
    in_progress = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM files WHERE status='skipped'")
    skipped = c.fetchone()[0]
    c.execute("SELECT SUM(original_size), SUM(output_size) FROM files WHERE status='completed'")
    row = c.fetchone()
    saved_bytes = (row[0] or 0) - (row[1] or 0)

    c.execute(
        "SELECT path, original_size, started_at FROM files WHERE status='in_progress' ORDER BY started_at DESC LIMIT 1"
    )
    running_row = c.fetchone()
    currently_running = None
    if running_row:
        currently_running = {
            "path": running_row["path"],
            "original_size": running_row["original_size"],
            "started_at": running_row["started_at"],
        }

    c.execute(
        "SELECT path, status, original_size, output_size, completed_at FROM files WHERE status='completed' ORDER BY completed_at DESC LIMIT 20"
    )
    recent = [dict(r) for r in c.fetchall()]

    c.execute(
        "SELECT path, status, original_size, output_size, reason FROM files WHERE status='failed' ORDER BY id DESC LIMIT 20"
    )
    failed_rows = [dict(r) for r in c.fetchall()]

    c.execute("SELECT path, status, original_size, output_size FROM files WHERE status='completed'")
    shows_data: Dict[str, Dict] = {}
    for r in c.fetchall():
        path = r["path"]
        parts = path.split("/")
        show = parts[-3] if len(parts) >= 3 else "unknown"
        if show not in shows_data:
            shows_data[show] = {"total": 0, "completed": 0, "saved": 0}
        shows_data[show]["completed"] += 1
        orig = r["original_size"] or 0
        out = r["output_size"] or 0
        shows_data[show]["saved"] += (orig - out)

    c.execute("SELECT path FROM files")
    for r in c.fetchall():
        path = r["path"]
        parts = path.split("/")
        show = parts[-3] if len(parts) >= 3 else "unknown"
        if show not in shows_data:
            shows_data[show] = {"total": 0, "completed": 0, "saved": 0}
        shows_data[show]["total"] += 1

    shows = sorted([{"name": k, **v} for k, v in shows_data.items()], key=lambda x: -int(x["saved"]))

    c.execute("SELECT started_at FROM sessions ORDER BY id DESC LIMIT 1")
    session_row = c.fetchone()
    session_stats = {"files": 0, "original_size": 0, "output_size": 0, "saved_bytes": 0}
    if session_row:
        c.execute(
            "SELECT COUNT(*), SUM(original_size), SUM(output_size) FROM files WHERE status='completed' AND started_at >= ?",
            (session_row[0],)
        )
        srow = c.fetchone()
        session_stats = {
            "files": srow[0] or 0,
            "original_size": srow[1] or 0,
            "output_size": srow[2] or 0,
            "saved_bytes": (srow[1] or 0) - (srow[2] or 0),
        }

    conn.close()
    return {
        "stats": {
            "total": total,
            "completed": completed,
            "failed": failed,
            "in_progress": in_progress,
            "skipped": skipped,
            "saved_bytes": saved_bytes,
        },
        "recent": recent,
        "failed": failed_rows,
        "shows": shows,
        "currently_running": currently_running,
        "session": session_stats,
    }


# ------------------------------------------------------------------
# Request handler
# ------------------------------------------------------------------

class RequestHandler(BaseHTTPRequestHandler):
    app: WebUIApp

    def log_message(self, format, *args):
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

    def _query_param(self, name: str, default=None) -> Any:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        return params.get(name, [default])[0]

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


# ------------------------------------------------------------------
# Server bootstrap
# ------------------------------------------------------------------

def make_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
    app = WebUIApp(host=host, port=port)

    class BoundHandler(RequestHandler):
        pass

    BoundHandler.app = app
    server = ThreadingHTTPServer((host, port), BoundHandler)
    return server, app


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Plex Compress Web UI")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port")
    args = parser.parse_args(argv)

    server, app = make_server(args.host, args.port)
    cfg = _get_config()
    print(f"Plex Compress Web UI running at http://{args.host}:{args.port}/")
    print(f"State DB: {cfg.state_db_path}")
    print(f"Library:  {cfg.library_path or '(not set)'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        app.runner.stop()
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
