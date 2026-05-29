"""Tests for plex_compress.intelligence module."""

import json
import os
import sqlite3
import tempfile

import pytest

from plex_compress.config import Config
from plex_compress.intelligence import (
    predict_savings_bytes,
    _extract_probe_metadata,
    _compute_scan_hash,
    is_candidate_intelligent,
    scan_library_intelligent,
    get_priority_queue,
    generate_report,
)
from plex_compress.state import StateDB


class TestPredictSavingsBytes:
    def test_h264_medium_bitrate(self):
        # 3 Mbps H.264, 45 min episode, ~1 GB file
        predicted = predict_savings_bytes(
            video_codec="h264",
            video_bitrate=3_000_000,
            file_size=1_073_741_824,
            duration=2700,
            audio_channels=6,
        )
        # 40% of 1GB = ~429MB video savings + audio savings
        assert predicted > 400_000_000

    def test_h264_high_bitrate(self):
        # 8 Mbps H.264 should get higher ratio
        low = predict_savings_bytes(
            video_codec="h264",
            video_bitrate=3_000_000,
            file_size=1_073_741_824,
            duration=2700,
            audio_channels=6,
        )
        high = predict_savings_bytes(
            video_codec="h264",
            video_bitrate=8_000_000,
            file_size=2_000_000_000,
            duration=2700,
            audio_channels=6,
        )
        # Higher bitrate should have higher ratio
        assert high > low

    def test_hevc_returns_lower_savings(self):
        # HEVC not in model, falls back to default
        predicted = predict_savings_bytes(
            video_codec="hevc",
            video_bitrate=3_000_000,
            file_size=1_073_741_824,
            duration=2700,
            audio_channels=6,
        )
        assert predicted > 0

    def test_stereo_no_audio_savings(self):
        # Already stereo: no audio savings component
        stereo = predict_savings_bytes(
            video_codec="h264",
            video_bitrate=3_000_000,
            file_size=1_073_741_824,
            duration=2700,
            audio_channels=2,
        )
        surround = predict_savings_bytes(
            video_codec="h264",
            video_bitrate=3_000_000,
            file_size=1_073_741_824,
            duration=2700,
            audio_channels=6,
        )
        assert surround > stereo

    def test_fallback_no_bitrate(self):
        # No bitrate/duration: fallback to 40% of file size
        predicted = predict_savings_bytes(
            video_codec="h264",
            video_bitrate=None,
            file_size=1_000_000_000,
            duration=None,
            audio_channels=6,
        )
        assert predicted == 400_000_000

    def test_no_data_returns_zero(self):
        predicted = predict_savings_bytes(
            video_codec="h264",
            video_bitrate=None,
            file_size=None,
            duration=None,
            audio_channels=6,
        )
        assert predicted == 0


class TestExtractProbeMetadata:
    def test_extracts_all_fields(self):
        probe = {
            "streams": [
                {
                    "index": 0,
                    "codec_name": "h264",
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "bit_rate": "2500000",
                    "disposition": {"default": 1},
                },
                {
                    "index": 1,
                    "codec_name": "ac3",
                    "codec_type": "audio",
                    "channels": 6,
                    "channel_layout": "5.1(side)",
                    "bit_rate": "384000",
                    "disposition": {"default": 1},
                },
                {
                    "index": 2,
                    "codec_name": "ass",
                    "codec_type": "subtitle",
                    "disposition": {"default": 1},
                },
            ],
            "format": {
                "filename": "/test/show.mkv",
                "duration": "2700.000000",
                "size": "1048576000",
                "bit_rate": "3100000",
            },
        }
        meta = _extract_probe_metadata(probe, "/test/show.mkv")
        assert meta["video_codec"] == "h264"
        assert meta["video_width"] == 1920
        assert meta["video_height"] == 1080
        assert meta["video_bitrate"] == 3100000
        assert meta["audio_codec"] == "ac3"
        assert meta["audio_channels"] == 6
        assert meta["audio_bitrate"] == 384000
        assert meta["duration"] == 2700.0
        assert meta["container"] == "mkv"
        assert meta["file_size"] == 1048576000
        assert meta["audio_stream_count"] == 1

    def test_no_video_stream(self):
        probe = {
            "streams": [
                {"codec_type": "audio", "codec_name": "aac", "channels": 2},
            ],
            "format": {"duration": "100", "size": "1000000"},
        }
        meta = _extract_probe_metadata(probe, "/test/audio_only.m4a")
        assert meta["video_codec"] is None
        assert meta["audio_codec"] == "aac"


class TestComputeScanHash:
    def test_returns_hash(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test content")
            path = f.name
        h = _compute_scan_hash(path)
        assert h is not None
        assert ":" in h
        os.unlink(path)

    def test_missing_file_returns_none(self):
        h = _compute_scan_hash("/nonexistent/path/file.mkv")
        assert h is None


class TestIsCandidateIntelligent:
    def test_skips_too_small(self, monkeypatch):
        probe = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                {"codec_type": "audio", "codec_name": "ac3", "channels": 6, "disposition": {"default": 1}},
            ],
            "format": {"duration": "3600", "size": "10485760", "bit_rate": "2000000"},
        }
        monkeypatch.setattr("plex_compress.intelligence.probe_file", lambda p: probe)

        cfg = Config(min_file_size_mb=100)
        ok, reason, _probe, meta = is_candidate_intelligent("/fake/small.mkv", cfg)
        assert ok is False
        assert "too_small" in reason
        assert meta is not None

    def test_skips_already_optimal(self, monkeypatch):
        probe = {
            "streams": [
                {"codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080},
                {"codec_type": "audio", "codec_name": "aac", "channels": 2, "channel_layout": "stereo", "disposition": {"default": 1}},
            ],
            "format": {"duration": "3600", "size": "1073741824", "bit_rate": "2000000"},
        }
        monkeypatch.setattr("plex_compress.intelligence.probe_file", lambda p: probe)

        cfg = Config()
        ok, reason, _probe, meta = is_candidate_intelligent("/fake/optimal.mkv", cfg)
        assert ok is False
        assert "already_optimal" in reason

    def test_candidate_with_predicted_savings(self, monkeypatch):
        probe = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "bit_rate": "3000000"},
                {"codec_type": "audio", "codec_name": "ac3", "channels": 6, "bit_rate": "384000", "disposition": {"default": 1}},
            ],
            "format": {"duration": "2700", "size": "1048576000", "bit_rate": "3500000"},
        }
        monkeypatch.setattr("plex_compress.intelligence.probe_file", lambda p: probe)

        cfg = Config()
        ok, reason, _probe, meta = is_candidate_intelligent("/fake/candidate.mkv", cfg)
        assert ok is True
        assert meta is not None
        assert "predicted_savings_bytes" in meta
        assert meta["predicted_savings_bytes"] > 0

    def test_incremental_scan_skips_unchanged(self, monkeypatch, tmp_path):
        db_path = tmp_path / "test.db"
        state = StateDB(str(db_path))

        # Pre-populate with a completed entry
        state.upsert_file(
            path="/fake/completed.mkv",
            status="completed",
            file_mtime=1000.0,
            file_size=1000000,
            scan_hash="1000.0:1000000",
        )

        # Mock os.stat to return matching mtime/size
        class FakeStat:
            st_mtime = 1000.0
            st_size = 1000000
            st_mode = 0o100644

        monkeypatch.setattr("plex_compress.intelligence.os.stat", lambda p: FakeStat())
        monkeypatch.setattr("plex_compress.intelligence.os.path.exists", lambda p: True)

        cfg = Config(temp_dir=str(tmp_path / "temp2"), state_db_path=str(tmp_path / "subdir" / "state.db"))
        ok, reason, _probe, meta = is_candidate_intelligent("/fake/completed.mkv", cfg, state=state)
        assert ok is False
        assert "already_completed" in reason


class TestScanLibraryIntelligent:
    def test_scans_and_persists_metadata(self, monkeypatch, tmp_path):
        db_path = tmp_path / "test.db"
        state = StateDB(str(db_path))

        # Create a temp directory with one video file
        lib_dir = tmp_path / "library"
        lib_dir.mkdir()
        video_file = lib_dir / "episode.mkv"
        video_file.write_text("fake video content")

        probe = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "bit_rate": "3000000"},
                {"codec_type": "audio", "codec_name": "ac3", "channels": 6, "bit_rate": "384000", "disposition": {"default": 1}},
            ],
            "format": {"duration": "2700", "size": "1048576000", "bit_rate": "3500000"},
        }
        monkeypatch.setattr("plex_compress.intelligence.probe_file", lambda p: probe)

        cfg = Config(library_path=str(lib_dir))
        report = scan_library_intelligent(cfg, state=state)

        assert report["total_files"] == 1
        assert len(report["candidates"]) == 1
        assert report["estimated_savings_gb"] > 0
        assert "by_codec" in report
        assert "h264" in report["by_codec"]

        # Verify metadata was persisted
        meta = state.get_file_metadata(str(video_file))
        assert meta is not None
        assert meta["video_codec"] == "h264"
        assert meta["video_width"] == 1920
        assert meta["predicted_savings_bytes"] > 0

    def test_respects_force_flag(self, monkeypatch, tmp_path):
        db_path = tmp_path / "test.db"
        state = StateDB(str(db_path))

        lib_dir = tmp_path / "library"
        lib_dir.mkdir()
        video_file = lib_dir / "episode.mkv"
        video_file.write_text("fake video content")

        # Pre-populate as completed
        state.upsert_file(
            path=str(video_file),
            status="completed",
            video_codec="h264",
        )

        probe = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                {"codec_type": "audio", "codec_name": "ac3", "channels": 6, "disposition": {"default": 1}},
            ],
            "format": {"duration": "2700", "size": "1048576000", "bit_rate": "3500000"},
        }
        monkeypatch.setattr("plex_compress.intelligence.probe_file", lambda p: probe)

        cfg = Config(library_path=str(lib_dir))

        # Without force: should skip
        report = scan_library_intelligent(cfg, state=state, force=False)
        assert len(report["candidates"]) == 0

        # With force: should include
        report = scan_library_intelligent(cfg, state=state, force=True)
        assert len(report["candidates"]) == 1


class TestPriorityQueue:
    def test_returns_candidates_sorted_by_savings(self, tmp_path):
        db_path = tmp_path / "test.db"
        state = StateDB(str(db_path))

        # Insert pending files with different predicted savings
        state.upsert_file("/fake/small.mkv", "pending", predicted_savings_bytes=100_000_000)
        state.upsert_file("/fake/large.mkv", "pending", predicted_savings_bytes=500_000_000)
        state.upsert_file("/fake/medium.mkv", "pending", predicted_savings_bytes=250_000_000)

        queue = get_priority_queue(state, limit=10)
        assert len(queue) == 3
        # Should be sorted by predicted_savings_bytes DESC
        assert queue[0]["path"] == "/fake/large.mkv"
        assert queue[1]["path"] == "/fake/medium.mkv"
        assert queue[2]["path"] == "/fake/small.mkv"


class TestGenerateReport:
    def test_report_contains_all_sections(self, tmp_path):
        db_path = tmp_path / "test.db"
        state = StateDB(str(db_path))

        # Insert some completed files
        state.upsert_file(
            "/fake/show1.mkv", "completed",
            original_size=1_000_000_000, output_size=600_000_000,
            video_codec="h264", video_width=1920, video_height=1080,
        )
        state.upsert_file(
            "/fake/show2.mkv", "completed",
            original_size=2_000_000_000, output_size=1_200_000_000,
            video_codec="h264", video_width=1920, video_height=1080,
        )
        state.upsert_file(
            "/fake/show3.mkv", "pending",
            predicted_savings_bytes=400_000_000,
            video_codec="mpeg4", video_width=1280, video_height=720,
        )

        # Mark as completed properly
        state.mark_completed("/fake/show1.mkv", 600_000_000)
        state.mark_completed("/fake/show2.mkv", 1_200_000_000)

        report = generate_report(state)

        assert "summary" in report
        assert "by_codec" in report
        assert "by_resolution" in report
        assert "top_pending" in report
        assert "scan_history" in report

        summary = report["summary"]
        assert summary["completed"] == 2
        assert summary["saved_bytes"] == 1_200_000_000

        # Check by_codec has h264
        codecs = {r["video_codec"] for r in report["by_codec"]}
        assert "h264" in codecs

        # Check top_pending has the pending file
        assert len(report["top_pending"]) == 1
        assert report["top_pending"][0]["path"] == "/fake/show3.mkv"
