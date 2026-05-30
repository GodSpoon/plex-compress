#!/usr/bin/env python3
"""Plex Compress dashboard — backward-compatible launcher.

Uses the new plex_compress.webui module.
"""

import sys
import os

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from plex_compress.webui.server import main

if __name__ == "__main__":
    main()
