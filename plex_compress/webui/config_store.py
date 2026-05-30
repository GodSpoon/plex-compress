"""Persistent JSON configuration store for the Web UI."""

import json
import os
from typing import Any, Dict

from ..config import Config

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.plex_compress/webui.json")

DEFAULT_WEBUI_CONFIG: Dict[str, Any] = {
    "library_path": "",
    "temp_dir": os.path.expanduser("~/tmp/plex_compress"),
    "state_db_path": os.path.expanduser("~/.plex_compress/state.db"),
    "log_path": os.path.expanduser("~/.plex_compress/webui.log"),
    "video_encoder": "libx265",
    "video_quality": 28,
    "video_preset": "medium",
    "audio_bitrate": "160k",
    "parallel_jobs": 1,
    "keep_backup": False,
    "dry_run": True,
    "verbose": False,
    "output_dir": None,
    "include_pattern": None,
    "force": False,
    "exclusions": [],
    "verify_checksum": True,
    "min_file_age_seconds": 300.0,
    "enable_file_locking": True,
    "post_replace_verify": True,
    "watch_interval": 60.0,
    "intelligent_scan": True,
    "limit": None,
    "single_file": None,
    "ui_theme": "dark",
    "ui_refresh_interval": 5000,
}


class ConfigStore:
    """Simple JSON-backed config store with Config dataclass integration."""

    def __init__(self, path: str = DEFAULT_CONFIG_PATH):
        self.path = path
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}
        # Merge defaults for any missing keys
        for key, value in DEFAULT_WEBUI_CONFIG.items():
            if key not in self._data:
                self._data[key] = value
        self._save()

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, default=str)

    def get(self, key: str, default=None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any):
        self._data[key] = value
        self._save()

    def update(self, updates: Dict[str, Any]):
        self._data.update(updates)
        self._save()

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)

    def to_config(self) -> Config:
        """Build a plex_compress Config dataclass from stored settings."""
        cfg = Config()
        for key, value in self._data.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        # Ensure paths are absolute
        if cfg.library_path:
            cfg.library_path = os.path.abspath(cfg.library_path)
        cfg.temp_dir = os.path.abspath(cfg.temp_dir)
        if cfg.output_dir:
            cfg.output_dir = os.path.abspath(cfg.output_dir)
        if cfg.single_file:
            cfg.single_file = os.path.abspath(cfg.single_file)
        os.makedirs(cfg.temp_dir, exist_ok=True)
        os.makedirs(os.path.dirname(cfg.state_db_path), exist_ok=True)
        return cfg
