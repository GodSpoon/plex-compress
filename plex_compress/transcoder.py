"""Core transcoding logic: copy, transcode, verify, replace."""

import os
import shutil
import subprocess
from typing import Optional

from .config import Config
from .probe import probe_file, get_video_stream, get_audio_streams, get_subtitle_streams, get_duration, get_file_size
from .audio import build_audio_encoder_args
from .video import build_video_encoder_args
from .state import StateDB
from .utils import copy_with_verify, safe_move, make_temp_path, get_free_space_gb
from . import (
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
)


def build_ffmpeg_command(input_path: str, output_path: str, cfg: Config) -> list:
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
        "-max_muxing_queue_size", "1024",
    ]
    # Video
    cmd.extend(build_video_encoder_args(cfg))

    # Audio
    cmd.extend(build_audio_encoder_args(cfg))

    # Subtitles: copy all
    cmd.extend(["-c:s", "copy"])
    cmd.extend(["-c:t", "copy"])

    # Container
    cmd.append(output_path)
    return cmd


def verify_output(input_path: str, output_path: str, cfg: Config) -> None:
    """Verify the transcoded output meets expectations."""
    try:
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

    # Check audio channels
    out_audio = get_audio_streams(out_probe)
    if out_audio:
        for aud in out_audio:
            channels = aud.get("channels", 0)
            layout = aud.get("channel_layout", "")
            if channels > 2 and "stereo" not in layout:
                raise ChannelLayoutError(
                    f"Audio has {channels} channels, layout={layout}"
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


def transcode_file(path: str, cfg: Config, state: StateDB, logger) -> bool:
    """Transcode a single file end-to-end.

    Returns True on success, False on failure.
    """
    if cfg.dry_run:
        logger.info(f"[DRY-RUN] Would transcode: {path}")
        return True

    # Check temp space
    free_gb = get_free_space_gb(cfg.temp_dir)
    file_size = os.path.getsize(path)
    state.mark_started(path, file_size)
    # Need at least 2x file size in temp (source copy + output)
    needed_gb = (file_size * 3) / (1024 ** 3)
    if free_gb < needed_gb:
        raise TempSpaceError(
            f"Only {free_gb:.1f} GB free in temp dir, need ~{needed_gb:.1f} GB for {path}"
        )

    temp_input = make_temp_path(cfg.temp_dir, suffix=os.path.splitext(path)[1])
    temp_output = make_temp_path(cfg.temp_dir, suffix="." + cfg.output_container)

    try:
        # Copy source to local temp
        logger.info(f"Copying {path} to temp...")
        if cfg.verify_checksum:
            copy_with_verify(path, temp_input)
        else:
            shutil.copy2(path, temp_input)


        # Build and run ffmpeg
        cmd = build_ffmpeg_command(temp_input, temp_output, cfg)
        logger.info(f"Transcoding: {' '.join(cmd)}")
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            raise FfmpegError(f"ffmpeg failed: {e.stderr}")

        # Verify output
        if cfg.verify_output:
            logger.info("Verifying output...")
            verify_output(temp_input, temp_output, cfg)

        # Check output is smaller (safety)
        out_size = os.path.getsize(temp_output)
        in_size = os.path.getsize(temp_input)
        if out_size > in_size * 1.1:
            raise SafetyError(
                f"Output size ({out_size}) > 110% of input ({in_size}), skipping replacement"
            )

        if cfg.output_dir:
            # Output-dir mode: preserve relative structure under output_dir
            if cfg.library_path and path.startswith(cfg.library_path):
                rel = os.path.relpath(path, cfg.library_path)
            else:
                rel = os.path.basename(path)
            output_path = os.path.join(cfg.output_dir, rel)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            safe_move(temp_output, output_path)
            state.mark_completed(path, out_size)
            logger.info(f"Done: {path} -> {output_path} ({in_size / 1024 / 1024:.1f} MB -> {out_size / 1024 / 1024:.1f} MB)")
        else:
            # In-place atomic replacement
            backup_path = path + cfg.backup_suffix
            if cfg.keep_backup:
                logger.info(f"Keeping backup at {backup_path}")
                safe_move(path, backup_path)
            else:
                os.remove(path)

            safe_move(temp_output, path)
            state.mark_completed(path, out_size)
            logger.info(f"Done: {path} ({in_size / 1024 / 1024:.1f} MB -> {out_size / 1024 / 1024:.1f} MB)")
        return True

    except Exception as e:
        state.mark_failed(path, str(e))
        logger.error(f"Failed: {path}: {e}")
        return False

    finally:
        # Cleanup temp files
        for p in (temp_input, temp_output):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
