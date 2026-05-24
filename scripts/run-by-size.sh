#!/usr/bin/env bash
set -euo pipefail

# Plex Compress — size-sorted batch transcode
# Processes shows from largest to smallest by total video file size.
# Edit PLEX_ROOT if your mount differs.

PLEX_ROOT="${PLEX_ROOT:-/mnt/truenas-media/Video}"
ENCODER="${ENCODER:-hevc_nvenc}"
QUALITY="${QUALITY:-28}"
PRESET="${PRESET:-p4}"
PARALLEL="${PARALLEL:-2}"
TEMP_DIR="${TEMP_DIR:-$HOME/tmp/plex_compress}"
STATE_DB="${STATE_DB:-$HOME/.plex_compress/state.db}"
LOG="${LOG:-$HOME/.plex_compress/transcode.log}"
LIMIT="${LIMIT:-}"

mkdir -p "$TEMP_DIR" "$(dirname "$STATE_DB")"

# Build show size list and sort by size descending
SHOW_ROOT="${PLEX_ROOT}/TV Shows"
if [ ! -d "$SHOW_ROOT" ]; then
    echo "Show root not found: $SHOW_ROOT"
    exit 1
fi

echo "Scanning show sizes..."
TMP_LIST=$(mktemp)
trap 'rm -f "$TMP_LIST"' EXIT

for show_dir in "$SHOW_ROOT"/*; do
    [ -d "$show_dir" ] || continue
    size=$(find "$show_dir" -type f \( -name "*.mkv" -o -name "*.mp4" -o -name "*.avi" -o -name "*.m4v" -o -name "*.mov" \) -printf "%s\n" 2>/dev/null | awk '{sum+=$1} END {print sum+0}')
    echo "$size $show_dir"
done > "$TMP_LIST"

SORTED=$(sort -t' ' -k1 -rn "$TMP_LIST")
TOTAL_SHOWS=$(echo "$SORTED" | wc -l)

echo "Found $TOTAL_SHOWS shows. Processing largest first."
echo ""

# Start a new session in the state DB
python3 -c "
import sys
sys.path.insert(0, '.')
from plex_compress.state import StateDB
import os
db = os.path.expanduser('${STATE_DB}')
s = StateDB(db)
sid = s.start_session(name='run-by-size')
print(f'Started session {sid}')
"

idx=0
while IFS= read -r line; do
    size=${line%% *}
    show_dir=${line#* }
    idx=$((idx + 1))
    size_gb=$(awk "BEGIN {printf \"%.1f\", $size/1024/1024/1024}")
    show_name=$(basename "$show_dir")
    echo "[$idx/$TOTAL_SHOWS] $show_name ($size_gb GB)"

    args=(
        --video-encoder "$ENCODER"
        --video-quality "$QUALITY"
        --video-preset "$PRESET"
        --parallel-jobs "$PARALLEL"
        --temp-dir "$TEMP_DIR"
        --state-db "$STATE_DB"
        --log "$LOG"
        --verbose
        --no-verify-checksum
    )

    if [ -n "$LIMIT" ]; then
        args+=(--limit "$LIMIT")
    fi

    python3 -m plex_compress "$show_dir" "${args[@]}" || true

done <<< "$SORTED"

echo ""
echo "All shows processed."
