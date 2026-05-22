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
        "-show_entries", "format:stream",
        "-show_format",
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
