"""Encoder calibration: measure achieved fps on this system.

A 30-second transcode of a real (or synthetic) file is run, and the
`-progress pipe:1` output is parsed for steady-state fps samples. The
median of the steady-state window is the encoder's true throughput at
the calibration resolution.

Calibrations are cached in `~/.plex_compress/calibration.json`, keyed by
`f"{encoder}|{quality}|{preset}|{width}x{height}"`. Stale entries are not
auto-evicted; the cache file is small.

Hardware encoders (hevc_videotoolbox, hevc_nvenc) are essentially
pixel-rate bound, so achieved fps at a different resolution can be
extrapolated linearly by `src_pixels / cal_pixels`.
"""

import json
import os
import statistics
import subprocess
import tempfile
import time
from typing import Any, Dict, Optional

from .config import Config
from .probe import probe_file, get_video_stream_meta


CALIBRATION_PATH = os.path.expanduser("~/.plex_compress/calibration.json")

# Default fps assumptions when no calibration is available yet.
# These are conservative starting points for back-of-envelope estimates.
DEFAULT_FPS = {
    "hevc_videotoolbox": 120.0,   # M-series VideoToolbox is fast
    "hevc_nvenc": 100.0,           # Turing/Ampere NVENC typical 1080p
    "libx265": 5.0,                # software CRF 28 medium preset
    "libx264": 15.0,               # software, not used here
}


def _calibration_key(encoder: str, quality: int, preset: str, width: int, height: int) -> str:
    return f"{encoder}|{quality}|{preset}|{width}x{height}"


def _load_cache() -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(CALIBRATION_PATH):
        return {}
    try:
        with open(CALIBRATION_PATH, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except (OSError, ValueError):
        return {}


def _save_cache(cache: Dict[str, Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(CALIBRATION_PATH), exist_ok=True)
    try:
        with open(CALIBRATION_PATH, "w") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
    except OSError:
        pass  # best-effort cache


def get_cached_calibration(cfg: Config, width: int = 1920, height: int = 1080) -> Optional[Dict[str, Any]]:
    """Return the cached calibration for this encoder+quality+preset, or None."""
    key = _calibration_key(cfg.video_encoder, cfg.video_quality, cfg.video_preset, width, height)
    cache = _load_cache()
    return cache.get(key)


def get_default_fps(encoder: str) -> float:
    """Return the conservative default fps for an encoder with no calibration."""
    return DEFAULT_FPS.get(encoder, 5.0)


def _build_video_args(cfg: Config) -> list:
    """Build the per-encoder video argument list, mirroring video.py."""
    from .video import build_video_encoder_args
    return build_video_encoder_args(cfg)


def calibrate_encoder(
    cfg: Config,
    sample_path: Optional[str],
    logger,
    duration_s: float = 5.0,
) -> Dict[str, Any]:
    """Run a short transcode and measure achieved fps.

    If sample_path is None, generates a synthetic 1080p testsrc via lavfi.
    Returns a calibration dict and caches it.
    Raises RuntimeError on ffmpeg failure.
    """
    width, height = 1920, 1080
    fps_target = 30.0

    if sample_path is not None:
        try:
            meta = get_video_stream_meta(probe_file(sample_path))
        except Exception as e:
            raise RuntimeError(f"Could not probe {sample_path} for calibration: {e}")
        if meta["width"] and meta["height"]:
            width, height = int(meta["width"]), int(meta["height"])
        if meta["fps"]:
            fps_target = float(meta["fps"])

        seek = max(0.0, (meta["duration"] or 0.0) / 2.0 - duration_s / 2.0)
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{seek:.3f}",
            "-t", f"{duration_s:.3f}",
            "-i", sample_path,
        ]
    else:
        # Synthetic 1080p testsrc.
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"testsrc=duration={duration_s}:size={width}x{height}:rate={int(fps_target)}",
        ]

    cmd.extend([
        "-c:v", cfg.video_encoder,
    ])
    # Append encoder-specific args (quality, preset, etc.) by calling the same
    # builder used for real transcodes, minus the codec selector we just added.
    enc_args = _build_video_args(cfg)
    # _build_video_args returns ['-c:v', encoder, ...rest...]
    for a in enc_args[2:]:
        cmd.append(a)
    # Discard the encoded output; we only want the fps measurements.
    cmd.extend(["-f", "null", "-"])

    fps_samples: list = []
    start = time.time()
    proc: Optional[subprocess.Popen] = None
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1
        )
        # ffmpeg's `-progress` is not enabled above, so it writes to stderr
        # lines like: "frame=  120 fps= 45 q=... size=... time=00:00:04.00 ..."
        for line in proc.stderr or []:
            if "fps=" in line:
                try:
                    tail = line.split("fps=", 1)[1]
                    # Field is whitespace-separated; first token is the number
                    fps_val = float(tail.split()[0])
                    fps_samples.append(fps_val)
                except (ValueError, IndexError):
                    pass
        returncode = proc.wait(timeout=duration_s * 6 + 30)
    except subprocess.TimeoutExpired:
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        raise RuntimeError("Calibration ffmpeg run timed out")
    wallclock = time.time() - start

    if returncode != 0:
        raise RuntimeError(
            f"Calibration ffmpeg exited with code {returncode} (encoder may not be functional)"
        )
    if not fps_samples:
        raise RuntimeError("Calibration produced no fps samples")

    # Trim warmup (first few samples are encoder-init overhead) and the
    # trailing ones (cooldown / shutdown noise). For short runs (few samples
    # because the encode was so fast), keep the lot. For long runs, drop the
    # first 25% (capped at 5) and the last 3.
    n = len(fps_samples)
    if n <= 5:
        trim_lo, trim_hi = 0, n
    else:
        trim_lo = min(5, n // 4)
        trim_hi = max(trim_lo + 1, n - 3)
    steady = fps_samples[trim_lo:trim_hi] if trim_hi > trim_lo else fps_samples
    if not steady:
        steady = fps_samples
    achieved_steady = statistics.median(steady)
    achieved_mean = statistics.mean(steady)
    achieved_min = min(steady)
    achieved_max = max(steady)

    result: Dict[str, Any] = {
        "encoder": cfg.video_encoder,
        "quality": cfg.video_quality,
        "preset": cfg.video_preset,
        "width": width,
        "height": height,
        "fps_target": fps_target,
        "achieved_fps_steady": round(achieved_steady, 2),
        "achieved_fps_mean": round(achieved_mean, 2),
        "achieved_fps_min": round(achieved_min, 2),
        "achieved_fps_max": round(achieved_max, 2),
        "wallclock_seconds": round(wallclock, 2),
        "samples_total": len(fps_samples),
        "samples_steady": len(steady),
        "timestamp": time.time(),
    }

    key = _calibration_key(cfg.video_encoder, cfg.video_quality, cfg.video_preset, width, height)
    cache = _load_cache()
    cache[key] = result
    _save_cache(cache)
    if logger is not None:
        logger.info(
            f"Calibrated {cfg.video_encoder} @ {width}x{height} q{cfg.video_quality} "
            f"preset={cfg.video_preset}: {achieved_steady:.1f} fps steady "
            f"(mean={achieved_mean:.1f}, min={achieved_min:.1f}, max={achieved_max:.1f}, "
            f"n={len(steady)})"
        )
    return result


def predict_transcode_time(
    duration_s: float,
    source_fps: float,
    source_width: int,
    source_height: int,
    calibration: Dict[str, Any],
) -> float:
    """Predict seconds to transcode a file given a calibration.

    Scales the calibration's achieved fps linearly by source pixels /
    calibration pixels (valid for pixel-rate-bound hardware encoders).
    Returns duration_s * (source_fps / scaled_achieved_fps).
    """
    if duration_s <= 0:
        return 0.0
    cal_w = int(calibration.get("width", 0) or 0)
    cal_h = int(calibration.get("height", 0) or 0)
    cal_fps = float(calibration.get("achieved_fps_steady") or 0.0)
    if cal_fps <= 0 or cal_w <= 0 or cal_h <= 0:
        # No usable calibration; assume real-time as a safe default.
        return float(duration_s)
    if source_width <= 0 or source_height <= 0:
        return float(duration_s)
    src_pixels = source_width * source_height
    cal_pixels = cal_w * cal_h
    if src_pixels <= 0 or cal_pixels <= 0:
        return float(duration_s)
    scaled_achieved_fps = cal_fps * (cal_pixels / src_pixels)
    if source_fps <= 0:
        source_fps = 30.0
    if scaled_achieved_fps <= 0:
        return float(duration_s)
    return float(duration_s) * (source_fps / scaled_achieved_fps)


def parse_calibration_age_days(calibration: Optional[Dict[str, Any]]) -> Optional[float]:
    """Return the age of a calibration in days, or None if no timestamp."""
    if not calibration:
        return None
    ts = calibration.get("timestamp")
    if not ts:
        return None
    return (time.time() - float(ts)) / 86400.0
