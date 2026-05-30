"""Background job runner for the Web UI.

Manages scan, transcode, watch, and health-check operations in background
threads and publishes progress events back to the UI via the server app.
"""

import fnmatch
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from ..config import Config
from ..health import run_health_check
from ..scanner import scan_library, is_candidate
from ..state import StateDB
from ..transcoder import transcode_file
from ..utils import setup_logging
from ..watch import LibraryWatcher
from plex_compress.webui.log_buffer import UILogHandler


class JobRunner:
    """Manages background transcoding/scanning jobs and publishes events."""

    def __init__(self, app):
        self.app = app
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="webui-runner")
        self.future: Optional[Any] = None
        self.cancel_event = threading.Event()
        self.watch_watcher: Optional[LibraryWatcher] = None
        self.lock = threading.Lock()
        self.state = "idle"  # idle | scanning | transcoding | watching | health_check
        self.progress: Dict[str, Any] = {}
        self.log_handler = UILogHandler(capacity=500)
        self._last_job_result: Optional[Dict] = None

    # ------------------------------------------------------------------
    # Public control
    # ------------------------------------------------------------------

    def start_job(self, job_type: str, kwargs: Dict[str, Any]) -> tuple:
        with self.lock:
            if self.future and not self.future.done():
                return False, f"Job already running: {self.state}"
            self.cancel_event.clear()
            self.state = job_type
            self.progress = {"type": job_type, "current": 0, "total": 0, "message": "Starting..."}
            self._last_job_result = None
            if job_type == "watch":
                self.future = self.executor.submit(self._run_watch, **kwargs)
            elif job_type == "scan":
                self.future = self.executor.submit(self._run_scan, **kwargs)
            elif job_type == "transcode":
                self.future = self.executor.submit(self._run_transcode, **kwargs)
            elif job_type == "health_check":
                self.future = self.executor.submit(self._run_health_check, **kwargs)
            else:
                return False, f"Unknown job type: {job_type}"
            return True, "Started"

    def stop(self) -> tuple:
        self.cancel_event.set()
        if self.watch_watcher:
            try:
                self.watch_watcher.stop()
            except Exception:
                pass
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
                "last_result": self._last_job_result,
            }

    # ------------------------------------------------------------------
    # Logger factory
    # ------------------------------------------------------------------

    def _make_logger(self, cfg: Config) -> logging.Logger:
        logger = logging.getLogger("plex_compress.webui")
        logger.setLevel(logging.DEBUG if cfg.verbose else logging.INFO)
        # Avoid duplicate handlers on repeated calls
        logger.handlers = [h for h in logger.handlers if not isinstance(h, (logging.StreamHandler, logging.FileHandler, UILogHandler))]
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        # Console
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        logger.addHandler(ch)
        # File
        if cfg.log_path:
            os.makedirs(os.path.dirname(cfg.log_path), exist_ok=True)
            fh = logging.FileHandler(cfg.log_path)
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        # UI buffer — reuse the instance that _api_logs reads from
        # Use plain message formatter to avoid duplicate timestamps in UI
        ui_fmt = logging.Formatter("%(message)s")
        self.log_handler.setFormatter(ui_fmt)
        logger.addHandler(self.log_handler)
        return logger

    # ------------------------------------------------------------------
    # Event publishing
    # ------------------------------------------------------------------

    def _publish(self, event_type: str, data: Dict[str, Any]):
        self.app.publish_event(event_type, data)

    def _set_progress(self, current: int, total: int, message: str, extra: Optional[Dict] = None):
        self.progress = {
            "type": self.state,
            "current": current,
            "total": total,
            "message": message,
            "timestamp": time.time(),
        }
        if extra:
            self.progress.update(extra)
        self._publish("progress", self.progress)

    def _finish(self, ok: bool, message: str, detail: Optional[Dict] = None):
        with self.lock:
            self._last_job_result = {"ok": ok, "message": message, "detail": detail or {}}
            self.state = "idle"
            self.progress = {}
        self._publish("finished", {"ok": ok, "message": message, "detail": detail or {}})

    # ------------------------------------------------------------------
    # Job implementations
    # ------------------------------------------------------------------

    def _run_health_check(self, cfg: Config):
        logger = self._make_logger(cfg)
        self._set_progress(0, 1, "Running health check...")
        try:
            ok, messages = run_health_check(cfg, logger)
            self._finish(ok, "Health check complete", {"messages": messages})
        except Exception as e:
            logger.exception("Health check failed")
            self._finish(False, f"Health check error: {e}")

    def _run_scan(self, cfg: Config, intelligent: bool = True, force: bool = False):
        logger = self._make_logger(cfg)
        state = StateDB(cfg.state_db_path)
        self._set_progress(0, 1, "Scanning library...")
        try:
            if intelligent:
                from ..intelligence import scan_library_intelligent
                report = scan_library_intelligent(cfg, state=state, force=force, logger=logger)
            else:
                report = scan_library(cfg, state=state, force=force)
            self._finish(
                True,
                f"Scan complete: {report['candidates']} candidates, {report['already_optimal']} already optimal",
                report,
            )
        except Exception as e:
            logger.exception("Scan failed")
            self._finish(False, f"Scan error: {e}")

    def _run_transcode(self, cfg: Config, limit: Optional[int] = None, force: bool = False):
        logger = self._make_logger(cfg)
        state = StateDB(cfg.state_db_path)

        # If there are pending files in the DB, use them; otherwise scan first
        pending = state.get_pending()
        if not pending:
            logger.info("No pending files in DB; running scan first...")
            self._set_progress(0, 1, "Scanning before transcode...")
            if cfg.dry_run:
                from ..intelligence import scan_library_intelligent
                report = scan_library_intelligent(cfg, state=state, force=force, logger=logger)
            else:
                report = scan_library(cfg, state=state, force=force)
            candidates = report["candidates"]
        else:
            candidates = pending

        # Apply filters
        if cfg.include_pattern:
            candidates = [p for p in candidates if fnmatch.fnmatch(os.path.basename(p), cfg.include_pattern)]
        if limit:
            candidates = candidates[:limit]

        total = len(candidates)
        if total == 0:
            self._finish(True, "No candidates to transcode.")
            return

        success = 0
        failed = 0
        skipped = 0

        session_id = state.start_session(name="webui-batch")
        self._set_progress(0, total, f"Starting batch of {total} files...")

        for idx, path in enumerate(candidates, 1):
            if self.cancel_event.is_set():
                logger.info("Cancellation requested, stopping batch.")
                break

            existing = state.get_status(path)
            if existing == "completed" and not force:
                skipped += 1
                continue

            self._set_progress(
                idx - 1,
                total,
                f"Transcoding {os.path.basename(path)}",
                {"current_file": path},
            )
            state.mark_started(path)
            try:
                ok = transcode_file(path, cfg, state, logger)
                if ok:
                    success += 1
                else:
                    failed += 1
            except Exception as e:
                logger.error(f"Exception transcoding {path}: {e}")
                state.mark_failed(path, str(e))
                failed += 1

        state.end_session(session_id)
        stats = state.get_stats()
        saved_mb = stats.get("saved_bytes", 0) / (1024 * 1024)
        self._finish(
            failed == 0,
            f"Batch complete: {success} succeeded, {failed} failed, {skipped} skipped",
            {"stats": stats, "saved_mb": saved_mb},
        )

    def _run_watch(self, cfg: Config, intelligent: bool = True, interval: float = 60.0):
        logger = self._make_logger(cfg)
        state = StateDB(cfg.state_db_path)
        self.watch_watcher = LibraryWatcher(cfg, state, logger, intelligent=intelligent)

        def _process(path: str) -> bool:
            if self.cancel_event.is_set():
                return False
            self._publish("watch_file", {"path": path, "status": "started"})
            try:
                ok = transcode_file(path, cfg, state, logger)
                self._publish("watch_file", {"path": path, "status": "completed" if ok else "failed"})
                return ok
            except Exception as e:
                logger.error(f"Watch mode error on {path}: {e}")
                self._publish("watch_file", {"path": path, "status": "failed", "error": str(e)})
                return False

        self._set_progress(0, 0, f"Watch mode started (interval {interval}s)")
        try:
            self.watch_watcher.run(_process, interval=interval)
        except Exception as e:
            logger.exception("Watch mode crashed")
            self._finish(False, f"Watch mode error: {e}")
            return
        finally:
            self.watch_watcher = None

        self._finish(True, "Watch mode stopped.")
