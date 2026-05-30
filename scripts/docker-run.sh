#!/usr/bin/env bash
set -euo pipefail

# Plex Compress — Docker / Podman runner for Willaird 7 (w7)
# Linux host with NVIDIA GPU (NVENC) support.
#
# Prerequisites:
#   - Docker or Podman installed
#   - nvidia-container-toolkit installed (for NVENC)
#   - Plex library mounted at PLEX_ROOT
#
# Usage:
#   ./scripts/docker-run.sh transcode --limit 10
#   ./scripts/docker-run.sh dry-run
#   ./scripts/docker-run.sh health-check
#   ./scripts/docker-run.sh watch
#   ./scripts/docker-run.sh webui   # starts Web UI + watch mode via compose
#
# Environment overrides:
#   PLEX_ROOT=/mnt/plex ENCODER=hevc_nvenc PARALLEL=2 ./scripts/docker-run.sh transcode

# ------------------------------------------------------------------------------
# Configurable env vars with defaults
# ------------------------------------------------------------------------------
PLEX_ROOT="${PLEX_ROOT:-/mnt/truenas-media/Video}"
ENCODER="${ENCODER:-auto}"
QUALITY="${QUALITY:-28}"
PRESET="${PRESET:-p4}"
PARALLEL="${PARALLEL:-2}"
TEMP_DIR="${TEMP_DIR:-$HOME/tmp/plex_compress}"
CONFIG_DIR="${CONFIG_DIR:-$HOME/.plex_compress}"
BACKUP="${BACKUP:-1}"
DRY_RUN="${DRY_RUN:-0}"
VERBOSE="${VERBOSE:-1}"
IMAGE="${IMAGE:-plex-compress:latest}"
CONTAINER_TOOL="${CONTAINER_TOOL:-docker}" # or 'podman'

# ------------------------------------------------------------------------------
# Auto-detect Podman if available and Docker isn't
# ------------------------------------------------------------------------------
if ! command -v docker &>/dev/null && command -v podman &>/dev/null; then
	CONTAINER_TOOL="podman"
fi

# ------------------------------------------------------------------------------
# Build image if missing
# ------------------------------------------------------------------------------
if ! "$CONTAINER_TOOL" image exists "$IMAGE" 2>/dev/null; then
	echo "[docker-run] Image $IMAGE not found, building..."
	"$CONTAINER_TOOL" build -t "$IMAGE" -f Dockerfile .
fi

# ------------------------------------------------------------------------------
# Ensure host directories exist
# ------------------------------------------------------------------------------
mkdir -p "$TEMP_DIR" "$CONFIG_DIR"

# ------------------------------------------------------------------------------
# Common Docker/Podman args
# ------------------------------------------------------------------------------
GPU_ARGS=()
if [[ "$CONTAINER_TOOL" == "podman" ]]; then
	# Podman uses --device for GPUs with nvidia-container-toolkit
	GPU_ARGS=(--device nvidia.com/gpu=all)
else
	# Docker uses --runtime=nvidia or --gpus all
	GPU_ARGS=(--runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all -e NVIDIA_DRIVER_CAPABILITIES=compute,video,utility)
fi

ENV_ARGS=(
	-e "PLEX_COMPRESS_ENCODER=$ENCODER"
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

# ------------------------------------------------------------------------------
# Compose path for long-running services (webui + watch)
# ------------------------------------------------------------------------------
CMD="${1:-transcode}"

if [[ "$CMD" == "webui" ]]; then
	echo "[docker-run] Starting Web UI + Watch mode via compose..."
	shift || true
	export PLEX_ROOT ENCODER QUALITY PRESET PARALLEL TEMP_DIR CONFIG_DIR BACKUP DRY_RUN VERBOSE
	if [[ "$CONTAINER_TOOL" == "podman" ]]; then
		podman compose -f docker-compose.yml -f docker-compose.nvidia.yml up -d "$@"
	else
		docker compose -f docker-compose.yml -f docker-compose.nvidia.yml up -d "$@"
	fi
	echo "[docker-run] Web UI available at http://localhost:8765"
	exit 0
fi

if [[ "$CMD" == "compose-down" ]]; then
	echo "[docker-run] Stopping compose stack..."
	if [[ "$CONTAINER_TOOL" == "podman" ]]; then
		podman compose -f docker-compose.yml -f docker-compose.nvidia.yml down "$@"
	else
		docker compose -f docker-compose.yml -f docker-compose.nvidia.yml down "$@"
	fi
	exit 0
fi

# ------------------------------------------------------------------------------
# One-shot container run
# ------------------------------------------------------------------------------
# SC2145: avoid mixing string and array; print command separately
printf '[docker-run] Running: %s run %s %s' "$CONTAINER_TOOL" "$IMAGE" "$CMD"
printf ' %q' "$@"
printf '\n'
exec "$CONTAINER_TOOL" run --rm \
	"${GPU_ARGS[@]}" \
	"${ENV_ARGS[@]}" \
	"${VOL_ARGS[@]}" \
	"$IMAGE" \
	"$CMD" "$@"
