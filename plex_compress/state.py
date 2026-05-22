"""SQLite state database for resume and tracking."""

import os
import sqlite3
from datetime import datetime
from typing import List, Optional, Dict, Any

from . import StateError, ResumeError


SCHEMA = """
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
            conn.commit()

    def reset_in_progress(self):
        """Reset any in-progress entries to pending (useful after a crash)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE files SET status='pending' WHERE status='in_progress'")
            conn.commit()

    def _now(self) -> str:
        return datetime.utcnow().isoformat()

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

    def mark_completed(self, path: str, output_size: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE files SET status='completed', output_size=?, completed_at=?
                   WHERE path=?""",
                (output_size, self._now(), path)
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
        self.upsert(path, "skipped", reason=reason)

    def get_status(self, path: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT status FROM files WHERE path=?", (path,)).fetchone()
            return row[0] if row else None

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
