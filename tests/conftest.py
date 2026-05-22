"""Pytest fixtures and configuration."""

import json
import os
import pytest

from plex_compress.config import Config


@pytest.fixture
def sample_probe_data():
    path = os.path.join(os.path.dirname(__file__), "fixtures", "sample_probe.json")
    with open(path) as f:
        return json.load(f)


@pytest.fixture
def default_config():
    return Config(
        library_path="/tmp/test_lib",
        temp_dir="/tmp/test_temp",
        state_db_path="/tmp/test_state.db",
        dry_run=True,
    )
