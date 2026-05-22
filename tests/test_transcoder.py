"""Tests for plex_compress.transcoder module."""

import pytest
from plex_compress.config import Config
from plex_compress.transcoder import build_ffmpeg_command, verify_output
from plex_compress import (
    CodecMismatchError,
    ChannelLayoutError,
    DurationError,
    SubtitleLossError,
)


def test_build_ffmpeg_command():
    cfg = Config(
        video_encoder="hevc_videotoolbox",
        video_quality=75,
        audio_encoder="aac",
        audio_bitrate="160k",
        output_container="mkv",
    )
    cmd = build_ffmpeg_command("/tmp/in.mkv", "/tmp/out.mkv", cfg)
    assert cmd[0] == "ffmpeg"
    assert "-i" in cmd
    assert "/tmp/in.mkv" in cmd
    assert "-c:v" in cmd
    assert "hevc_videotoolbox" in cmd
    assert "-q:v" in cmd
    assert "75" in cmd
    assert "-c:a" in cmd
    assert "aac" in cmd
    assert "-b:a" in cmd
    assert "160k" in cmd
    assert "-c:s" in cmd
    assert "copy" in cmd
    assert "/tmp/out.mkv" in cmd


def test_build_ffmpeg_command_nvenc():
    cfg = Config(
        video_encoder="hevc_nvenc",
        video_quality=28,
        video_preset="p4",
        audio_encoder="aac",
        audio_bitrate="160k",
        output_container="mkv",
    )
    cmd = build_ffmpeg_command("/tmp/in.mkv", "/tmp/out.mkv", cfg)
    assert cmd[0] == "ffmpeg"
    assert "-c:v" in cmd
    assert "hevc_nvenc" in cmd
    assert "-cq" in cmd
    assert "28" in cmd
    assert "-preset" in cmd
    assert "p4" in cmd
    assert "-bf" in cmd
    assert "4" in cmd
    assert "-pix_fmt" in cmd
    assert "yuv420p" in cmd
    assert "-rc" in cmd
    assert "vbr" in cmd


def test_verify_output_ok(monkeypatch):
    probe_in = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "duration": "2700"},
            {"codec_type": "audio", "codec_name": "ac3", "channels": 6},
            {"codec_type": "subtitle", "codec_name": "ass"},
        ],
        "format": {"duration": "2700"},
    }
    probe_out = {
        "streams": [
            {"codec_type": "video", "codec_name": "hevc", "duration": "2700"},
            {"codec_type": "audio", "codec_name": "aac", "channels": 2, "channel_layout": "stereo"},
            {"codec_type": "subtitle", "codec_name": "ass"},
        ],
        "format": {"duration": "2700"},
    }

    def mock_probe(path):
        return probe_out if "out" in path else probe_in

    monkeypatch.setattr("plex_compress.transcoder.probe_file", mock_probe)
    cfg = Config()
    verify_output("/tmp/in.mkv", "/tmp/out.mkv", cfg)


def test_verify_output_codec_mismatch(monkeypatch):
    probe_in = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264"},
        ],
        "format": {},
    }
    probe_out = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264"},  # Should be hevc
        ],
        "format": {},
    }

    def mock_probe(path):
        return probe_out if "out" in path else probe_in

    monkeypatch.setattr("plex_compress.transcoder.probe_file", mock_probe)
    cfg = Config(video_encoder="hevc_videotoolbox")
    with pytest.raises(CodecMismatchError):
        verify_output("/tmp/in.mkv", "/tmp/out.mkv", cfg)


def test_verify_output_channel_layout(monkeypatch):
    probe_in = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264"},
            {"codec_type": "audio", "codec_name": "ac3", "channels": 6},
        ],
        "format": {},
    }
    probe_out = {
        "streams": [
            {"codec_type": "video", "codec_name": "hevc"},
            {"codec_type": "audio", "codec_name": "aac", "channels": 6, "channel_layout": "5.1(side)"},
        ],
        "format": {},
    }

    def mock_probe(path):
        return probe_out if "out" in path else probe_in

    monkeypatch.setattr("plex_compress.transcoder.probe_file", mock_probe)
    cfg = Config()
    with pytest.raises(ChannelLayoutError):
        verify_output("/tmp/in.mkv", "/tmp/out.mkv", cfg)


def test_verify_output_duration_tolerance(monkeypatch):
    probe_in = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "duration": "2700"},
        ],
        "format": {"duration": "2700"},
    }
    probe_out = {
        "streams": [
            {"codec_type": "video", "codec_name": "hevc", "duration": "2600"},
        ],
        "format": {"duration": "2600"},
    }

    def mock_probe(path):
        return probe_out if "out" in path else probe_in

    monkeypatch.setattr("plex_compress.transcoder.probe_file", mock_probe)
    cfg = Config(verify_duration_tolerance=2.0)
    with pytest.raises(DurationError):
        verify_output("/tmp/in.mkv", "/tmp/out.mkv", cfg)


def test_verify_output_subtitle_loss(monkeypatch):
    probe_in = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264"},
            {"codec_type": "subtitle", "codec_name": "ass"},
        ],
        "format": {},
    }
    probe_out = {
        "streams": [
            {"codec_type": "video", "codec_name": "hevc"},
        ],
        "format": {},
    }

    def mock_probe(path):
        return probe_out if "out" in path else probe_in

    monkeypatch.setattr("plex_compress.transcoder.probe_file", mock_probe)
    cfg = Config()
    with pytest.raises(SubtitleLossError):
        verify_output("/tmp/in.mkv", "/tmp/out.mkv", cfg)
