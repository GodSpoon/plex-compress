"""Entry point for python -m plex_compress.webui."""

import sys
from .server import main

if __name__ == "__main__":
    sys.exit(main())
