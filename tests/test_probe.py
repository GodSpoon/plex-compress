"""Tests for plex_compress.probe module."""

import pytest
from plex_compress.probe import (
    get_video_stream,
    get_audio_streams,
    get_default_audio_stream,
    get_subtitle_streams,
    get_duration,
    get_bitrate,
    get_file_size,
)


def test_get_video_stream(sample_probe_data):
    video = get_video_stream(sample_probe_data)
    assert video is not None
    assert video["codec_name"] == "h264"
    assert video["width"] == 1920


def test_get_audio_streams(sample_probe_data):
    audio = get_audio_streams(sample_probe_data)
    assert len(audio) == 1
    assert audio[0]["codec_name"] == "ac3"
    assert audio[0]["channels"] == 6


def test_get_default_audio_stream(sample_probe_data):
    audio = get_default_audio_stream(sample_probe_data)
    assert audio is not None
    assert audio["codec_name"] == "ac3"


def test_get_subtitle_streams(sample_probe_data):
    subs = get_subtitle_streams(sample_probe_data)
    assert len(subs) == 1
    assert subs[0]["codec_name"] == "ass"


def test_get_duration(sample_probe_data):
    dur = get_duration(sample_probe_data)
    assert dur == pytest.approx(2700.0)


def test_get_bitrate(sample_probe_data):
    br = get_bitrate(sample_probe_data)
    assert br == 3100000


def test_get_file_size(sample_probe_data):
    sz = get_file_size(sample_probe_data)
    assert sz == 1048576000


def test_get_video_stream_missing():
    data = {"streams": [{"codec_type": "audio"}]}
    assert get_video_stream(data) is None


def test_get_duration_missing():
    data = {"streams": [], "format": {}}
    assert get_duration(data) is None
