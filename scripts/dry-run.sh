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

# Examples of additional flags you can pass:
#   --include-pattern "S01*"          # Only Season 1 episodes
#   --exclude "Specials"              # Skip Specials directories
#   --limit 20                        # Only estimate first 20 candidates
#   --file "/path/to/one/file.mkv"    # Dry-run a single file
#   --output-dir /tmp/test-output     # Preview output structure
#   --force                           # Include already-completed files
#   --no-verify-checksum              # Skip slow network checksums
#   --parallel-jobs 2                 # Simulate 2 concurrent jobs
