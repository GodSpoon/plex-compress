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


def find_video_files(root: str, exclusions: Optional[List[str]] = None) -> List[str]:
    """Recursively find all video files under root."""
    return [path for path, _sig in find_video_files_with_sig(root, exclusions)]


def find_video_files_with_sig(root: str, exclusions: Optional[List[str]] = None):
    """Recursively find video files, capturing an 'mtime:size' signature in the
    same directory pass via os.scandir.

    On network mounts (SMB/NFS), the directory listing already carries stat
    info, so reading it here avoids a second per-file stat() round-trip later.
    Returns a sorted list of (path, signature_or_None) tuples.
    """
    exclusions = exclusions or []
    results: List[Tuple[str, Optional[str]]] = []
    stack = [root]
    while stack:
        current = stack.pop()
        if any(excl in current for excl in exclusions):
            continue
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                            continue
                        ext = os.path.splitext(entry.name)[1].lower()
                        if ext not in VIDEO_EXTENSIONS:
                            continue
                        try:
                            st = entry.stat(follow_symlinks=False)
                            sig = f"{int(st.st_mtime)}:{st.st_size}"
                        except OSError:
                            sig = None
                        results.append((entry.path, sig))
                    except OSError:
                        continue
        except OSError:
            continue
    results.sort(key=lambda t: t[0])
    return results


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


# Skip reasons that are stable for an unchanged file (safe to cache by
# signature). A probe error is NOT here: it may be a transient network read
# failure on a slow mount, so it must always be retried.
_STABLE_SKIP_PREFIXES = ("too_small", "too_short", "already_optimal", "no_video_stream")


def _is_stable_skip(reason: str) -> bool:
    return any(reason.startswith(p) for p in _STABLE_SKIP_PREFIXES)


def scan_library(cfg: Config, state=None, force: bool = False, full_scan: bool = False,
                 logger=None, progress_cb=None) -> Dict:
    """Scan library and return a report dict.

    Modes:
      - Incremental (default): for files already judged in a previous scan and
        unchanged (detected via a cheap mtime:size signature), the cached
        *stable* verdict is reused instead of running a full ffprobe. This
        avoids thousands of redundant network probes on slow (SMB/NFS) mounts.
      - Full (full_scan=True): re-probe every file except those already
        'completed'. Ignores the skip/pending cache so the candidate picture
        is rebuilt from scratch.

    The state DB's authoritative truth is the 'completed' status (work we have
    actually transcoded); it is never re-probed or re-transcoded unless force
    is set. 'skipped'/'pending' are scan-time verdicts cached only as a
    performance optimisation. Probe errors are tracked as 'error' and are
    always retried (never cached) and surfaced to the caller.

    Report keys:
      - total_files, candidates, skipped, already_optimal, estimated_savings_gb
      - errors: List[Tuple[path, reason]]   (unreadable / broken / transient)
      - probed: int   (files that needed a fresh ffprobe)
      - cached: int   (files resolved from the state DB without probing)
      - full_scan: bool
    """
    import logging
    from .state import compute_scan_sig

    logger = logger or logging.getLogger("plex_compress")
    files_with_sig = find_video_files_with_sig(cfg.library_path, cfg.exclusions)
    total = len(files_with_sig)
    candidates: List[str] = []
    skipped: List[Tuple[str, str]] = []
    errors: List[Tuple[str, str]] = []
    already_optimal = 0
    total_original_size = 0
    probed = 0
    cached = 0

    mode = "full" if full_scan else "incremental"
    logger.info(f"Found {total} video files; resolving candidates ({mode} scan)...")

    for i, (path, sig) in enumerate(files_with_sig):
        if sig is None:
            sig = compute_scan_sig(path)
        record = state.get_scan_record(path) if state is not None else None

        # 'completed' is durable truth: never re-evaluate unless force.
        if record and record.get("status") == "completed" and not force:
            skipped.append((path, "already_completed (state_db)"))
            if state is not None and sig and not record.get("scan_sig"):
                state.set_scan_sig(path, sig)
            cached += 1
            if progress_cb:
                progress_cb(i + 1, total, probed, cached)
            continue

        # Incremental fast path: reuse a cached *stable* verdict for an
        # unchanged file. Never reuse probe errors (status 'error') or
        # transcode failures -> those are always retried.
        if record and sig and not force and not full_scan:
            status = record.get("status")
            reason = record.get("reason") or ""
            sig_ok = (record.get("scan_sig") == sig) or not record.get("scan_sig")
            reusable = (
                status == "pending"
                or (status == "skipped" and _is_stable_skip(reason))
            )
            if sig_ok and reusable:
                if state is not None and not record.get("scan_sig"):
                    state.set_scan_sig(path, sig)  # backfill legacy rows
                if status == "pending":
                    candidates.append(path)
                    if record.get("original_size"):
                        total_original_size += record["original_size"]
                else:
                    skipped.append((path, reason or "skipped (cached)"))
                    if "already_optimal" in reason:
                        already_optimal += 1
                cached += 1
                if progress_cb:
                    progress_cb(i + 1, total, probed, cached)
                continue

        # Slow path: new / changed / transient-error / full-scan -> probe.
        ok, reason, probe = is_candidate(path, cfg)
        probed += 1
        if ok:
            candidates.append(path)
            size = get_file_size(probe) if probe else None
            if size:
                total_original_size += size
            if state is not None:
                state.record_scan(path, "pending", original_size=size, scan_sig=sig)
        elif reason.startswith("probe_error"):
            # Unreadable: could be a broken file or a transient mount hiccup.
            # Record as 'error' (always retried, never cached) and surface it.
            errors.append((path, reason))
            if state is not None:
                state.record_scan(path, "error", reason=reason, scan_sig=None)
        else:
            skipped.append((path, reason))
            if "already_optimal" in reason:
                already_optimal += 1
            if state is not None:
                size = get_file_size(probe) if probe else None
                state.record_scan(path, "skipped", original_size=size, reason=reason, scan_sig=sig)

        if (i + 1) % 250 == 0:
            logger.info(f"Scan progress: {i + 1}/{total} ({probed} probed, {cached} cached, {len(candidates)} candidates)")
        if progress_cb:
            progress_cb(i + 1, total, probed, cached)

    # Rough estimate: HEVC saves ~35-45% for H.264 sources
    estimated_savings_gb = (total_original_size * 0.40) / (1024 ** 3)

    logger.info(
        f"Scan complete ({mode}): {total} files, {probed} probed, {cached} cached, "
        f"{len(candidates)} candidates, {len(errors)} unreadable"
    )

    return {
        "total_files": total,
        "candidates": candidates,
        "skipped": skipped,
        "errors": errors,
        "already_optimal": already_optimal,
        "estimated_savings_gb": estimated_savings_gb,
        "probed": probed,
        "cached": cached,
        "full_scan": full_scan,
    }
