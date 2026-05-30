"""Simple extension/plugin discovery system for the Web UI."""

import importlib.util
import os
import sys
from typing import Callable, Dict, List

DEFAULT_EXTENSIONS_DIR = os.path.expanduser("~/.plex_compress/webui/extensions")


class ExtensionManager:
    """Discovers and loads .py extensions from the extensions directory."""

    def __init__(self, app, extensions_dir: str = DEFAULT_EXTENSIONS_DIR):
        self.app = app
        self.extensions_dir = extensions_dir
        self.extensions: List[Dict] = []
        self._load_all()

    def _load_all(self):
        if not os.path.isdir(self.extensions_dir):
            return
        for fname in sorted(os.listdir(self.extensions_dir)):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            path = os.path.join(self.extensions_dir, fname)
            try:
                self._load_file(fname, path)
            except Exception as e:
                self.extensions.append({
                    "name": fname,
                    "loaded": False,
                    "error": str(e),
                })

    def _load_file(self, fname: str, path: str):
        name = f"plex_compress_webui_ext_{fname[:-3]}"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load spec for {path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)

        if hasattr(mod, "register"):
            mod.register(self.app)
            self.extensions.append({
                "name": fname[:-3],
                "loaded": True,
                "error": None,
            })
        else:
            self.extensions.append({
                "name": fname[:-3],
                "loaded": False,
                "error": "No register(app) function found",
            })
