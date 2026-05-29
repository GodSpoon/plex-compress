#!/usr/bin/env python3
"""Convenience launcher for the Plex Compress Web UI.

Usage:
    python3 scripts/webui.py
    python3 scripts/webui.py --port 8080 --host 0.0.0.0
"""

import sys
import os

# Ensure repo root is on path
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from plex_compress.webui.server import main

if __name__ == "__main__":
    main()
