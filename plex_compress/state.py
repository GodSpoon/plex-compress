"""SQLite state database for resume, tracking, and intelligent transcoding decisions."""

import os
import re
import sqlite3
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple

from . import StateError, ResumeError


# Migration-aware schema. Version tracked in _schema_version table.
SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    original_size INTEGER,
    output_size INTEGER,
    reason TEXT,
    started_at TEXT,
    completed_at TEXT,
    error_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_status ON files(status);
CREATE INDEX IF NOT EXISTS idx_path ON files(path);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00',
    ended_at TEXT,
    name TEXT
);
"""

SCHEMA_V2 = """
-- Rich per-file probe metadata for intelligent decisions
ALTER TABLE files ADD COLUMN video_codec TEXT;
ALTER TABLE files ADD COLUMN video_width INTEGER;
ALTER TABLE files ADD COLUMN video_height INTEGER;
ALTER TABLE files ADD COLUMN video_bitrate INTEGER;
ALTER TABLE files ADD COLUMN audio_codec TEXT;
ALTER TABLE files ADD COLUMN audio_channels INTEGER;
ALTER TABLE files ADD COLUMN audio_bitrate INTEGER;
ALTER TABLE files ADD COLUMN duration REAL;
ALTER TABLE files ADD COLUMN container TEXT;
ALTER TABLE files ADD COLUMN file_mtime REAL;
ALTER TABLE files ADD COLUMN file_size INTEGER;
ALTER TABLE files ADD COLUMN predicted_savings_bytes INTEGER;
ALTER TABLE files ADD COLUMN actual_savings_bytes INTEGER;
ALTER TABLE files ADD COLUMN scan_hash TEXT;  -- hash of (mtime,size) for incremental scan

CREATE INDEX IF NOT EXISTS idx_codec ON files(video_codec);
CREATE INDEX IF NOT EXISTS idx_resolution ON files(video_width, video_height);
CREATE INDEX IF NOT EXISTS idx_savings ON files(predicted_savings_bytes);

-- Per-scan snapshots for trend analysis
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scanned_at TEXT NOT NULL,
    total_files INTEGER,
    candidates INTEGER,
    already_optimal INTEGER,
    skipped INTEGER,
    estimated_savings_gb REAL,
    library_path TEXT
);

-- Per-file scan association for change tracking
CREATE TABLE IF NOT EXISTS scan_files (
    scan_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    was_candidate BOOLEAN,
    reason TEXT,
    PRIMARY KEY (scan_id, file_id)
);
"""


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        conn.execute(f"SELECT {column} FROM {table} LIMIT 0")
        return True
    except sqlite3.OperationalError:
        return False


def _migrate(conn: sqlite3.Connection) -> int:
    """Apply migrations and return current schema version."""
    if not _table_exists(conn, "files"):
        conn.executescript(SCHEMA_V1)

    if not _table_exists(conn, "_schema_version"):
        conn.execute("CREATE TABLE _schema_version (version INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO _schema_version (version) VALUES (1)")
        conn.commit()

    row = conn.execute("SELECT version FROM _schema_version LIMIT 1").fetchone()
    version = row[0] if row else 1

    if version < 2:
        # Apply v2 migrations column-by-column (SQLite ALTER TABLE is limited)
        v2_columns = [
            ("video_codec", "TEXT"),
            ("video_width", "INTEGER"),
            ("video_height", "INTEGER"),
            ("video_bitrate", "INTEGER"),
            ("audio_codec", "TEXT"),
            ("audio_channels", "INTEGER"),
            ("audio_bitrate", "INTEGER"),
            ("duration", "REAL"),
            ("container", "TEXT"),
            ("file_mtime", "REAL"),
            ("file_size", "INTEGER"),
            ("predicted_savings_bytes", "INTEGER"),
            ("actual_savings_bytes", "INTEGER"),
            ("scan_hash", "TEXT"),
        ]
        for col, typ in v2_columns:
            if not _column_exists(conn, "files", col):
                conn.execute(f"ALTER TABLE files ADD COLUMN {col} {typ}")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_codec ON files(video_codec)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_resolution ON files(video_width, video_height)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_savings ON files(predicted_savings_bytes)")

        if not _table_exists(conn, "scans"):
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scanned_at TEXT NOT NULL,
                    total_files INTEGER,
                    candidates INTEGER,
                    already_optimal INTEGER,
                    skipped INTEGER,
                    estimated_savings_gb REAL,
                    library_path TEXT
                );
                CREATE TABLE IF NOT EXISTS scan_files (
                    scan_id INTEGER NOT NULL,
                    file_id INTEGER NOT NULL,
                    was_candidate BOOLEAN,
                    reason TEXT,
                    PRIMARY KEY (scan_id, file_id)
                );
            """)

        conn.execute("UPDATE _schema_version SET version = 2")
        conn.commit()
        version = 2

    return version


class StateDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()
        self.reset_in_progress()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            _migrate(conn)

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

    # ------------------------------------------------------------------
    # Rich metadata upsert (v2)
    # ------------------------------------------------------------------

    def upsert_file(
        self,
        path: str,
        status: str,
        original_size: Optional[int] = None,
        output_size: Optional[int] = None,
        reason: Optional[str] = None,
        video_codec: Optional[str] = None,
        video_width: Optional[int] = None,
        video_height: Optional[int] = None,
        video_bitrate: Optional[int] = None,
        audio_codec: Optional[str] = None,
        audio_channels: Optional[int] = None,
        audio_bitrate: Optional[int] = None,
        duration: Optional[float] = None,
        container: Optional[str] = None,
        file_mtime: Optional[float] = None,
        file_size: Optional[int] = None,
        predicted_savings_bytes: Optional[int] = None,
        scan_hash: Optional[str] = None,
    ):
        """Upsert a file with full probe metadata."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO files (
                       path, status, original_size, output_size, reason, started_at,
                       video_codec, video_width, video_height, video_bitrate,
                       audio_codec, audio_channels, audio_bitrate, duration,
                       container, file_mtime, file_size, predicted_savings_bytes, scan_hash
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET
                       status=excluded.status,
                       original_size=COALESCE(excluded.original_size, original_size),
                       output_size=COALESCE(excluded.output_size, output_size),
                       reason=COALESCE(excluded.reason, reason),
                       started_at=COALESCE(excluded.started_at, started_at),
                       video_codec=COALESCE(excluded.video_codec, video_codec),
                       video_width=COALESCE(excluded.video_width, video_width),
                       video_height=COALESCE(excluded.video_height, video_height),
                       video_bitrate=COALESCE(excluded.video_bitrate, video_bitrate),
                       audio_codec=COALESCE(excluded.audio_codec, audio_codec),
                       audio_channels=COALESCE(excluded.audio_channels, audio_channels),
                       audio_bitrate=COALESCE(excluded.audio_bitrate, audio_bitrate),
                       duration=COALESCE(excluded.duration, duration),
                       container=COALESCE(excluded.container, container),
                       file_mtime=COALESCE(excluded.file_mtime, file_mtime),
                       file_size=COALESCE(excluded.file_size, file_size),
                       predicted_savings_bytes=COALESCE(excluded.predicted_savings_bytes, predicted_savings_bytes),
                       scan_hash=COALESCE(excluded.scan_hash, scan_hash)
                """,
                (
                    path, status, original_size, output_size, reason, self._now(),
                    video_codec, video_width, video_height, video_bitrate,
                    audio_codec, audio_channels, audio_bitrate, duration,
                    container, file_mtime, file_size, predicted_savings_bytes, scan_hash,
                )
            )
            conn.commit()

    def get_file_metadata(self, path: str) -> Optional[Dict[str, Any]]:
        """Return all stored metadata for a file."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT * FROM files WHERE path=?""", (path,)
            ).fetchone()
            if row:
                return dict(row)
            return None

    def file_has_changed(self, path: str, mtime: float, size: int) -> bool:
        """Check if file has changed since last scan (for incremental scanning)."""
        scan_hash = f"{mtime}:{size}"
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT scan_hash FROM files WHERE path=?", (path,)
            ).fetchone()
            if row is None:
                return True  # New file
            return row[0] != scan_hash

    def mark_started(self, path: str, original_size: Optional[int] = None):
        self.upsert_file(path, "in_progress", original_size=original_size)

    def mark_completed(self, path: str, output_size: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE files SET status='completed', output_size=?, completed_at=?
                   WHERE path=?""",
                (output_size, self._now(), path)
            )
            conn.commit()
        self._update_actual_savings(path)

    def _update_actual_savings(self, path: str):
        """Compute actual savings from original_size and output_size."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE files SET actual_savings_bytes = COALESCE(original_size, 0) - COALESCE(output_size, 0)
                   WHERE path=?""",
                (path,)
            )
            conn.commit()

    def mark_failed(self, path: str, reason: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE files SET status='failed', reason=?, error_count=error_count+1
                   WHERE path=?""",
                (reason, path)
            )
            conn.commit()

    def mark_skipped(self, path: str, reason: str):
        self.upsert_file(path, "skipped", reason=reason)

    def get_status(self, path: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT status FROM files WHERE path=?", (path,)).fetchone()
            return row[0] if row else None

    def get_pending(self, limit: Optional[int] = None) -> List[str]:
        with sqlite3.connect(self.db_path) as conn:
            sql = """SELECT path FROM files
                     WHERE status IN ('pending', 'failed')
                     ORDER BY COALESCE(predicted_savings_bytes, 0) DESC, id"""
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
            predicted = conn.execute(
                "SELECT SUM(predicted_savings_bytes) FROM files WHERE status='completed'"
            ).fetchone()[0] or 0
            actual = conn.execute(
                "SELECT SUM(actual_savings_bytes) FROM files WHERE status='completed'"
            ).fetchone()[0] or 0
            return {
                "total": total,
                "completed": completed,
                "failed": failed,
                "skipped": skipped,
                "in_progress": in_progress,
                "original_size_bytes": original_size,
                "output_size_bytes": output_size,
                "saved_bytes": original_size - output_size,
                "predicted_savings_bytes": predicted,
                "actual_savings_bytes": actual,
                "prediction_error_bytes": predicted - actual,
            }

    def get_stats_by_codec(self) -> List[Dict[str, Any]]:
        """Return per-video-codec breakdown of completed files."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT video_codec,
                          COUNT(*) as count,
                          SUM(original_size) as original_size,
                          SUM(output_size) as output_size,
                          SUM(actual_savings_bytes) as saved,
                          AVG(actual_savings_bytes) as avg_saved
                   FROM files WHERE status='completed' AND video_codec IS NOT NULL
                   GROUP BY video_codec
                   ORDER BY saved DESC"""
            ).fetchall()
            return [dict(r) for r in rows]

    def get_stats_by_resolution(self) -> List[Dict[str, Any]]:
        """Return per-resolution breakdown."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT video_width, video_height,
                          COUNT(*) as count,
                          SUM(original_size) as original_size,
                          SUM(output_size) as output_size,
                          SUM(actual_savings_bytes) as saved
                   FROM files WHERE status='completed'
                   GROUP BY video_width, video_height
                   ORDER BY saved DESC"""
            ).fetchall()
            return [dict(r) for r in rows]

    def get_top_candidates(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return highest predicted-savings pending files."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT path, video_codec, video_width, video_height,
                          video_bitrate, audio_codec, audio_channels,
                          original_size, predicted_savings_bytes
                   FROM files
                   WHERE status IN ('pending', 'failed')
                   ORDER BY predicted_savings_bytes DESC
                   LIMIT ?""",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def record_scan(self, total_files: int, candidates: int, already_optimal: int,
                    skipped: int, estimated_savings_gb: float, library_path: str) -> int:
        """Record a scan snapshot. Returns scan_id."""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.execute(
                """INSERT INTO scans
                   (scanned_at, total_files, candidates, already_optimal, skipped, estimated_savings_gb, library_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (self._now(), total_files, candidates, already_optimal, skipped, estimated_savings_gb, library_path)
            )
            conn.commit()
            return c.lastrowid or 0

    def record_scan_file(self, scan_id: int, file_id: int, was_candidate: bool, reason: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO scan_files (scan_id, file_id, was_candidate, reason)
                   VALUES (?, ?, ?, ?)""",
                (scan_id, file_id, was_candidate, reason)
            )
            conn.commit()

    def get_scan_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM scans ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def reset_failed(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE files SET status='pending', reason=NULL WHERE status='failed'")
            conn.commit()

    def reset_failed_for_show(self, show_pattern: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE files SET status='pending', reason=NULL WHERE status='failed' AND path LIKE ?",
                (f"%{show_pattern}%",)
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Scan report helpers
    # ------------------------------------------------------------------

    def get_scan_summary(self) -> Dict[str, Any]:
        """Return overall scan summary from files table."""
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] or 0
            candidates = conn.execute(
                "SELECT COUNT(*) FROM files WHERE status IN ('pending', 'failed')"
            ).fetchone()[0] or 0
            already_optimal = conn.execute(
                "SELECT COUNT(*) FROM files WHERE status='skipped' AND reason LIKE '%already_optimal%'"
            ).fetchone()[0] or 0
            skipped = conn.execute(
                "SELECT COUNT(*) FROM files WHERE status='skipped'"
            ).fetchone()[0] or 0
            total_size = conn.execute(
                "SELECT SUM(original_size) FROM files"
            ).fetchone()[0] or 0
            pending_size = conn.execute(
                "SELECT SUM(original_size) FROM files WHERE status IN ('pending', 'failed')"
            ).fetchone()[0] or 0
            completed_size = conn.execute(
                "SELECT SUM(original_size) FROM files WHERE status='completed'"
            ).fetchone()[0] or 0
            saved = conn.execute(
                "SELECT SUM(COALESCE(original_size,0) - COALESCE(output_size,0)) FROM files WHERE status='completed'"
            ).fetchone()[0] or 0
            estimated_savings = conn.execute(
                "SELECT SUM(predicted_savings_bytes) FROM files WHERE status IN ('pending', 'failed')"
            ).fetchone()[0] or 0

        return {
            "total_files": total,
            "candidates": candidates,
            "already_optimal": already_optimal,
            "skipped": skipped,
            "estimated_savings_bytes": estimated_savings,
            "estimated_savings_gb": round(estimated_savings / (1024 ** 3), 1) if estimated_savings else 0.0,
            "total_library_size_bytes": total_size,
            "pending_size_bytes": pending_size,
            "completed_size_bytes": completed_size,
            "saved_so_far_bytes": saved,
        }

    def get_pending_summary(self) -> Dict[str, Any]:
        """Return counts, sizes, predicted savings for pending files."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT COUNT(*) as candidates,
                          SUM(original_size) as pending_size,
                          SUM(predicted_savings_bytes) as estimated_savings
                   FROM files WHERE status IN ('pending', 'failed')"""
            ).fetchone()
        return {
            "candidates": row[0] or 0,
            "pending_size_bytes": row[1] or 0,
            "estimated_savings_bytes": row[2] or 0,
        }

    def get_pending_by_codec(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT video_codec,
                          COUNT(*) as count,
                          SUM(original_size) as pending_size,
                          SUM(predicted_savings_bytes) as predicted_savings
                   FROM files
                   WHERE status IN ('pending', 'failed') AND video_codec IS NOT NULL
                   GROUP BY video_codec
                   ORDER BY predicted_savings DESC"""
            ).fetchall()
            return [dict(r) for r in rows]

    def get_pending_by_resolution(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT video_width, video_height,
                          COUNT(*) as count,
                          SUM(predicted_savings_bytes) as predicted_savings
                   FROM files
                   WHERE status IN ('pending', 'failed')
                     AND video_width IS NOT NULL AND video_height IS NOT NULL
                   GROUP BY video_width, video_height
                   ORDER BY predicted_savings DESC"""
            ).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def _extract_show_name(path: str) -> str:
        parts = path.replace("\\", "/").split("/")
        lower_parts = [p.lower() for p in parts]
        if "tv shows" in lower_parts:
            idx = lower_parts.index("tv shows")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        if "movies" in lower_parts:
            return "Movies"
        return parts[-2] if len(parts) >= 2 else "unknown"

    def get_pending_by_show(self, limit: int = 20) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            all_rows = conn.execute("SELECT path FROM files").fetchall()
            pending_rows = conn.execute(
                """SELECT path, original_size, predicted_savings_bytes
                   FROM files WHERE status IN ('pending', 'failed')"""
            ).fetchall()

        show_totals: Dict[str, int] = {}
        for r in all_rows:
            name = self._extract_show_name(r["path"])
            show_totals[name] = show_totals.get(name, 0) + 1

        shows: Dict[str, Dict[str, Any]] = {}
        for r in pending_rows:
            name = self._extract_show_name(r["path"])
            if name not in shows:
                shows[name] = {
                    "name": name,
                    "total": show_totals.get(name, 0),
                    "pending": 0,
                    "pending_size": 0,
                    "predicted_savings": 0,
                }
            shows[name]["pending"] += 1
            shows[name]["pending_size"] += r["original_size"] or 0
            shows[name]["predicted_savings"] += r["predicted_savings_bytes"] or 0

        sorted_shows = sorted(shows.values(), key=lambda x: -x["predicted_savings"])
        return sorted_shows[:limit]

    @staticmethod
    def _infer_media_type(path: str) -> str:
        lower = path.lower()
        basename = os.path.basename(path)
        if "tv shows" in lower or re.search(r"S\d{2}E\d{2}", basename, re.IGNORECASE):
            return "tv_shows"
        if "movies" in lower:
            return "movies"
        return "other"

    def get_media_type_breakdown(self) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT path, original_size, predicted_savings_bytes
                   FROM files WHERE status IN ('pending', 'failed')"""
            ).fetchall()

        result = {
            "tv_shows": {"count": 0, "size_bytes": 0, "pending_savings_bytes": 0},
            "movies": {"count": 0, "size_bytes": 0, "pending_savings_bytes": 0},
            "other": {"count": 0, "size_bytes": 0, "pending_savings_bytes": 0},
        }
        for r in rows:
            mtype = self._infer_media_type(r["path"])
            result[mtype]["count"] += 1
            result[mtype]["size_bytes"] += r["original_size"] or 0
            result[mtype]["pending_savings_bytes"] += r["predicted_savings_bytes"] or 0
        return result

    def get_transcoding_velocity(self) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT started_at, completed_at, actual_savings_bytes
                   FROM files
                   WHERE status='completed'
                     AND started_at IS NOT NULL
                     AND completed_at IS NOT NULL
                   ORDER BY completed_at DESC
                   LIMIT 50"""
            ).fetchall()

        if not rows:
            return {"gb_per_hour": 0.0, "files_per_hour": 0.0, "avg_seconds_per_file": 0.0}

        total_seconds = 0.0
        total_savings = 0
        valid_count = 0

        for r in rows:
            try:
                started = datetime.fromisoformat(r["started_at"])
                completed = datetime.fromisoformat(r["completed_at"])
                delta = (completed - started).total_seconds()
                if delta > 0:
                    total_seconds += delta
                    total_savings += r["actual_savings_bytes"] or 0
                    valid_count += 1
            except (ValueError, TypeError):
                continue

        if valid_count == 0 or total_seconds == 0:
            return {"gb_per_hour": 0.0, "files_per_hour": 0.0, "avg_seconds_per_file": 0.0}

        hours = total_seconds / 3600.0
        gb = total_savings / (1024 ** 3)
        gb_per_hour = gb / hours if hours > 0 else 0.0
        files_per_hour = valid_count / hours if hours > 0 else 0.0
        avg_seconds = total_seconds / valid_count

        return {
            "gb_per_hour": round(gb_per_hour, 2),
            "files_per_hour": round(files_per_hour, 2),
            "avg_seconds_per_file": round(avg_seconds, 1),
        }

    def get_time_stats(self) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT started_at, completed_at
                   FROM files
                   WHERE status='completed'
                     AND started_at IS NOT NULL
                     AND completed_at IS NOT NULL"""
            ).fetchall()
            session_count = conn.execute(
                "SELECT COUNT(*) FROM sessions"
            ).fetchone()[0] or 0

        total_seconds = 0.0
        for r in rows:
            try:
                started = datetime.fromisoformat(r["started_at"])
                completed = datetime.fromisoformat(r["completed_at"])
                delta = (completed - started).total_seconds()
                if delta > 0:
                    total_seconds += delta
            except (ValueError, TypeError):
                continue

        return {
            "total_transcode_seconds": int(total_seconds),
            "total_transcode_hours": round(total_seconds / 3600.0, 1),
            "session_count": session_count,
        }

    def get_eta(self, velocity_gb_per_hour: float) -> Dict[str, Any]:
        pending = self.get_pending_summary()
        pending_gb = pending["pending_size_bytes"] / (1024 ** 3)
        hours = pending_gb / velocity_gb_per_hour if velocity_gb_per_hour > 0 else None
        return {
            "pending_files": pending["candidates"],
            "pending_size_gb": round(pending_gb, 1),
            "hours_remaining": round(hours, 1) if hours is not None else None,
            "days_remaining": round(hours / 24.0, 1) if hours is not None else None,
        }

    # ------------------------------------------------------------------
    # Legacy compatibility
    # ------------------------------------------------------------------

    def upsert(self, path: str, status: str, original_size: Optional[int] = None,
               output_size: Optional[int] = None, reason: Optional[str] = None):
        """Backward-compatible upsert without metadata."""
        self.upsert_file(path, status, original_size=original_size,
                         output_size=output_size, reason=reason)
