#!/usr/bin/env bash
set -euo pipefail

# Plex Compress — Docker / Podman Entrypoint
# Auto-detects hardware encoders and dispatches to the right mode.

ENCODER="${PLEX_COMPRESS_ENCODER:-auto}"
QUALITY="${PLEX_COMPRESS_QUALITY:-28}"
PRESET="${PLEX_COMPRESS_PRESET:-medium}"
PARALLEL="${PLEX_COMPRESS_PARALLEL:-1}"
TEMP_DIR="${PLEX_COMPRESS_TEMP_DIR:-/tmp/plex_compress}"
STATE_DB="${PLEX_COMPRESS_STATE_DB:-/config/state.db}"
LOG="${PLEX_COMPRESS_LOG:-/config/plex_compress.log}"
LIBRARY="${PLEX_COMPRESS_LIBRARY_PATH:-/mnt/plex}"
BACKUP="${PLEX_COMPRESS_BACKUP:-0}"
DRY_RUN="${PLEX_COMPRESS_DRY_RUN:-0}"
VERBOSE="${PLEX_COMPRESS_VERBOSE:-0}"
VERIFY_CHECKSUM="${PLEX_COMPRESS_VERIFY_CHECKSUM:-0}"

# ------------------------------------------------------------------------------
# Auto-detect encoder if set to 'auto'
# ------------------------------------------------------------------------------
if [[ "$ENCODER" == "auto" ]]; then
	echo "[entrypoint] Auto-detecting best encoder..."

	# Check NVIDIA NVENC
	# Note: avoid grep -q with pipefail — it closes the pipe early and
	# causes ffmpeg to get SIGPIPE, making the pipeline return non-zero.
	if ffmpeg -encoders 2>/dev/null | grep hevc_nvenc >/dev/null 2>&1; then
		if nvidia-smi -L &>/dev/null || [[ -c /dev/nvidiactl ]]; then
			ENCODER="hevc_nvenc"
			echo "[entrypoint] NVIDIA GPU detected — using hevc_nvenc"
		else
			echo "[entrypoint] hevc_nvenc compiled in but no NVIDIA runtime visible"
		fi
	fi

	# Check Apple VideoToolbox (only works on native macOS, not in Linux containers)
	if [[ "$ENCODER" == "auto" ]] && ffmpeg -encoders 2>/dev/null | grep hevc_videotoolbox >/dev/null 2>&1; then
		# In a Linux container, videotoolbox won't work even if compiled in
		if [[ "$(uname -s)" == "Darwin" ]]; then
			ENCODER="hevc_videotoolbox"
			echo "[entrypoint] macOS host detected — using hevc_videotoolbox"
		else
			echo "[entrypoint] hevc_videotoolbox available but running inside Linux container; skipping"
		fi
	fi

	# Fallback to software
	if [[ "$ENCODER" == "auto" ]]; then
		ENCODER="libx265"
		echo "[entrypoint] No hardware encoder available — falling back to libx265 (CPU)"
	fi
fi

# ------------------------------------------------------------------------------
# Ensure directories exist
# ------------------------------------------------------------------------------
mkdir -p "$TEMP_DIR" "$(dirname "$STATE_DB")" "$(dirname "$LOG")"

# ------------------------------------------------------------------------------
# Build common args
# ------------------------------------------------------------------------------
args=(
	--video-encoder "$ENCODER"
	--video-quality "$QUALITY"
	--video-preset "$PRESET"
	--parallel-jobs "$PARALLEL"
	--temp-dir "$TEMP_DIR"
	--state-db "$STATE_DB"
	--log "$LOG"
)

[[ "$BACKUP" == "1" ]] && args+=(--backup)
[[ "$DRY_RUN" == "1" ]] && args+=(--dry-run)
[[ "$VERBOSE" == "1" ]] && args+=(--verbose)
[[ "$VERIFY_CHECKSUM" != "1" ]] && args+=(--no-verify-checksum)

# ------------------------------------------------------------------------------
# Subcommand dispatch
# ------------------------------------------------------------------------------
CMD="${1:-help}"
shift || true

case "$CMD" in
help)
	echo "Plex Compress Container"
	echo "======================="
	echo ""
	echo "This container does NOT start transcoding by default."
	echo "You must explicitly choose a command:"
	echo ""
	echo "  health-check    Run pre-flight validation"
	echo "  dry-run         Scan library and report candidates (safe)"
	echo "  transcode       Batch transcode ONCE then exit"
	echo "  watch           Start watch mode (runs forever, auto-transcodes)"
	echo "  webui           Start Web UI server on :8765"
	echo "  shell           Drop to bash"
	echo "  ffmpeg          Passthrough to ffmpeg"
	echo ""
	echo "Current config:"
	echo "  Library:   $LIBRARY"
	echo "  Encoder:   $ENCODER"
	echo "  Quality:   $QUALITY"
	echo "  Preset:    $PRESET"
	echo "  Parallel:  $PARALLEL"
	echo "  Temp dir:  $TEMP_DIR"
	echo "  State DB:  $STATE_DB"
	echo ""
	echo "Examples:"
	echo "  podman run --rm plex-compress health-check"
	echo "  podman run --rm plex-compress dry-run"
	echo "  podman run --rm plex-compress transcode --limit 10"
	echo "  podman run --rm plex-compress watch"
	exit 0
	;;

health-check)
	echo "[entrypoint] Running health check..."
	exec /opt/venv/bin/python -m plex_compress --health-check --video-encoder "$ENCODER" "${args[@]}" "$@"
	;;

dry-run)
	echo "[entrypoint] Dry-run scan: $LIBRARY"
	exec /opt/venv/bin/python -m plex_compress "$LIBRARY" --dry-run "${args[@]}" "$@"
	;;

transcode)
	echo "[entrypoint] Transcoding: $LIBRARY (encoder=$ENCODER)"
	exec /opt/venv/bin/python -m plex_compress "$LIBRARY" "${args[@]}" "$@"
	;;

watch)
	echo "[entrypoint] Watch mode: $LIBRARY (encoder=$ENCODER)"
	exec /opt/venv/bin/python -m plex_compress "$LIBRARY" --watch --watch-interval 300 "${args[@]}" "$@"
	;;

webui)
	echo "[entrypoint] Starting Web UI on 0.0.0.0:8765"
	exec /opt/venv/bin/python -m plex_compress.webui --host 0.0.0.0 --port 8765 "$@"
	;;

shell | bash | sh)
	echo "[entrypoint] Dropping to shell..."
	exec /bin/bash "$@"
	;;

ffmpeg)
	# Passthrough to ffmpeg for debugging
	exec ffmpeg "$@"
	;;

*)
	echo "[entrypoint] Unknown command: $CMD"
	echo "Valid commands: health-check, dry-run, transcode, watch, webui, shell, ffmpeg"
	exit 1
	;;
esac
