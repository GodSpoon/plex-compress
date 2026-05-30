"""Entry point: python -m plex_compress.webui"""

import sys
import os

# Ensure repo root is on path for absolute imports
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from .server import main

if __name__ == "__main__":
    main()
