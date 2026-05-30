#!/usr/bin/env bash
set -euo pipefail

# Plex Compress — macOS runner for M5 Pro (Apple Silicon)
#
# ⚠️  CRITICAL: Apple VideoToolbox does NOT passthrough to Linux containers.
#    This script provides TWO paths:
#
#    1. NATIVE (default) — runs plex-compress directly on macOS using
#       hevc_videotoolbox hardware transcoding. FASTEST. Recommended.
#
#    2. DOCKER — runs inside a Linux ARM64 container with software (libx265)
#       encoding. SLOW but useful for testing container parity.
#
# Usage:
#   ./scripts/docker-run-macos.sh native transcode --limit 5
#   ./scripts/docker-run-macos.sh native dry-run
#   ./scripts/docker-run-macos.sh native watch
#   ./scripts/docker-run-macos.sh native webui
#   ./scripts/docker-run-macos.sh docker transcode --limit 5
#
# Environment overrides:
#   PLEX_ROOT=/Volumes/Media ENCODER=hevc_videotoolbox PARALLEL=4 ./scripts/docker-run-macos.sh native transcode

# ------------------------------------------------------------------------------
# Configurable env vars with defaults
# ------------------------------------------------------------------------------
PLEX_ROOT="${PLEX_ROOT:-/Volumes/Media}"
ENCODER="${ENCODER:-hevc_videotoolbox}"
QUALITY="${QUALITY:-28}"
PRESET="${PRESET:-medium}"
PARALLEL="${PARALLEL:-4}"
TEMP_DIR="${TEMP_DIR:-$HOME/tmp/plex_compress}"
CONFIG_DIR="${CONFIG_DIR:-$HOME/.plex_compress}"
BACKUP="${BACKUP:-1}"
DRY_RUN="${DRY_RUN:-0}"
VERBOSE="${VERBOSE:-1}"
IMAGE="${IMAGE:-plex-compress:latest}"
CONTAINER_TOOL="${CONTAINER_TOOL:-docker}"

MODE="${1:-native}"
shift || true

# ------------------------------------------------------------------------------
# Validate mode
# ------------------------------------------------------------------------------
if [[ "$MODE" != "native" && "$MODE" != "docker" ]]; then
	echo "Usage: $0 <native|docker> [command] [args...]"
	echo ""
	echo "  native  — Run directly on macOS (uses VideoToolbox hardware encoding)"
	echo "  docker  — Run in Linux container (software encoding only)"
	exit 1
fi

# ------------------------------------------------------------------------------
# NATIVE MODE
# ------------------------------------------------------------------------------
if [[ "$MODE" == "native" ]]; then
	# Check dependencies
	if ! command -v ffmpeg &>/dev/null; then
		echo "[macos] ffmpeg not found. Install with:  brew install ffmpeg"
		exit 1
	fi
	if ! command -v python3 &>/dev/null; then
		echo "[macos] python3 not found. Install with:  brew install python"
		exit 1
	fi

	# Auto-detect VideoToolbox if encoder not explicitly set
	if [[ "$ENCODER" == "auto" ]] || [[ "$ENCODER" == "hevc_videotoolbox" ]]; then
		if ffmpeg -encoders 2>/dev/null | grep -q hevc_videotoolbox; then
			ENCODER="hevc_videotoolbox"
			echo "[macos] VideoToolbox encoder available — using hevc_videotoolbox"
		else
			echo "[macos] Warning: hevc_videotoolbox not available in ffmpeg. Install with: brew install ffmpeg"
			ENCODER="libx265"
		fi
	fi

	mkdir -p "$TEMP_DIR" "$CONFIG_DIR"

	args=(
		--video-encoder "$ENCODER"
		--video-quality "$QUALITY"
		--video-preset "$PRESET"
		--parallel-jobs "$PARALLEL"
		--temp-dir "$TEMP_DIR"
		--state-db "$CONFIG_DIR/state.db"
		--log "$CONFIG_DIR/plex_compress.log"
	)
	[[ "$BACKUP" == "1" ]] && args+=(--backup)
	[[ "$VERBOSE" == "1" ]] && args+=(--verbose)

	CMD="${1:-transcode}"
	shift || true

	case "$CMD" in
	health-check)
		echo "[macos] Health check..."
		exec python3 -m plex_compress --health-check --video-encoder "$ENCODER" "${args[@]}" "$@"
		;;
	dry-run)
		echo "[macos] Dry-run scan: $PLEX_ROOT"
		exec python3 -m plex_compress "$PLEX_ROOT" --dry-run "${args[@]}" "$@"
		;;
	transcode)
		echo "[macos] Transcoding: $PLEX_ROOT (encoder=$ENCODER)"
		exec python3 -m plex_compress "$PLEX_ROOT" "${args[@]}" "$@"
		;;
	watch)
		echo "[macos] Watch mode: $PLEX_ROOT (encoder=$ENCODER)"
		exec python3 -m plex_compress "$PLEX_ROOT" --watch --watch-interval 300 "${args[@]}" "$@"
		;;
	webui)
		echo "[macos] Starting Web UI on http://localhost:8765"
		exec python3 -m plex_compress.webui --host 0.0.0.0 --port 8765 "$@"
		;;
	*)
		echo "[macos] Unknown command: $CMD"
		echo "Valid: health-check, dry-run, transcode, watch, webui"
		exit 1
		;;
	esac
fi

# ------------------------------------------------------------------------------
# DOCKER MODE (Linux ARM64 container, software encoding)
# ------------------------------------------------------------------------------
if [[ "$MODE" == "docker" ]]; then
	echo "[macos-docker] ⚠️  Running in Docker with software encoding (libx265)."
	echo "[macos-docker] For hardware transcoding on M5 Pro, use:  $0 native ..."
	echo ""

	if ! command -v docker &>/dev/null && command -v podman &>/dev/null; then
		CONTAINER_TOOL="podman"
	fi

	if ! "$CONTAINER_TOOL" image exists "$IMAGE" 2>/dev/null; then
		echo "[macos-docker] Building $IMAGE for linux/arm64..."
		"$CONTAINER_TOOL" build --platform linux/arm64 -t "$IMAGE" -f Dockerfile .
	fi

	mkdir -p "$TEMP_DIR" "$CONFIG_DIR"

	ENV_ARGS=(
		-e "PLEX_COMPRESS_ENCODER=libx265"
		-e "PLEX_COMPRESS_QUALITY=$QUALITY"
		-e "PLEX_COMPRESS_PRESET=$PRESET"
		-e "PLEX_COMPRESS_PARALLEL=$PARALLEL"
		-e "PLEX_COMPRESS_TEMP_DIR=/tmp/plex_compress"
		-e "PLEX_COMPRESS_STATE_DB=/config/state.db"
		-e "PLEX_COMPRESS_LOG=/config/plex_compress.log"
		-e "PLEX_COMPRESS_LIBRARY_PATH=/mnt/plex"
		-e "PLEX_COMPRESS_BACKUP=$BACKUP"
		-e "PLEX_COMPRESS_DRY_RUN=$DRY_RUN"
		-e "PLEX_COMPRESS_VERBOSE=$VERBOSE"
	)

	VOL_ARGS=(
		-v "$PLEX_ROOT:/mnt/plex:rw"
		-v "$CONFIG_DIR:/config"
		-v "$TEMP_DIR:/tmp/plex_compress"
	)

	CMD="${1:-transcode}"
	shift || true

	# For watch/webui long-running, use compose
	if [[ "$CMD" == "webui" ]]; then
		export PLEX_ROOT ENCODER=libx265 QUALITY PRESET PARALLEL TEMP_DIR CONFIG_DIR BACKUP DRY_RUN VERBOSE
		"$CONTAINER_TOOL" compose -f docker-compose.yml -f docker-compose.macos.yml up -d "$@"
		echo "[macos-docker] Web UI available at http://localhost:8765"
		exit 0
	fi

	# SC2145: avoid mixing string and array; print command separately
	printf '[macos-docker] Running: %s run --rm %s %s' "$CONTAINER_TOOL" "$IMAGE" "$CMD"
	printf ' %q' "$@"
	printf '\n'
	exec "$CONTAINER_TOOL" run --rm \
		--platform linux/arm64 \
		"${ENV_ARGS[@]}" \
		"${VOL_ARGS[@]}" \
		"$IMAGE" \
		"$CMD" "$@"
fi
