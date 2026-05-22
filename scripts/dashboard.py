#!/usr/bin/env python3
"""Plex Compress dashboard — standalone web UI.

Serves a real-time dashboard at http://HOST:8765/
Reads from ~/.plex_compress/state.db and transcode.log.

Usage:
    python3 scripts/dashboard.py
    python3 scripts/dashboard.py --port 8080 --host 0.0.0.0
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path


DEFAULT_DB = os.path.expanduser("~/.plex_compress/state.db")
DEFAULT_LOG = os.path.expanduser("~/.plex_compress/transcode.log")
WEB_DIR = Path(__file__).parent / "web"


def _db_query(db_path: str):
    """Return dashboard data from state DB."""
    if not os.path.exists(db_path):
        return {
            "stats": {"total": 0, "completed": 0, "failed": 0, "in_progress": 0,
                      "skipped": 0, "saved_bytes": 0},
            "recent": [],
            "failed": [],
            "shows": [],
            "currently_running": None,
        }

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Overall stats
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

    # Currently running (most recent in_progress)
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

    # Recent completed
    c.execute(
        "SELECT path, status, original_size, output_size, completed_at FROM files WHERE status='completed' ORDER BY completed_at DESC LIMIT 20"
    )
    recent = [dict(r) for r in c.fetchall()]

    # Failed files
    c.execute(
        "SELECT path, status, original_size, output_size, reason FROM files WHERE status='failed' ORDER BY id DESC LIMIT 20"
    )
    failed_rows = [dict(r) for r in c.fetchall()]

    # Per-show breakdown
    c.execute("SELECT path, status, original_size, output_size FROM files WHERE status='completed'")
    shows_data = {}
    for r in c.fetchall():
        path = r["path"]
        parts = path.split("/")
        show = parts[-3] if len(parts) >= 3 else "unknown"
        if show not in shows_data:
            shows_data[show] = {"total": 0, "completed": 0, "saved": 0}
        shows_data[show]["total"] += 1
        shows_data[show]["completed"] += 1
        orig = r["original_size"] or 0
        out = r["output_size"] or 0
        shows_data[show]["saved"] += (orig - out)

    # Also count tracked files per show (includes pending/failed)
    c.execute("SELECT path FROM files")
    for r in c.fetchall():
        path = r["path"]
        parts = path.split("/")
        show = parts[-3] if len(parts) >= 3 else "unknown"
        if show not in shows_data:
            shows_data[show] = {"total": 0, "completed": 0, "saved": 0}
        shows_data[show]["total"] += 1

    shows = sorted(
        [{"name": k, **v} for k, v in shows_data.items()],
        key=lambda x: -x["saved"]
    )

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
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Suppress default request logging
        pass

    def _json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body, status=200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            index_path = WEB_DIR / "index.html"
            if index_path.exists():
                self._html(index_path.read_text())
            else:
                self._html("Dashboard UI not found.", 404)
        elif self.path == "/api/status":
            data = _db_query(DEFAULT_DB)
            self._json(data)
        else:
            self.send_response(404)
            self.end_headers()


def main():
    parser = argparse.ArgumentParser(description="Plex Compress Dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=8765, help="Port")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), Handler)
    print(f"Dashboard running at http://{args.host}:{args.port}/")
    print(f"Reading state from {DEFAULT_DB}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
