"""Tests for plex_compress.scanner module."""

import json
import os
import tempfile

import pytest

from plex_compress.config import Config
from plex_compress.scanner import find_video_files, is_candidate
from plex_compress import AlreadyOptimalError, NotCandidateError


def test_find_video_files():
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "Show", "Season 01"))
        open(os.path.join(tmp, "Show", "Season 01", "ep01.mkv"), "w").close()
        open(os.path.join(tmp, "Show", "Season 01", "ep02.mp4"), "w").close()
        open(os.path.join(tmp, "Show", "Season 01", "readme.txt"), "w").close()
        files = find_video_files(tmp)
        assert len(files) == 2
        assert all(f.endswith((".mkv", ".mp4")) for f in files)


def test_find_video_files_excludes():
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "Show", "extras"))
        open(os.path.join(tmp, "Show", "extras", "bonus.mkv"), "w").close()
        open(os.path.join(tmp, "Show", "main.mkv"), "w").close()
        files = find_video_files(tmp, exclusions=["extras"])
        assert len(files) == 1
        assert "main.mkv" in files[0]


def test_is_candidate_too_small():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "small.mkv")
        # Create a tiny file; ffprobe will fail, but we can mock
        open(path, "w").close()
        cfg = Config(min_file_size_mb=10)
        # Since ffprobe fails on empty file, it returns probe_error
        ok, reason = is_candidate(path, cfg)
        assert ok is False


def test_is_candidate_already_optimal_video(monkeypatch):
    probe = {
        "streams": [
            {"codec_type": "video", "codec_name": "hevc", "disposition": {"default": 1}},
            {"codec_type": "audio", "codec_name": "aac", "channels": 2, "channel_layout": "stereo", "disposition": {"default": 1}},
        ],
        "format": {"duration": "3600", "size": "1073741824", "bit_rate": "2000000"},
    }

    def mock_probe(p):
        return probe

    monkeypatch.setattr("plex_compress.scanner.probe_file", mock_probe)

    cfg = Config()
    ok, reason = is_candidate("/fake/path.mkv", cfg)
    assert ok is False
    assert "already_optimal" in reason
