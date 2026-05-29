"""Intelligent transcoding decisions: savings prediction, incremental scan, priority queue."""

import os
from typing import Dict, List, Optional, Tuple, Any

from .config import Config
from .probe import (
    probe_file,
    get_video_stream,
    get_default_audio_stream,
    get_audio_streams,
    get_duration,
    get_file_size,
    get_bitrate,
)
from .state import StateDB
from . import (
    AlreadyOptimalError,
    NotCandidateError,
    SkipFileError,
    ProbeError,
)


# Savings prediction model: estimated compression ratio by source codec + bitrate.
# Based on empirical data: H.264 -> HEVC savings depend heavily on source bitrate.
# Higher bitrate sources compress more aggressively.
_SAVINGS_MODEL: Dict[str, Dict[str, float]] = {
    "h264": {
        "low":    0.30,   # < 2 Mbps
        "medium": 0.40,   # 2-5 Mbps
        "high":   0.50,   # 5-10 Mbps
        "ultra":  0.55,   # > 10 Mbps
    },
    "mpeg4": {
        "low":    0.35,
        "medium": 0.45,
        "high":   0.55,
        "ultra":  0.60,
    },
    "mpeg2": {
        "low":    0.40,
        "medium": 0.50,
        "high":   0.60,
        "ultra":  0.65,
    },
    "default": {
        "low":    0.25,
        "medium": 0.35,
        "high":   0.45,
        "ultra":  0.50,
    },
}


def _bitrate_tier(video_bitrate: Optional[int]) -> str:
    """Classify bitrate into tier for savings prediction."""
    if video_bitrate is None:
        return "medium"
    bps = video_bitrate
    if bps < 2_000_000:
        return "low"
    elif bps < 5_000_000:
        return "medium"
    elif bps < 10_000_000:
        return "high"
    return "ultra"


def predict_savings_bytes(
    video_codec: str,
    video_bitrate: Optional[int],
    file_size: Optional[int],
    duration: Optional[float],
    audio_channels: int = 6,
) -> int:
    """Predict space savings in bytes for transcoding a file.

    Model accounts for:
    - Video compression: codec-specific ratio based on bitrate tier
    - Audio overhead reduction: 5.1 -> stereo saves ~200-400 kbps
    - Container overhead: negligible

    If file_size is known, applies ratio directly.
    If only bitrate + duration known, estimates size from bitrate.
    """
    codec = video_codec.lower()
    tier = _bitrate_tier(video_bitrate)
    ratio = _SAVINGS_MODEL.get(codec, _SAVINGS_MODEL["default"]).get(tier, 0.40)

    # Audio overhead savings: 5.1 AC3 @ 384k -> stereo AAC @ 160k = ~224k saved
    # If already stereo, no audio savings
    audio_savings_bps = 0
    if audio_channels > 2:
        audio_savings_bps = 224_000  # ~224 kbps typical savings

    if file_size and duration:
        # Use actual file size + estimated audio savings
        video_savings = int(file_size * ratio)
        audio_savings = int(audio_savings_bps * duration / 8)
        return video_savings + audio_savings

    if video_bitrate and duration:
        # Estimate from bitrate: video savings + audio savings
        total_bitrate = video_bitrate + audio_savings_bps
        estimated_size = int(total_bitrate * duration / 8)
        return int(estimated_size * ratio)

    # Fallback: assume 40% of file size
    if file_size:
        return int(file_size * 0.40)

    return 0


def _compute_scan_hash(path: str) -> Optional[str]:
    """Compute a hash of file metadata for incremental scanning."""
    try:
        st = os.stat(path)
        return f"{st.st_mtime}:{st.st_size}"
    except OSError:
        return None


def _extract_probe_metadata(probe: Dict[str, Any], path: str) -> Dict[str, Any]:
    """Extract all relevant metadata from ffprobe output."""
    video = get_video_stream(probe)
    audio = get_default_audio_stream(probe)
    all_audio = get_audio_streams(probe)

    audio_bitrate = None
    if audio and audio.get("bit_rate"):
        try:
            audio_bitrate = int(audio["bit_rate"])
        except (ValueError, TypeError):
            pass

    meta = {
        "video_codec": video.get("codec_name", "").lower() if video else None,
        "video_width": video.get("width") if video else None,
        "video_height": video.get("height") if video else None,
        "video_bitrate": get_bitrate(probe),
        "audio_codec": audio.get("codec_name", "").lower() if audio else None,
        "audio_channels": audio.get("channels") if audio else None,
        "audio_bitrate": audio_bitrate,
        "duration": get_duration(probe),
        "container": os.path.splitext(path)[1].lstrip(".").lower() if path else None,
        "file_size": get_file_size(probe),
        "audio_stream_count": len(all_audio),
    }
    return meta


def is_candidate_intelligent(
    path: str,
    cfg: Config,
    state: Optional[StateDB] = None,
    force: bool = False,
) -> Tuple[bool, str, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Check if a file is a candidate, with intelligent metadata extraction.

    Returns: (is_candidate, reason, probe_data, metadata_dict)
    """
    # Incremental scan: skip unchanged files already in DB
    if state is not None and not force:
        try:
            st = os.stat(path)
            if not state.file_has_changed(path, st.st_mtime, st.st_size):
                db_status = state.get_status(path)
                if db_status == "completed":
                    return False, "already_completed (state_db)", None, None
                if db_status == "skipped":
                    return False, "already_skipped (state_db)", None, None
                # If pending/failed, re-evaluate (status may have changed)
        except OSError:
            pass

    try:
        probe = probe_file(path)
    except ProbeError as e:
        return False, f"probe_error: {e}", None, None

    meta = _extract_probe_metadata(probe, path)

    # Size check
    size = meta.get("file_size")
    if size is not None and size < cfg.min_file_size_mb * 1024 * 1024:
        return False, f"too_small ({size / 1024 / 1024:.1f} MB)", probe, meta

    # Duration check
    duration = meta.get("duration")
    if duration is not None and duration < cfg.min_duration_seconds:
        return False, f"too_short ({duration:.0f}s)", probe, meta

    # Video codec check
    video_codec = meta.get("video_codec")
    if video_codec is None:
        return False, "no_video_stream", probe, meta
    if video_codec in cfg.skip_codecs_video:
        return False, f"already_optimal_video ({video_codec})", probe, meta

    # Audio codec check (only skip if video is also optimal)
    audio_codec = meta.get("audio_codec")
    if audio_codec:
        if audio_codec in cfg.skip_codecs_audio and video_codec in cfg.skip_codecs_video:
            return False, f"already_optimal_both ({video_codec}/{audio_codec})", probe, meta

    # Compute predicted savings
    predicted = predict_savings_bytes(
        video_codec=video_codec,
        video_bitrate=meta.get("video_bitrate"),
        file_size=size,
        duration=duration,
        audio_channels=meta.get("audio_channels") or 6,
    )
    meta["predicted_savings_bytes"] = predicted

    return True, "candidate", probe, meta


def scan_library_intelligent(
    cfg: Config,
    state: Optional[StateDB] = None,
    force: bool = False,
    logger=None,
) -> Dict[str, Any]:
    """Intelligent library scan with metadata persistence and incremental detection.

    Returns a rich report dict with per-codec, per-resolution breakdowns.
    """
    from .scanner import find_video_files

    files = find_video_files(cfg.library_path, cfg.exclusions)
    candidates = []
    skipped = []
    already_optimal = 0
    total_predicted_savings = 0
    total_original_size = 0

    # Per-codec counters for reporting
    by_codec: Dict[str, Dict[str, Any]] = {}
    by_resolution: Dict[Tuple[int, int], Dict[str, Any]] = {}

    for path in files:
        # Check state DB first for completed files (fast path)
        if state is not None and not force:
            db_status = state.get_status(path)
            if db_status == "completed":
                skipped.append((path, "already_completed (state_db)"))
                continue

        ok, reason, probe, meta = is_candidate_intelligent(path, cfg, state=state, force=force)

        if ok:
            candidates.append(path)
            if meta:
                size = meta.get("file_size", 0) or 0
                predicted = meta.get("predicted_savings_bytes", 0) or 0
                total_original_size += size
                total_predicted_savings += predicted

                # Track by codec
                vc = meta.get("video_codec", "unknown")
                if vc not in by_codec:
                    by_codec[vc] = {"count": 0, "total_size": 0, "predicted_savings": 0}
                by_codec[vc]["count"] += 1
                by_codec[vc]["total_size"] += size
                by_codec[vc]["predicted_savings"] += predicted

                # Track by resolution
                res = (meta.get("video_width") or 0, meta.get("video_height") or 0)
                if res not in by_resolution:
                    by_resolution[res] = {"count": 0, "total_size": 0, "predicted_savings": 0}
                by_resolution[res]["count"] += 1
                by_resolution[res]["total_size"] += size
                by_resolution[res]["predicted_savings"] += predicted

                # Persist to state DB with full metadata
                if state is not None:
                    scan_hash = _compute_scan_hash(path)
                    state.upsert_file(
                        path=path,
                        status="pending",
                        original_size=size,
                        video_codec=meta.get("video_codec"),
                        video_width=meta.get("video_width"),
                        video_height=meta.get("video_height"),
                        video_bitrate=meta.get("video_bitrate"),
                        audio_codec=meta.get("audio_codec"),
                        audio_channels=meta.get("audio_channels"),
                        audio_bitrate=meta.get("audio_bitrate"),
                        duration=meta.get("duration"),
                        container=meta.get("container"),
                        file_mtime=os.path.getmtime(path) if os.path.exists(path) else None,
                        file_size=size,
                        predicted_savings_bytes=predicted,
                        scan_hash=scan_hash,
                    )
        else:
            skipped.append((path, reason))
            if "already_optimal" in reason:
                already_optimal += 1
            if state is not None and probe is not None and meta is not None:
                # Persist skipped files too, so we don't re-probe them
                scan_hash = _compute_scan_hash(path)
                state.upsert_file(
                    path=path,
                    status="skipped",
                    reason=reason,
                    original_size=meta.get("file_size"),
                    video_codec=meta.get("video_codec"),
                    video_width=meta.get("video_width"),
                    video_height=meta.get("video_height"),
                    video_bitrate=meta.get("video_bitrate"),
                    audio_codec=meta.get("audio_codec"),
                    audio_channels=meta.get("audio_channels"),
                    audio_bitrate=meta.get("audio_bitrate"),
                    duration=meta.get("duration"),
                    container=meta.get("container"),
                    file_mtime=os.path.getmtime(path) if os.path.exists(path) else None,
                    file_size=meta.get("file_size"),
                    scan_hash=scan_hash,
                )

    estimated_savings_gb = total_predicted_savings / (1024 ** 3)

    # Record scan snapshot
    scan_id = None
    if state is not None:
        scan_id = state.record_scan(
            total_files=len(files),
            candidates=len(candidates),
            already_optimal=already_optimal,
            skipped=len(skipped),
            estimated_savings_gb=estimated_savings_gb,
            library_path=cfg.library_path,
        )

    return {
        "total_files": len(files),
        "candidates": candidates,
        "skipped": skipped,
        "already_optimal": already_optimal,
        "estimated_savings_gb": estimated_savings_gb,
        "estimated_savings_bytes": total_predicted_savings,
        "total_original_size_bytes": total_original_size,
        "by_codec": by_codec,
        "by_resolution": by_resolution,
        "scan_id": scan_id,
    }


def get_priority_queue(state: StateDB, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Return the highest-value pending files for transcoding."""
    return state.get_top_candidates(limit=limit or 100)


def generate_report(state: StateDB) -> Dict[str, Any]:
    """Generate a comprehensive transcoding report."""
    stats = state.get_stats()
    by_codec = state.get_stats_by_codec()
    by_resolution = state.get_stats_by_resolution()
    top_pending = state.get_top_candidates(limit=10)
    scan_history = state.get_scan_history(limit=5)

    return {
        "summary": stats,
        "by_codec": by_codec,
        "by_resolution": by_resolution,
        "top_pending": top_pending,
        "scan_history": scan_history,
    }
