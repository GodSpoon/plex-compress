"""Tests for plex_compress.audio module."""

from plex_compress.config import Config
from plex_compress.audio import build_audio_filter, build_audio_encoder_args


def test_build_audio_filter_default():
    cfg = Config()
    filt = build_audio_filter(cfg)
    assert "loudnorm" in filt
    assert "I=-16.0" in filt
    assert "TP=-1.5" in filt
    assert "LRA=11.0" in filt


def test_build_audio_filter_rfc7845():
    cfg = Config(use_rfc7845_downmix=True)
    filt = build_audio_filter(cfg)
    assert "pan=stereo" in filt
    assert "0.374107" in filt
    assert "loudnorm" in filt


def test_build_audio_encoder_args_default():
    cfg = Config()
    args = build_audio_encoder_args(cfg)
    assert "-c:a" in args
    assert "aac" in args
    assert "-b:a" in args
    assert "160k" in args
    assert "-ac" in args
    assert "2" in args
    assert "-af" in args


def test_build_audio_encoder_args_rfc():
    cfg = Config(use_rfc7845_downmix=True)
    args = build_audio_encoder_args(cfg)
    assert "-af" in args
    assert "-ac" not in args
