"""Core transcoding logic: copy, transcode, verify, replace."""

import os
import re
import shutil
import statistics
import subprocess
import time
from typing import Optional

from .config import Config
from .probe import (
    probe_file, get_video_stream, get_audio_streams, get_default_audio_stream,
    get_subtitle_streams, get_duration, get_file_size, get_attachment_streams,
    get_chapters, get_video_stream_meta, get_audio_streams_meta,
)
from .audio import build_audio_encoder_args, build_audio_filter

from .video import build_video_encoder_args
from .state import StateDB
from .utils import (
    copy_with_verify, safe_move, make_temp_path, get_free_space_gb,
    acquire_file_lock, release_file_lock, is_file_recently_modified,
)
from . import (
    PlexCompressError,
    TranscodeError,
    VerificationError,
    FfmpegError,
    CopyError,
    ReplaceError,
    TempSpaceError,
    SafetyError,
    DryRunError,
    InterruptError,
    CodecMismatchError,
    ChannelLayoutError,
    DurationError,
    SubtitleLossError,
    ContainerError,
    MetadataError,
    AudioLossError,
    AttachmentLossError,
    ChapterLossError,
    LockError,
)


def build_ffmpeg_command(input_path: str, output_path: str, cfg: Config, probe_data: Optional[dict] = None) -> list:
    """Build the full ffmpeg command list."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-map", "0:v:0?",
        "-map", "0:a?",
        "-map", "0:s?",
        "-map", "0:t?",
        "-map_chapters", "0",
        "-map_metadata", "0",
        "-max_muxing_queue_size", "9999",
    ]
    # Video
    cmd.extend(build_video_encoder_args(cfg))

    # Audio
    if probe_data is not None:
        audio_streams = [s for s in probe_data.get("streams", []) if s.get("codec_type") == "audio"]
        if audio_streams:
            default_idx = 0
            for i, stream in enumerate(audio_streams):
                dispositions = stream.get("disposition", {})
                if dispositions.get("default") == 1:
                    default_idx = i
                    break

            audio_filter = build_audio_filter(cfg)
            for i, stream in enumerate(audio_streams):
                if i == default_idx:
                    if audio_filter:
                        cmd.extend([f"-af:a:{i}", audio_filter])
                    if not cfg.use_rfc7845_downmix:
                        cmd.extend([f"-ac:a:{i}", str(cfg.audio_channels)])
                    cmd.extend([
                        f"-c:a:{i}", cfg.audio_encoder,
                        f"-b:a:{i}", cfg.audio_bitrate,
                    ])
                else:
                    cmd.extend([f"-c:a:{i}", "copy"])
        else:
            cmd.extend(build_audio_encoder_args(cfg))
    else:
        cmd.extend(build_audio_encoder_args(cfg))

    # Subtitles: copy all
    cmd.extend(["-c:s", "copy"])
    cmd.extend(["-c:t", "copy"])

    # Container
    cmd.append(output_path)
    return cmd



def verify_output(input_path: str, output_path: str, cfg: Config, in_probe: Optional[dict] = None) -> None:
    """Verify the transcoded output meets expectations.

    in_probe: optional pre-computed input probe dict. When provided, the
    function skips a redundant ffprobe on the input. Callers that already
    have the input probed (e.g. transcoder._transcode_attempt) should pass it.
    """
    try:
        if in_probe is None:
            in_probe = probe_file(input_path)
        out_probe = probe_file(output_path)
    except Exception as e:
        raise VerificationError(f"Probe failed during verification: {e}")

    # Check video codec
    out_video = get_video_stream(out_probe)
    if out_video is None:
        raise CodecMismatchError("No video stream in output")
    expected_video = "hevc" if cfg.video_encoder in ("hevc_videotoolbox", "libx265", "hevc_nvenc") else cfg.video_encoder
    if out_video.get("codec_name") != expected_video:
        raise CodecMismatchError(
            f"Expected video codec {expected_video}, got {out_video.get('codec_name')}"
        )

    # Check audio channels (only the default track must be stereo;
    # non-default tracks are copied as-is and may retain surround.)
    out_audio = get_audio_streams(out_probe)
    if out_audio:
        default_aud = get_default_audio_stream(out_probe)
        if default_aud is None:
            default_aud = out_audio[0]
        channels = default_aud.get("channels", 0)
        layout = default_aud.get("channel_layout", "")
        if channels > 2 and "stereo" not in layout:
            raise ChannelLayoutError(
                f"Default audio has {channels} channels, layout={layout}"
            )

    # Check duration
    in_dur = get_duration(in_probe)
    out_dur = get_duration(out_probe)
    if in_dur is not None and out_dur is not None:
        diff = abs(in_dur - out_dur)
        if diff > cfg.verify_duration_tolerance:
            raise DurationError(
                f"Duration mismatch: input={in_dur:.2f}s output={out_dur:.2f}s diff={diff:.2f}s"
            )

    # Check subtitles preserved
    in_subs = get_subtitle_streams(in_probe)
    out_subs = get_subtitle_streams(out_probe)
    if len(in_subs) > len(out_subs):
        raise SubtitleLossError(
            f"Subtitle count dropped from {len(in_subs)} to {len(out_subs)}"
        )

    # Check audio streams preserved
    in_audio = get_audio_streams(in_probe)
    out_audio = get_audio_streams(out_probe)
    if len(in_audio) > len(out_audio):
        raise AudioLossError(
            f"Audio count dropped from {len(in_audio)} to {len(out_audio)}"
        )

    # Check attachments preserved
    in_attach = get_attachment_streams(in_probe)
    out_attach = get_attachment_streams(out_probe)
    if len(in_attach) > len(out_attach):
        raise AttachmentLossError(
            f"Attachment count dropped from {len(in_attach)} to {len(out_attach)}"
        )

    # Check chapters preserved
    in_chaps = get_chapters(in_probe)
    out_chaps = get_chapters(out_probe)
    if len(in_chaps) > len(out_chaps):
        raise ChapterLossError(
            f"Chapter count dropped from {len(in_chaps)} to {len(out_chaps)}"
        )



_FPS_RE = re.compile(r"fps=\s*([0-9]*\.?[0-9]+)")


def _parse_fps_samples(ffmpeg_log: str) -> list:
    """Return the list of fps samples ffmpeg reported to stderr.

    ffmpeg's `-progress` style progress lines look like
        frame=  120 fps= 45 q=... size=... time=00:00:04.00 ...
    Returns floats in sample order. Empty list on any read error.
    """
    try:
        with open(ffmpeg_log, "r", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    out: list = []
    for m in _FPS_RE.finditer(text):
        try:
            v = float(m.group(1))
            if v > 0 and v < 1e6:  # filter out clearly bogus values
                out.append(v)
        except (ValueError, IndexError):
            continue
    return out


def _steady_state_fps(samples: list) -> Optional[float]:
    """Return the steady-state median fps from a list of samples.

    Trims a fixed fraction of warmup and cooldown samples (encoder init
    tends to produce low values for the first second or two, and the last
    few samples are noisy as ffmpeg shuts down). Returns None if no
    samples are available.
    """
    if not samples:
        return None
    n = len(samples)
    trim_lo = min(5, max(0, n // 4))
    trim_hi = max(trim_lo + 1, n - 3)
    steady = samples[trim_lo:trim_hi] if trim_hi > trim_lo else samples
    if not steady:
        return None
    try:
        return float(statistics.median(steady))
    except statistics.StatisticsError:
        return None


def _build_encode_metrics(
    in_probe: dict,
    out_size: int,
    in_size: int,
    cfg: Config,
    wallclock_seconds: Optional[float],
    achieved_fps: Optional[float],
) -> dict:
    """Build the metrics dict we persist alongside each completed transcode.

    All values are best-effort; missing data is left as None. Never raises.
    """
    m: dict = {
        "encoder": cfg.video_encoder,
        "encoder_quality": cfg.video_quality,
        "encoder_preset": cfg.video_preset,
        "actual_savings_bytes": max(0, in_size - out_size),
        "output_bytes": out_size,
        "input_bytes": in_size,
    }
    if wallclock_seconds is not None:
        m["wallclock_seconds"] = float(wallclock_seconds)
    if achieved_fps is not None:
        m["achieved_fps"] = float(achieved_fps)
    try:
        v = get_video_stream_meta(in_probe) if in_probe else None
    except Exception:
        v = None
    if v:
        m["video_codec"] = v.get("codec")
        m["video_width"] = v.get("width")
        m["video_height"] = v.get("height")
        m["video_bitrate"] = v.get("bitrate")
        m["duration"] = v.get("duration")
        m["source_bpp"] = v.get("bpp")
        if v.get("fps"):
            m["fps_source"] = v.get("fps")
        # bpp of the *output* video stream: we don't know exactly how
        # container overhead is split, but the dominant component is the
        # video bitstream, so back-calculate from out_size / duration.
        if v.get("width") and v.get("height") and v.get("fps") and v.get("duration"):
            dur = float(v["duration"])
            w = int(v["width"])
            h = int(v["height"])
            fps = float(v["fps"])
            if dur > 0 and w > 0 and h > 0 and fps > 0 and out_size > 0:
                # output_bpp is bits/pixel relative to the source resolution
                # (a useful cross-resolution comparison metric).
                m["output_bpp"] = (out_size * 8) / (w * h * fps * dur)
    try:
        a_meta = get_audio_streams_meta(in_probe) if in_probe else []
    except Exception:
        a_meta = []
    if a_meta:
        # Pick the default audio track; fall back to the first.
        primary = next((a for a in a_meta if a.get("default")), a_meta[0])
        m["audio_codec"] = primary.get("codec")
        m["audio_channels"] = primary.get("channels")
    return m


def _transcode_attempt(path: str, temp_input: str, temp_output: str, ffmpeg_log: str, final_tmp: str, cfg: Config, state: StateDB, logger) -> bool:
    """Single transcode attempt. Returns True on success, raises on failure."""
    try:
        # Cleanup orphaned temp from a previous crashed in-place run
        if os.path.exists(final_tmp):
            try:
                os.remove(final_tmp)
            except OSError:
                pass

        # Copy source to local temp
        logger.info(f"Copying {path} to temp...")
        in_probe = probe_file(path)
        if cfg.verify_checksum:
            copy_with_verify(path, temp_input)
        else:
            shutil.copy2(path, temp_input)

        # Build and run ffmpeg
        cmd = build_ffmpeg_command(temp_input, temp_output, cfg, probe_file(temp_input))
        logger.info(f"Transcoding: ffmpeg {' '.join(cmd[:12])} ...")
        transcode_start = time.monotonic()
        with open(ffmpeg_log, "w") as stderr_fh:
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=stderr_fh)
            except subprocess.CalledProcessError:
                stderr_fh.flush()
                with open(ffmpeg_log, "r") as f:
                    stderr_text = f.read()
                raise FfmpegError(f"ffmpeg failed: {stderr_text[-4000:]}")
        transcode_wallclock = time.monotonic() - transcode_start
        fps_samples = _parse_fps_samples(ffmpeg_log)
        achieved_fps = _steady_state_fps(fps_samples)

        # Verify output (reuse in_probe; saves an extra ffprobe on the input)
        if cfg.verify_output:
            logger.info("Verifying output...")
            verify_output(temp_input, temp_output, cfg, in_probe=in_probe)

        # Check output is smaller (safety)
        out_size = os.path.getsize(temp_output)
        in_size = os.path.getsize(temp_input)
        if out_size > in_size * 1.1:
            raise SafetyError(
                f"Output size ({out_size}) > 110% of input ({in_size}), skipping replacement"
            )
        if out_size > in_size * 0.95:
            raise SafetyError(
                f"Output size ({out_size}) > 95% of input ({in_size}), not worth the transcode"
            )

        if cfg.output_dir:
            if cfg.library_path and path.startswith(cfg.library_path):
                rel = os.path.relpath(path, cfg.library_path)
            else:
                rel = os.path.basename(path)
            output_path = os.path.join(cfg.output_dir, rel)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            safe_move(temp_output, output_path)
            metrics = _build_encode_metrics(
                in_probe, out_size, in_size, cfg, transcode_wallclock, achieved_fps
            )
            state.mark_completed(path, out_size, metrics=metrics)
            if achieved_fps is not None:
                logger.info(
                    f"Done: {path} -> {output_path} "
                    f"({in_size / 1024 / 1024:.1f} MB -> {out_size / 1024 / 1024:.1f} MB, "
                    f"{achieved_fps:.1f} fps)"
                )
            else:
                logger.info(
                    f"Done: {path} -> {output_path} "
                    f"({in_size / 1024 / 1024:.1f} MB -> {out_size / 1024 / 1024:.1f} MB)"
                )
        else:
            shutil.copy2(temp_output, final_tmp)
            backup_path = path + cfg.backup_suffix
            if cfg.keep_backup:
                if os.path.exists(backup_path):
                    os.remove(backup_path)
                os.replace(path, backup_path)
                logger.info(f"Keeping backup at {backup_path}")
            os.replace(final_tmp, path)

            # Post-replace verification: ensure the replaced file is valid
            if cfg.post_replace_verify:
                logger.info("Verifying replaced file...")
                try:
                    verify_output(temp_input, path, cfg, in_probe=in_probe)
                except Exception as e:
                    # Attempt rollback if backup exists
                    if cfg.keep_backup and os.path.exists(backup_path):
                        logger.error(f"Post-replace verification failed, rolling back: {e}")
                        os.replace(backup_path, path)
                        raise SafetyError(f"Post-replace verification failed, rolled back: {e}")
                    raise SafetyError(f"Post-replace verification failed (no backup): {e}")

            metrics = _build_encode_metrics(
                in_probe, out_size, in_size, cfg, transcode_wallclock, achieved_fps
            )
            state.mark_completed(path, out_size, metrics=metrics)
            if achieved_fps is not None:
                logger.info(
                    f"Done: {path} ({in_size / 1024 / 1024:.1f} MB -> {out_size / 1024 / 1024:.1f} MB, "
                    f"{achieved_fps:.1f} fps)"
                )
            else:
                logger.info(
                    f"Done: {path} ({in_size / 1024 / 1024:.1f} MB -> {out_size / 1024 / 1024:.1f} MB)"
                )
        return True
    finally:
        for p in (temp_input, temp_output, ffmpeg_log, final_tmp):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
def transcode_file(path: str, cfg: Config, state: StateDB, logger) -> bool:
    """Transcode a single file end-to-end.

    Returns True on success, False on failure.
    """
    if cfg.dry_run:
        logger.info(f"[DRY-RUN] Would transcode: {path}")
        return True

    # File age guard: skip files still being written
    if is_file_recently_modified(path, cfg.min_file_age_seconds):
        logger.info(
            f"Skipping recently modified file (lt; {cfg.min_file_age_seconds}s old): {path}"
        )
        return False

    file_size = os.path.getsize(path)

    # Check temp space (peak is ~1× file size; 1.5× gives safety margin for
    # partial writes, mkvmerge scratch, and verification copies).
    free_gb = get_free_space_gb(cfg.temp_dir)
    needed_gb = (file_size * 1.5) / (1024 ** 3)
    if free_gb < needed_gb:
        logger.error(
            f"Only {free_gb:.1f} GB free in temp dir, need ~{needed_gb:.1f} GB for {path}"
        )
        return False

    # Acquire file lock to prevent concurrent processing
    lock_fd = None
    if cfg.enable_file_locking:
        lock_fd = acquire_file_lock(path)
        if lock_fd is None:
            logger.info(f"Skipping locked file (another process is working on it): {path}")
            return False

    state.mark_started(path, file_size)

    try:
        last_error = None
        for attempt in range(2):
            temp_input = make_temp_path(cfg.temp_dir, suffix=os.path.splitext(path)[1])
            ffmpeg_log = temp_input + ".ffmpeg.log"
            temp_output = make_temp_path(cfg.temp_dir, suffix="." + cfg.output_container)
            final_tmp = path + ".plex_compress_tmp"

            try:
                return _transcode_attempt(path, temp_input, temp_output, ffmpeg_log, final_tmp, cfg, state, logger)
            except DurationError as e:
                last_error = e
                if attempt == 0:
                    logger.warning(f"Duration mismatch on attempt 1, retrying once...")
                    continue
                break
            except InterruptError:
                raise
            except PlexCompressError as e:
                last_error = e
                break

        if last_error:
            state.mark_failed(path, str(last_error))
            logger.error(f"Failed: {path}: {last_error}")
        return False
    finally:
        release_file_lock(lock_fd, path)