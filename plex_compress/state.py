"""SQLite state database for resume and tracking."""

import os
import sqlite3
from datetime import datetime
from typing import List, Optional, Dict, Any

from . import StateError, ResumeError


def compute_scan_sig(path: str) -> Optional[str]:
    """Cheap change-detection signature for a file: 'mtime:size'.

    A single stat() call (one network round-trip on SMB/NFS) instead of a
    full ffprobe. Returns None if the file is missing/unreadable.
    """
    try:
        st = os.stat(path)
        return f"{int(st.st_mtime)}:{st.st_size}"
    except OSError:
        return None


# Add-on columns for the encode-metrics / prediction-accuracy tracker.
# All columns are nullable so existing rows are unaffected; the migration
# adds them on first connection to a pre-existing database.
_METRICS_COLUMNS = [
    ("video_codec", "TEXT"),
    ("video_width", "INTEGER"),
    ("video_height", "INTEGER"),
    ("video_bitrate", "INTEGER"),
    ("audio_codec", "TEXT"),
    ("audio_channels", "INTEGER"),
    ("duration", "REAL"),
    ("source_bpp", "REAL"),
    ("output_bpp", "REAL"),
    ("achieved_fps", "REAL"),
    ("predicted_savings_bytes", "INTEGER"),
    ("actual_savings_bytes", "INTEGER"),
    ("encoder", "TEXT"),
    ("encoder_quality", "INTEGER"),
    ("encoder_preset", "TEXT"),
    ("calibration_id", "TEXT"),
]

# Build a CREATE TABLE statement that includes both the legacy columns and
# the new metrics columns, so fresh databases get them in the base schema.
_files_columns = [
    "id INTEGER PRIMARY KEY AUTOINCREMENT",
    "path TEXT UNIQUE NOT NULL",
    "status TEXT NOT NULL DEFAULT 'pending'",
    "original_size INTEGER",
    "output_size INTEGER",
    "reason TEXT",
    "started_at TEXT",
    "completed_at TEXT",
    "error_count INTEGER DEFAULT 0",
    "scan_sig TEXT",
] + [f"{name} {ddl}" for name, ddl in _METRICS_COLUMNS]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS files (
    {', '.join(_files_columns)}
);

CREATE INDEX IF NOT EXISTS idx_status ON files(status);
CREATE INDEX IF NOT EXISTS idx_path ON files(path);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00',
    ended_at TEXT,
    name TEXT
);

CREATE TABLE IF NOT EXISTS encode_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    encoder TEXT,
    encoder_quality INTEGER,
    encoder_preset TEXT,
    width INTEGER,
    height INTEGER,
    fps_source REAL,
    achieved_fps REAL,
    wallclock_seconds REAL,
    source_bpp REAL,
    output_bpp REAL,
    input_bytes INTEGER,
    output_bytes INTEGER,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_encode_metrics_path ON encode_metrics(path);
"""


class StateDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()
        self.reset_in_progress()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)
            # Enable WAL mode for better concurrency and reliability
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            # Migration: add scan_sig to pre-existing databases
            cols = [r[1] for r in conn.execute("PRAGMA table_info(files)").fetchall()]
            if "scan_sig" not in cols:
                conn.execute("ALTER TABLE files ADD COLUMN scan_sig TEXT")
            # Migration: encode-metrics columns for the prediction-accuracy tracker.
            for col, ddl in _METRICS_COLUMNS:
                if col not in cols:
                    conn.execute(f"ALTER TABLE files ADD COLUMN {col} {ddl}")
            conn.commit()
    def reset_in_progress(self):
        """Reset any in-progress entries to pending (useful after a crash)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE files SET status='pending' WHERE status='in_progress'")
            conn.commit()

    def _now(self) -> str:
        return datetime.utcnow().isoformat()

    def start_session(self, name: str = "") -> int:
        """Record a new batch session start. Returns session id."""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.execute(
                "INSERT INTO sessions (started_at, name) VALUES (?, ?)",
                (self._now(), name)
            )
            conn.commit()
            return c.lastrowid or 0

    def end_session(self, session_id: int):
        """Mark a session as ended."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET ended_at=? WHERE id=?",
                (self._now(), session_id)
            )
            conn.commit()

    def get_current_session_start(self) -> Optional[str]:
        """Return the started_at of the most recent session."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT started_at FROM sessions ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return row[0] if row else None

    def get_session_stats(self) -> Dict[str, Any]:
        """Return stats for the current session (since most recent session start)."""
        session_start = self.get_current_session_start()
        if not session_start:
            return {
                "files": 0,
                "original_size": 0,
                "output_size": 0,
                "saved_bytes": 0,
            }
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """SELECT COUNT(*), SUM(original_size), SUM(output_size)
                   FROM files WHERE status='completed' AND started_at >= ?""",
                (session_start,)
            )
            count, orig, out = c.fetchone()
            orig = orig or 0
            out = out or 0
            return {
                "files": count or 0,
                "original_size": orig,
                "output_size": out,
                "saved_bytes": orig - out,
            }

    def upsert(self, path: str, status: str, original_size: Optional[int] = None,
               output_size: Optional[int] = None, reason: Optional[str] = None):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO files (path, status, original_size, output_size, reason, started_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET
                       status=excluded.status,
                       original_size=COALESCE(excluded.original_size, original_size),
                       output_size=COALESCE(excluded.output_size, output_size),
                       reason=COALESCE(excluded.reason, reason),
                       started_at=COALESCE(excluded.started_at, started_at)""",
                (path, status, original_size, output_size, reason, self._now())
            )
            conn.commit()

    def mark_started(self, path: str, original_size: Optional[int] = None):
        self.upsert(path, "in_progress", original_size=original_size)

    def mark_completed(self, path: str, output_size: int, metrics: Optional[Dict[str, Any]] = None):
        """Mark a file as successfully transcoded.

        ``metrics`` is an optional dict; any of the following keys are
        persisted to the matching column (other keys are silently ignored):
            video_codec, video_width, video_height, video_bitrate,
            audio_codec, audio_channels, duration,
            source_bpp, output_bpp, achieved_fps,
            predicted_savings_bytes, actual_savings_bytes,
            encoder, encoder_quality, encoder_preset, calibration_id.
        Also records a row in the ``encode_metrics`` table for the
        prediction-accuracy tracker.
        Never raises on metrics errors; logging is best-effort.
        """
        # Store the signature of the *new* (transcoded) file so future scans
        # recognise it as unchanged and skip re-probing it.
        sig = compute_scan_sig(path)
        m = metrics or {}
        with sqlite3.connect(self.db_path) as conn:
            set_clauses = [
                "status='completed'",
                "output_size=?",
                "completed_at=?",
                "scan_sig=?",
            ]
            params: list = [output_size, self._now(), sig]
            for col, _ddl in _METRICS_COLUMNS:
                if col in m:
                    set_clauses.append(f"{col}=?")
                    params.append(m[col])
            params.append(path)
            conn.execute(
                f"UPDATE files SET {', '.join(set_clauses)} WHERE path=?",
                params,
            )
            conn.commit()
        # Best-effort side table for analytics (do not block on failure).
        if m:
            try:
                self.record_encode_metrics(path, m)
            except Exception:
                pass

    def record_encode_metrics(self, path: str, metrics: Dict[str, Any]) -> None:
        """Append a row to encode_metrics for the prediction-accuracy tracker.

        Mirrors the columns already stored on the files row. Never raises.
        """
        m = metrics or {}
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO encode_metrics
                   (path, encoder, encoder_quality, encoder_preset,
                    width, height, fps_source, achieved_fps,
                    wallclock_seconds, source_bpp, output_bpp,
                    input_bytes, output_bytes, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    path,
                    m.get("encoder"),
                    m.get("encoder_quality"),
                    m.get("encoder_preset"),
                    m.get("video_width"),
                    m.get("video_height"),
                    m.get("fps_source"),
                    m.get("achieved_fps"),
                    m.get("wallclock_seconds"),
                    m.get("source_bpp"),
                    m.get("output_bpp"),
                    m.get("input_bytes"),
                    m.get("output_bytes"),
                    self._now(),
                ),
            )
            conn.commit()

    def get_prediction_accuracy(self) -> Optional[Dict[str, Any]]:
        """Compare predicted vs. actual savings across all completed files.

        Returns a dict with prediction accuracy stats, or None if there are
        no rows with both predicted and actual values recorded.
        """
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT COUNT(*),
                          COALESCE(SUM(predicted_savings_bytes), 0),
                          COALESCE(SUM(actual_savings_bytes), 0),
                          COALESCE(SUM(output_size), 0),
                          COALESCE(SUM(original_size), 0)
                   FROM files
                   WHERE status='completed'
                     AND predicted_savings_bytes IS NOT NULL
                     AND actual_savings_bytes IS NOT NULL"""
            ).fetchone()
        if not row or row[0] == 0:
            return None
        count, pred_savings, act_savings, total_out, total_in = row
        pred_savings = pred_savings or 0
        act_savings = act_savings or 0
        total_in = total_in or 0
        total_out = total_out or 0
        abs_err = abs(pred_savings - act_savings)
        rel_err = (abs_err / max(1, abs(pred_savings))) * 100.0
        return {
            "samples": int(count),
            "predicted_savings_bytes": int(pred_savings),
            "actual_savings_bytes": int(act_savings),
            "absolute_error_bytes": int(abs_err),
            "relative_error_pct": round(rel_err, 2),
            "actual_savings_pct": round((act_savings / total_in) * 100.0, 2) if total_in else 0.0,
        }

    def get_bpp_table(self) -> Dict[int, float]:
        """Return median output_bpp per width tier, computed from real encodes.

        Used by the scanner to refine its predictions after we have enough
        ground truth. Returns an empty dict if no data is available.
        """
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT video_width, output_bpp FROM files
                   WHERE status='completed' AND output_bpp IS NOT NULL
                     AND video_width IS NOT NULL AND output_bpp > 0"""
            ).fetchall()
        if not rows:
            return {}
        by_width: Dict[int, List[float]] = {}
        for w, b in rows:
            by_width.setdefault(int(w), []).append(float(b))
        # Use median per tier; safer than mean against outliers.
        import statistics
        return {w: round(statistics.median(bs), 6) for w, bs in by_width.items()}

    def mark_failed(self, path: str, reason: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE files SET status='failed', reason=?, error_count=error_count+1
                   WHERE path=?""",
                (reason, path)
            )
            conn.commit()

    def mark_skipped(self, path: str, reason: str):
        self.upsert(path, "skipped", reason=reason)

    def get_status(self, path: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT status FROM files WHERE path=?", (path,)).fetchone()
            return row[0] if row else None

    def get_scan_record(self, path: str) -> Optional[Dict[str, Any]]:
        """Return {status, scan_sig, original_size, reason} for a path, or None.

        Used by the incremental scanner to decide whether a file needs a
        (slow) ffprobe or whether the cached verdict can be reused.
        """
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT status, scan_sig, original_size, reason FROM files WHERE path=?",
                (path,)
            ).fetchone()
            if not row:
                return None
            return {
                "status": row[0],
                "scan_sig": row[1],
                "original_size": row[2],
                "reason": row[3],
            }

    def record_scan(self, path: str, status: str, original_size: Optional[int] = None,
                    reason: Optional[str] = None, scan_sig: Optional[str] = None):
        """Persist a scan verdict (pending/skipped) with its file signature.

        Never downgrades a 'completed' or 'in_progress' row.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO files (path, status, original_size, reason, scan_sig, started_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET
                       status=CASE WHEN files.status IN ('completed','in_progress')
                                   THEN files.status ELSE excluded.status END,
                       original_size=COALESCE(excluded.original_size, files.original_size),
                       reason=COALESCE(excluded.reason, files.reason),
                       scan_sig=excluded.scan_sig""",
                (path, status, original_size, reason, scan_sig, self._now())
            )
            conn.commit()

    def set_scan_sig(self, path: str, scan_sig: str):
        """Backfill the scan signature for an existing row without changing status."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE files SET scan_sig=? WHERE path=?", (scan_sig, path))
            conn.commit()

    def get_pending(self, limit: Optional[int] = None) -> List[str]:
        with sqlite3.connect(self.db_path) as conn:
            sql = "SELECT path FROM files WHERE status IN ('pending', 'failed') ORDER BY id"
            if limit:
                sql += f" LIMIT {limit}"
            rows = conn.execute(sql).fetchall()
            return [r[0] for r in rows]

    def get_stats(self) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            completed = conn.execute(
                "SELECT COUNT(*) FROM files WHERE status='completed'"
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM files WHERE status='failed'"
            ).fetchone()[0]
            skipped = conn.execute(
                "SELECT COUNT(*) FROM files WHERE status='skipped'"
            ).fetchone()[0]
            in_progress = conn.execute(
                "SELECT COUNT(*) FROM files WHERE status='in_progress'"
            ).fetchone()[0]
            size_result = conn.execute(
                "SELECT SUM(original_size), SUM(output_size) FROM files WHERE status='completed'"
            ).fetchone()
            original_size = size_result[0] or 0
            output_size = size_result[1] or 0
            return {
                "total": total,
                "completed": completed,
                "failed": failed,
                "skipped": skipped,
                "in_progress": in_progress,
                "original_size_bytes": original_size,
                "output_size_bytes": output_size,
                "saved_bytes": original_size - output_size,
            }

    def reset_failed(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE files SET status='pending', reason=NULL WHERE status='failed'")
            conn.commit()

    def reset_failed_for_show(self, show_pattern: str):
        """Reset failed entries matching a show path pattern."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE files SET status='pending', reason=NULL WHERE status='failed' AND path LIKE ?",
                (f"%{show_pattern}%",)
            )
            conn.commit()
