"""Tests for plex_compress.scanner module."""

import json
import os
import tempfile

import pytest

from plex_compress.config import Config
from plex_compress.scanner import find_video_files, find_video_files_with_sig, is_candidate, scan_library
from plex_compress.state import StateDB
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
        ok, reason, _probe = is_candidate(path, cfg)
        assert ok is False


def test_find_video_files_with_sig():
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "Show"))
        p = os.path.join(tmp, "Show", "ep01.mkv")
        with open(p, "w") as f:
            f.write("x" * 100)
        results = find_video_files_with_sig(tmp)
        assert len(results) == 1
        path, sig = results[0]
        assert path == p
        assert sig is not None and ":" in sig
        assert sig.endswith(":100")


def test_scan_is_incremental(tmp_path, monkeypatch):
    """Second scan of an unchanged library must not re-probe any file."""
    lib = tmp_path / "lib" / "Show" / "Season 01"
    lib.mkdir(parents=True)
    f = lib / "ep01.mkv"
    f.write_bytes(b"x" * 1000)

    db = StateDB(str(tmp_path / "state.db"))
    cfg = Config(library_path=str(tmp_path / "lib"), state_db_path=str(tmp_path / "state.db"))

    probe_calls = {"n": 0}
    real_is_candidate = is_candidate

    def counting_is_candidate(path, c):
        probe_calls["n"] += 1
        return False, "too_small (mock)", {"format": {"size": "1000"}}

    monkeypatch.setattr("plex_compress.scanner.is_candidate", counting_is_candidate)

    # First scan probes the new file once and records the verdict + signature.
    scan_library(cfg, state=db, force=False)
    assert probe_calls["n"] == 1

    # Second scan: unchanged file -> verdict served from cache, no probe.
    scan_library(cfg, state=db, force=False)
    assert probe_calls["n"] == 1

    # Modifying the file changes its signature -> it gets re-probed.
    f.write_bytes(b"y" * 2000)
    scan_library(cfg, state=db, force=False)
    assert probe_calls["n"] == 2


def test_probe_errors_are_never_cached(tmp_path, monkeypatch):
    """A probe error (possibly transient) must be retried on every scan, not
    cached as a permanent skip, and must be surfaced in report['errors']."""
    lib = tmp_path / "lib" / "Movies" / "Broken (2000)"
    lib.mkdir(parents=True)
    f = lib / "broken.mkv"
    f.write_bytes(b"\x00" * 1000)

    db = StateDB(str(tmp_path / "state.db"))
    cfg = Config(library_path=str(tmp_path / "lib"), state_db_path=str(tmp_path / "state.db"))

    calls = {"n": 0}

    def err_is_candidate(path, c):
        calls["n"] += 1
        return False, "probe_error: moov atom not found", None

    monkeypatch.setattr("plex_compress.scanner.is_candidate", err_is_candidate)

    r1 = scan_library(cfg, state=db, force=False)
    assert calls["n"] == 1
    assert len(r1["errors"]) == 1

    # Second scan: probe error is NOT cached -> file is probed again.
    r2 = scan_library(cfg, state=db, force=False)
    assert calls["n"] == 2
    assert len(r2["errors"]) == 1


def test_full_scan_ignores_skip_cache(tmp_path, monkeypatch):
    """Full scan re-probes stable-skipped files; incremental reuses the cache."""
    lib = tmp_path / "lib" / "Movies" / "Small (2000)"
    lib.mkdir(parents=True)
    f = lib / "small.mkv"
    f.write_bytes(b"x" * 1000)

    db = StateDB(str(tmp_path / "state.db"))
    cfg = Config(library_path=str(tmp_path / "lib"), state_db_path=str(tmp_path / "state.db"))

    calls = {"n": 0}

    def small_is_candidate(path, c):
        calls["n"] += 1
        return False, "too_small (1.0 MB)", {"format": {"size": "1000"}}

    monkeypatch.setattr("plex_compress.scanner.is_candidate", small_is_candidate)

    scan_library(cfg, state=db, force=False)
    assert calls["n"] == 1
    # Incremental reuses the stable skip -> no re-probe.
    scan_library(cfg, state=db, force=False)
    assert calls["n"] == 1
    # Full scan re-probes regardless of cache.
    scan_library(cfg, state=db, force=False, full_scan=True)
    assert calls["n"] == 2


def test_completed_is_durable_truth(tmp_path, monkeypatch):
    """A completed file is never re-probed, even on a full scan."""
    lib = tmp_path / "lib" / "Movies" / "Done (2000)"
    lib.mkdir(parents=True)
    f = lib / "done.mkv"
    f.write_bytes(b"x" * 1000)

    db = StateDB(str(tmp_path / "state.db"))
    db.mark_started(str(f))            # real pipeline always marks started first
    db.mark_completed(str(f), output_size=500)
    cfg = Config(library_path=str(tmp_path / "lib"), state_db_path=str(tmp_path / "state.db"))

    calls = {"n": 0}

    def boom(path, c):
        calls["n"] += 1
        return True, "candidate", {"format": {"size": "1000"}}

    monkeypatch.setattr("plex_compress.scanner.is_candidate", boom)

    r = scan_library(cfg, state=db, force=False, full_scan=True)
    assert calls["n"] == 0           # completed file never probed
    assert str(f) not in r["candidates"]


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
    ok, reason, _probe = is_candidate("/fake/path.mkv", cfg)
    assert ok is False
    assert "already_optimal" in reason
