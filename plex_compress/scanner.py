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
    get_video_stream_meta,
    get_audio_streams_meta,
)
from . import (
    AlreadyOptimalError,
    NotCandidateError,
    SkipFileError,
    ProbeError,
)


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts"}


# Codec efficiency ratios: how many bits each codec needs to reach the same
# perceptual quality as H.264 at the same scene. Lower = more efficient.
# Derived from Netflix per-title encoding research and the x265 quality
# tables; values rounded for estimation purposes.
CODEC_EFFICIENCY = {
    "mpeg2video": 5.0, "mpeg1video": 5.0,
    "h264": 1.0, "h263": 1.2,
    "hevc": 0.5, "h265": 0.5,
    "av1": 0.4, "vp9": 0.55,
    "wmv2": 1.5, "vc1": 1.3,
}

# Target codec (always HEVC for this tool) efficiency at the *baseline* CRF
# of 28. Used to translate source-bpp into target-bpp via
# `target_bpp = src_bpp * TARGET_EFFICIENCY / source_eff * quality_factor`.
TARGET_EFFICIENCY = 0.5

# Default video metadata when we have only file size (cached pending).
# 1080p / 30 fps is the dominant tier for a typical Plex TV library.
_DEFAULT_WIDTH = 1920
_DEFAULT_HEIGHT = 1080
_DEFAULT_FPS = 30.0


def parse_bitrate(s: str) -> int:
    """Parse '160k' -> 160000, '1.5M' -> 1500000, '1000' -> 1000.

    Returns 0 for unrecognized input; never raises.
    """
    if s is None:
        return 0
    text = str(s).strip()
    if not text:
        return 0
    suffix = text[-1].lower()
    mult = 1
    body = text
    if suffix in ("k", "m", "g"):
        body = text[:-1].strip()
        mult = {"k": 1000, "m": 1000 ** 2, "g": 1000 ** 3}[suffix]
    try:
        return int(float(body) * mult)
    except (ValueError, TypeError):
        return 0


def predict_output_video_bytes(
    probe: Optional[Dict[str, Any]],
    cfg: Config,
    file_size_fallback: int = 0,
    bpp_table: Optional[Dict[int, float]] = None,
) -> int:
    """Predict the size in bytes of the *video* stream after transcoding.

    Uses the bpp model: target_bpp = source_bpp * codec_efficiency * quality_factor.
    If a calibrated bpp_table is provided (from real encodes), it overrides the
    estimate for the matching source width. Falls back to a 60% of original
    file size estimate when probe data is insufficient.
    """
    if not probe:
        return int(file_size_fallback * 0.60) if file_size_fallback else 0
    v = get_video_stream_meta(probe)
    width = v["width"]
    height = v["height"]
    fps = v["fps"]
    bitrate = v["bitrate"]
    duration = v["duration"] or get_duration(probe)
    if not (width and height and fps and duration and bitrate):
        return int(file_size_fallback * 0.60) if file_size_fallback else 0
    if width <= 0 or height <= 0 or fps <= 0 or duration <= 0 or bitrate <= 0:
        return int(file_size_fallback * 0.60) if file_size_fallback else 0
    src_bpp = bitrate / (width * height * fps)
    codec = (v["codec"] or "").lower() if v["codec"] else ""
    source_eff = CODEC_EFFICIENCY.get(codec, 1.0)
    # Quality adjustment: linear in log-space; +1 CRF ~ -10% bpp. CRF 28 baseline.
    quality_factor = 10 ** ((28 - int(cfg.video_quality)) / 10.0)
    # Source bpp -> target HEVC bpp: divide by source efficiency, multiply by
    # the target's baseline efficiency, then scale by the user's quality knob.
    target_bpp = src_bpp * (TARGET_EFFICIENCY / source_eff) * quality_factor

    if bpp_table and width in bpp_table:
        target_bpp = bpp_table[width]

    out_bits = target_bpp * width * height * fps * duration
    return int(out_bits / 8)


def predict_output_audio_bytes(probe: Optional[Dict[str, Any]], cfg: Config) -> int:
    """Predict the size in bytes of all audio streams after transcoding.

    The first/default audio track is re-encoded to cfg.audio_bitrate at
    cfg.audio_channels; other tracks are copied at their source bitrate.
    Returns 0 if probe or duration is missing.
    """
    if not probe:
        return 0
    duration = get_duration(probe) or 0
    if duration <= 0:
        return 0
    audios = get_audio_streams_meta(probe)
    if not audios:
        return 0
    target_bitrate = parse_bitrate(cfg.audio_bitrate)
    any_default = any(a["default"] for a in audios)
    total_bits = 0
    for i, a in enumerate(audios):
        is_default = a["default"] or (i == 0 and not any_default)
        if is_default:
            bitrate = target_bitrate
        else:
            bitrate = a["bitrate"] or 0
        if bitrate <= 0:
            # Fall back to a conservative default for unspecified tracks.
            bitrate = 160000 if is_default else 96000
        total_bits += bitrate * duration
    return int(total_bits / 8)


def predict_savings(
    probe: Optional[Dict[str, Any]],
    file_size: int,
    cfg: Config,
    bpp_table: Optional[Dict[int, float]] = None,
) -> Dict[str, int]:
    """Predict per-file savings.

    Returns {in_video, in_audio, out_video, out_audio, savings_bytes,
    predicted_output_bytes}. in_video/in_audio/in_total are derived from
    the probe (bitrate * duration) when possible, else fall back to
    file_size with a 90/10 video/audio split.
    """
    duration = get_duration(probe) if probe else None
    if probe and duration:
        v_meta = get_video_stream_meta(probe)
        a_meta = get_audio_streams_meta(probe)
        in_video = int((v_meta["bitrate"] or 0) * duration / 8) if v_meta["bitrate"] else int(file_size * 0.90)
        in_audio = sum(int((a["bitrate"] or 0) * duration / 8) for a in a_meta)
        if in_audio == 0 and a_meta:
            in_audio = int(file_size * 0.10 / max(1, len(a_meta)))
        elif in_audio == 0:
            in_audio = int(file_size * 0.10)
    else:
        in_video = int(file_size * 0.90)
        in_audio = int(file_size * 0.10)

    out_video = predict_output_video_bytes(probe, cfg, file_size, bpp_table)
    out_audio = predict_output_audio_bytes(probe, cfg)
    predicted_output = out_video + out_audio
    in_total = in_video + in_audio
    savings = max(0, in_total - predicted_output)
    return {
        "in_video": in_video,
        "in_audio": in_audio,
        "out_video": out_video,
        "out_audio": out_audio,
        "in_total": in_total,
        "predicted_output_bytes": predicted_output,
        "savings_bytes": savings,
    }


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
    from .calibrate import get_cached_calibration, get_default_fps, predict_transcode_time

    logger = logger or logging.getLogger("plex_compress")
    files_with_sig = find_video_files_with_sig(cfg.library_path, cfg.exclusions)
    total = len(files_with_sig)
    candidates: List[str] = []
    skipped: List[Tuple[str, str]] = []
    errors: List[Tuple[str, str]] = []
    already_optimal = 0
    total_original_size = 0
    total_predicted_savings_bytes = 0
    total_predicted_output_bytes = 0
    estimated_transcode_seconds = 0.0
    savings_by_codec: Dict[str, int] = {}
    probed = 0
    cached = 0

    # Try to load a real calibration for the configured encoder. If none,
    # fall back to encoder-specific default fps assumptions.
    cal = get_cached_calibration(cfg, _DEFAULT_WIDTH, _DEFAULT_HEIGHT)
    cal_achieved_fps = float((cal or {}).get("achieved_fps_steady") or 0.0)
    if cal_achieved_fps <= 0:
        cal_achieved_fps = get_default_fps(cfg.video_encoder)
        cal = None  # ensure predict_transcode_time uses default path
    cal_label = (
        f"{cal_achieved_fps:.1f} fps (calibrated)" if cal
        else f"{cal_achieved_fps:.1f} fps (default for {cfg.video_encoder})"
    )

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
                    orig_size = record.get("original_size") or 0
                    if orig_size:
                        total_original_size += orig_size
                        # For cached pending, assume 1080p h264 for codec,
                        # and a default 30fps for time estimation. The
                        # savings ratio is the only thing we can guess at
                        # without re-probing.
                        default_pred = predict_savings(None, orig_size, cfg)
                        total_predicted_savings_bytes += default_pred["savings_bytes"]
                        total_predicted_output_bytes += default_pred["predicted_output_bytes"]
                        savings_by_codec["h264"] = savings_by_codec.get("h264", 0) + default_pred["savings_bytes"]
                        # Time: assume 30fps source. We can't know resolution
                        # from size alone, so use the calibration's resolution.
                        est_seconds = predict_transcode_time(
                            orig_size / 6_000_000,  # rough: ~6 Mbps per byte/s
                            _DEFAULT_FPS,
                            _DEFAULT_WIDTH,
                            _DEFAULT_HEIGHT,
                            {"width": _DEFAULT_WIDTH, "height": _DEFAULT_HEIGHT,
                             "achieved_fps_steady": cal_achieved_fps},
                        )
                        estimated_transcode_seconds += est_seconds
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
                pred = predict_savings(probe, size, cfg)
                total_predicted_savings_bytes += pred["savings_bytes"]
                total_predicted_output_bytes += pred["predicted_output_bytes"]
                v_meta = get_video_stream_meta(probe) if probe else None
                codec = (v_meta["codec"] if v_meta else None) or "unknown"
                savings_by_codec[codec] = savings_by_codec.get(codec, 0) + pred["savings_bytes"]
                if v_meta and v_meta["duration"] and v_meta["fps"] and v_meta["width"] and v_meta["height"]:
                    est_seconds = predict_transcode_time(
                        v_meta["duration"],
                        v_meta["fps"],
                        v_meta["width"],
                        v_meta["height"],
                        {"width": (cal or {}).get("width", _DEFAULT_WIDTH),
                         "height": (cal or {}).get("height", _DEFAULT_HEIGHT),
                         "achieved_fps_steady": cal_achieved_fps},
                    )
                    estimated_transcode_seconds += est_seconds
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

    estimated_savings_gb = total_predicted_savings_bytes / (1024 ** 3)
    estimated_output_gb = total_predicted_output_bytes / (1024 ** 3)
    estimated_transcode_hours = estimated_transcode_seconds / 3600.0
    savings_by_codec_gb = {k: round(v / (1024 ** 3), 3) for k, v in savings_by_codec.items()}

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
        "estimated_savings_gb": round(estimated_savings_gb, 3),
        "estimated_output_gb": round(estimated_output_gb, 3),
        "estimated_savings_by_codec": savings_by_codec_gb,
        "estimated_transcode_hours": round(estimated_transcode_hours, 2),
        "transcode_fps_basis": cal_label,
        "probed": probed,
        "cached": cached,
        "full_scan": full_scan,
    }
