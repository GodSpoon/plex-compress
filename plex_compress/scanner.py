"""Library scanner and candidate selection."""

import os
from typing import Dict, List, Tuple, Optional, Any

from .config import Config
from .probe import (
    probe_file,
    get_video_stream,
    get_default_audio_stream,
    get_duration,
    get_file_size,
)
from . import (
    AlreadyOptimalError,
    NotCandidateError,
    SkipFileError,
    ProbeError,
)


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts"}


def find_video_files(root: str, exclusions: List[str] = None) -> List[str]:
    """Recursively find all video files under root."""
    exclusions = exclusions or []
    files = []
    for dirpath, _dirnames, filenames in os.walk(root):
        # Skip excluded directories
        if any(excl in dirpath for excl in exclusions):
            continue
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext in VIDEO_EXTENSIONS:
                files.append(os.path.join(dirpath, name))
    return sorted(files)


def is_candidate(path: str, cfg: Config) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Check if a file is a candidate for transcoding.

    Returns (is_candidate, reason, probe_data).
    """
    try:
        probe = probe_file(path)
    except ProbeError as e:
        return False, f"probe_error: {e}", None

    # Size check
    size = get_file_size(probe)
    if size is not None and size < cfg.min_file_size_mb * 1024 * 1024:
        return False, f"too_small ({size / 1024 / 1024:.1f} MB)", probe

    # Duration check
    duration = get_duration(probe)
    if duration is not None and duration < cfg.min_duration_seconds:
        return False, f"too_short ({duration:.0f}s)", probe

    # Video codec check
    video = get_video_stream(probe)
    if video is None:
        return False, "no_video_stream", probe
    video_codec = video.get("codec_name", "").lower()
    if video_codec in cfg.skip_codecs_video:
        return False, f"already_optimal_video ({video_codec})", probe

    # Audio codec check (only skip if video is also optimal)
    audio = get_default_audio_stream(probe)
    if audio:
        audio_codec = audio.get("codec_name", "").lower()
        audio_channels = audio.get("channels", 0)
        audio_layout = audio.get("channel_layout", "")
        # If both video and audio are already optimal, skip
        if video_codec in cfg.skip_codecs_video and audio_codec in cfg.skip_codecs_audio:
            if audio_channels <= 2 or "stereo" in audio_layout:
                return False, f"already_optimal_both ({video_codec}/{audio_codec})", probe

    return True, "candidate", probe


def scan_library(cfg: Config, state=None, force: bool = False) -> Dict:
    """Scan library and return report dict.

    Keys:
      - total_files: int
      - candidates: List[str]
      - skipped: List[Tuple[str, str]]
      - already_optimal: int
      - estimated_savings_gb: float
    """
    files = find_video_files(cfg.library_path, cfg.exclusions)
    candidates = []
    skipped = []
    already_optimal = 0
    total_original_size = 0

    for path in files:
        # Skip already-completed files unless force is set
        if state is not None and not force:
            status = state.get_status(path)
            if status == "completed":
                skipped.append((path, "already_completed (state_db)"))
                continue

        ok, reason, probe = is_candidate(path, cfg)
        if ok:
            candidates.append(path)
            size = get_file_size(probe)
            if size:
                total_original_size += size
        else:
            skipped.append((path, reason))
            if "already_optimal" in reason:
                already_optimal += 1

    # Rough estimate: HEVC saves ~35-45% for H.264 sources
    estimated_savings_gb = (total_original_size * 0.40) / (1024 ** 3)

    return {
        "total_files": len(files),
        "candidates": candidates,
        "skipped": skipped,
        "already_optimal": already_optimal,
        "estimated_savings_gb": estimated_savings_gb,
    }
