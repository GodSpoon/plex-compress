#!/usr/bin/env bash
set -euo pipefail

# Plex Compress — Dry-run scan with NVENC defaults
# Edit PLEX_ROOT if your mount differs.

PLEX_ROOT="${PLEX_ROOT:-/mnt/truenas-media/Video}"
ENCODER="${ENCODER:-hevc_nvenc}"
QUALITY="${QUALITY:-28}"
PRESET="${PRESET:-p4}"
TEMP_DIR="${TEMP_DIR:-$HOME/tmp/plex_compress}"
STATE_DB="${STATE_DB:-$HOME/.plex_compress/state.db}"
LOG="${LOG:-$HOME/.plex_compress/dry-run.log}"

mkdir -p "$TEMP_DIR" "$(dirname "$STATE_DB")"

python3 -m plex_compress "$PLEX_ROOT" \
  --video-encoder "$ENCODER" \
  --video-quality "$QUALITY" \
  --video-preset "$PRESET" \
  --temp-dir "$TEMP_DIR" \
  --state-db "$STATE_DB" \
  --log "$LOG" \
  --dry-run \
  --verbose \
  "$@"
