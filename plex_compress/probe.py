"""ffprobe wrapper and stream parsing."""

import json
import subprocess
from typing import Any, Dict, List, Optional

from . import FfprobeError, MetadataError


def probe_file(path: str) -> Dict[str, Any]:
    """Run ffprobe on a file and return parsed JSON."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format:stream:chapter",
        "-show_format",
        "-show_chapters",
        "-of", "json",
        path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
    except subprocess.CalledProcessError as e:
        raise FfprobeError(f"ffprobe failed for {path}: {e.stderr}")
    except subprocess.TimeoutExpired:
        raise FfprobeError(f"ffprobe timed out for {path}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise FfprobeError(f"Invalid JSON from ffprobe for {path}: {e}")

    if not data or "streams" not in data:
        raise MetadataError(f"No streams found in {path}")

    return data


def get_video_stream(probe: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the first video stream from probe data."""
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video":
            return stream
    return None


def get_audio_streams(probe: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return all audio streams from probe data."""
    return [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]


def get_default_audio_stream(probe: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the default audio stream, or first if none marked default."""
    audio = get_audio_streams(probe)
    for s in audio:
        if s.get("disposition", {}).get("default") == 1:
            return s
    return audio[0] if audio else None


def get_subtitle_streams(probe: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return all subtitle streams from probe data."""
    return [s for s in probe.get("streams", []) if s.get("codec_type") == "subtitle"]


def get_attachment_streams(probe: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return all attachment streams from probe data."""
    return [s for s in probe.get("streams", []) if s.get("codec_type") == "attachment"]


def get_chapters(probe: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return all chapters from probe data."""
    return probe.get("chapters", [])


def get_duration(probe: Dict[str, Any]) -> Optional[float]:
    """Extract duration in seconds from probe data."""
    # Try format duration first
    fmt = probe.get("format", {})
    duration = fmt.get("duration")
    if duration is not None:
        try:
            return float(duration)
        except (ValueError, TypeError):
            pass
    # Fallback to video stream duration
    video = get_video_stream(probe)
    if video:
        duration = video.get("duration")
        if duration is not None:
            try:
                return float(duration)
            except (ValueError, TypeError):
                pass
    return None


def get_bitrate(probe: Dict[str, Any]) -> Optional[int]:
    """Extract bitrate in bits/sec from probe data."""
    fmt = probe.get("format", {})
    bitrate = fmt.get("bit_rate")
    if bitrate is not None:
        try:
            return int(bitrate)
        except (ValueError, TypeError):
            pass
    return None


def get_file_size(probe: Dict[str, Any]) -> Optional[int]:
    """Extract file size in bytes from probe data."""
    fmt = probe.get("format", {})
    size = fmt.get("size")
    if size is not None:
        try:
            return int(size)
        except (ValueError, TypeError):
            pass
    return None


def _parse_frame_rate(rate: Any) -> Optional[float]:
    """Parse an ffprobe r_frame_rate value like '30000/1001' into fps.

    Returns None for missing/empty/zero-denominator inputs.
    """
    if rate is None:
        return None
    s = str(rate).strip()
    if not s:
        return None
    if s in ("0/0", "0"):
        return None
    if "/" in s:
        try:
            num_s, den_s = s.split("/", 1)
            num = float(num_s)
            den = float(den_s)
            if den == 0:
                return None
            return num / den
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return float(s)
    except ValueError:
        return None


def get_video_stream_meta(probe: Dict[str, Any]) -> Dict[str, Any]:
    """Return a structured dict describing the first video stream.

    Fields: codec, width, height, fps, bitrate, duration, bpp.
    bpp = bits_per_pixel = bitrate / (width * height * fps).
    None for any missing field. Never raises.
    """
    v = get_video_stream(probe)
    if v is None:
        return {
            "codec": None, "width": None, "height": None,
            "fps": None, "bitrate": None, "duration": None, "bpp": None,
        }
    width = v.get("width")
    height = v.get("height")
    fps = _parse_frame_rate(v.get("r_frame_rate") or v.get("avg_frame_rate"))
    bitrate_raw = v.get("bit_rate")
    bitrate: Optional[int] = None
    if bitrate_raw is not None:
        try:
            bitrate = int(bitrate_raw)
        except (ValueError, TypeError):
            bitrate = None
    # Fall back to format-level bit_rate if per-stream missing or implausibly small
    if not bitrate or bitrate < 1000:
        fmt_br = probe.get("format", {}).get("bit_rate")
        if fmt_br is not None:
            try:
                fmt_b = int(fmt_br)
                # Subtract audio tracks roughly to estimate video-only bitrate
                audios = get_audio_streams(probe)
                audio_total = 0
                for a in audios:
                    abr = a.get("bit_rate")
                    if abr is not None:
                        try:
                            audio_total += int(abr)
                        except (ValueError, TypeError):
                            pass
                est = fmt_b - audio_total
                if est > 0:
                    bitrate = est
            except (ValueError, TypeError):
                pass
    duration = get_duration(probe)
    bpp: Optional[float] = None
    if bitrate and width and height and fps and width > 0 and height > 0 and fps > 0:
        bpp = bitrate / (width * height * fps)
    return {
        "codec": v.get("codec_name"),
        "width": width,
        "height": height,
        "fps": fps,
        "bitrate": bitrate,
        "duration": duration,
        "bpp": bpp,
    }


def get_audio_streams_meta(probe: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return a list of structured dicts, one per audio stream.

    Fields: codec, channels, layout, bitrate, default, language.
    Never raises; missing fields are None/False.
    """
    out: List[Dict[str, Any]] = []
    for s in get_audio_streams(probe):
        bitrate_raw = s.get("bit_rate")
        bitrate: Optional[int] = None
        if bitrate_raw is not None:
            try:
                bitrate = int(bitrate_raw)
            except (ValueError, TypeError):
                bitrate = None
        if not bitrate or bitrate < 1000:
            # ffmpeg's per-stream bit_rate for audio is often unset.
            # Estimate from duration + max bit rate if available, else 0.
            abr = s.get("max_bit_rate") or s.get("duration")
            # Best to just leave bitrate=None; callers handle 0 correctly.
            pass
        out.append({
            "codec": s.get("codec_name"),
            "channels": s.get("channels"),
            "layout": s.get("channel_layout"),
            "bitrate": bitrate,
            "default": s.get("disposition", {}).get("default", 0) == 1,
            "language": s.get("tags", {}).get("language") if isinstance(s.get("tags"), dict) else None,
        })
    return out
