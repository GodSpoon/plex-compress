#!/usr/bin/env bash
set -euo pipefail

# Plex Compress — NVENC batch transcode for Arch Linux + RTX 2070 Super
# Edit PLEX_ROOT if your mount differs.

PLEX_ROOT="${PLEX_ROOT:-/mnt/truenas-media/Video}"
ENCODER="${ENCODER:-hevc_nvenc}"
QUALITY="${QUALITY:-28}"
PRESET="${PRESET:-p4}"
PARALLEL="${PARALLEL:-2}"
TEMP_DIR="${TEMP_DIR:-$HOME/tmp/plex_compress}"
STATE_DB="${STATE_DB:-$HOME/.plex_compress/state.db}"
LOG="${LOG:-$HOME/.plex_compress/transcode.log}"
BACKUP="${BACKUP:-1}"
LIMIT="${LIMIT:-}"

mkdir -p "$TEMP_DIR" "$(dirname "$STATE_DB")"

args=(
  --video-encoder "$ENCODER"
  --video-quality "$QUALITY"
  --video-preset "$PRESET"
  --parallel-jobs "$PARALLEL"
  --temp-dir "$TEMP_DIR"
  --state-db "$STATE_DB"
  --log "$LOG"
  --verbose
)

if [[ "$BACKUP" == "1" ]]; then
  args+=(--backup)
fi

# Skip slow checksum verification when copying over network mounts
args+=(--no-verify-checksum)

if [[ -n "$LIMIT" ]]; then
  args+=(--limit "$LIMIT")
fi

python3 -m plex_compress "$PLEX_ROOT" "${args[@]}" "$@"

# Examples of additional flags you can pass:
#   --include-pattern "S01*"          # Only transcode Season 1
#   --file "/path/to/one/file.mkv"    # Transcode a single test file
#   --output-dir /mnt/plex-compressed # Write to a new dir, leave originals
#   --force                           # Re-process already-completed files
#   --exclude "Specials" "Extras"     # Skip these directories
