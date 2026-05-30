# plex_compress

Transcode Plex libraries to space-efficient HEVC (H.265) with stereo-normalized audio.

- **Video:** HEVC via `libx265` (CPU), `hevc_videotoolbox` (Apple Silicon), or **`hevc_nvenc`** (NVIDIA RTX 20-series+)
- **Audio:** Default track -> AAC-LC stereo @ 160 kbps with EBU R128 loudnorm (`I=-16 LUFS`). All other audio tracks copied as-is.
- **Container:** MKV (subtitle/attachment/chapter preservation)

## Why

Plex libraries accumulate large H.264 + 5.1 AC3/E-AC3 files. Pre-transcoding to HEVC + stereo AAC saves **30-55%** disk space, eliminates on-the-fly audio downmixing, and direct-plays on virtually every client.

## Install

Requires **ffmpeg** compiled with the encoder you plan to use:

```bash
# macOS (VideoToolbox)
brew install ffmpeg

# Arch Linux (NVENC)
sudo pacman -S ffmpeg nvidia-utils
```

For NVENC, verify your GPU is visible:

```bash
ffmpeg -encoders 2>/dev/null | grep hevc_nvenc
nvidia-smi
```

## Quick Start

```bash
# Clone and enter the repo
git clone https://github.com/GodSpoon/plex-compress.git
cd plex-compress

# Health check (recommended before any batch run)
python3 -m plex_compress --health-check --video-encoder hevc_nvenc

# Dry-run scan to see what would be processed
python3 -m plex_compress /mnt/plex/TV --dry-run --video-encoder hevc_nvenc

# Intelligent dry-run: persists metadata, estimates per-file savings, skips unchanged files
python3 -m plex_compress /mnt/plex/TV --dry-run --intelligent-scan --video-encoder hevc_nvenc

# Transcode a single file to test quality
python3 -m plex_compress /mnt/plex/TV \
  --file "/mnt/plex/TV/Show/Season 01/E01.mkv" \
  --output-dir /tmp/test-output \
  --video-encoder hevc_nvenc

# Batch transcode with backup (recommended first run)
python3 -m plex_compress /mnt/plex/TV \
  --video-encoder hevc_nvenc \
  --video-quality 28 \
  --video-preset p4 \
  --parallel-jobs 2 \
  --backup \
  --limit 10

# Autonomous watch mode (processes new files as they appear)
python3 -m plex_compress /mnt/plex/TV \
  --watch \
  --watch-interval 300 \
  --video-encoder hevc_nvenc \
  --backup

# Generate a comprehensive report of what's been done and what's pending
python3 -m plex_compress --report
python3 -m plex_compress --report --report-format json
```

## Web UI

A full-featured web dashboard is included for visual monitoring and control.

```bash
# Start the web UI server
python3 -m plex_compress.webui

# Or specify host/port
python3 -m plex_compress.webui --host 0.0.0.0 --port 8765
```

Then open **http://localhost:8765** (or your machine's IP for LAN access).

### Web UI Features

| Feature | Description |
|---------|-------------|
| **Dashboard** | Real-time stats (completed, failed, pending, space saved), progress bar, current activity |
| **Queue** | Pending files sorted by predicted savings with codec/resolution info |
| **Library** | Searchable/filterable view of all tracked files by status |
| **Reports** | Charts (by codec, by resolution), prediction accuracy, scan history, top candidates |
| **Configuration** | Full config form with encoder presets, paths, safety toggles |
| **Live Logs** | Real-time log stream from the server |
| **Extensions** | Plugin system for custom routes and event handlers |
| **Command Palette** | `Ctrl/Cmd + K` — fuzzy-search all actions and navigation |
| **Keyboard Shortcuts** | `g d` Dashboard, `g q` Queue, `h` Health Check, `t` Transcode, `r` Refresh, etc. |
| **Server-Sent Events** | Live progress updates without polling |
| **Mobile Responsive** | Collapsible sidebar, stacked layouts |

### Web UI Design System

The UI uses a three-layer token architecture (Primitive → Semantic → Component):
- Dark theme with glassmorphism sidebar
- Gradient buttons with glow effects
- Skeleton loaders on first load
- Empty states with icons for all tables
- `prefers-reduced-motion` and `prefers-contrast` support

## CLI Options

### Paths and Targets

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `library_path` | — | Root directory to scan and transcode | *(required unless `--file` used)* |
| `--file` | `-f` | Process a single file instead of scanning | `None` |
| `--output-dir` | `-o` | Write outputs here instead of replacing originals in-place | `None` |
| `--include-pattern` | — | Glob pattern to filter scanned files (e.g. `*.mkv`, `S01*`) | `None` |
| `--exclude` | — | Directory names to skip (repeatable) | `[]` |
| `--temp-dir` | — | Local temp directory for transcoding | `~/tmp/plex_compress` |

### Video and Audio

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--video-encoder` | — | `libx265`, `hevc_videotoolbox`, `hevc_nvenc` | `libx265` |
| `--video-quality` | — | CRF/CQ 0-51 (x265/NVENC), 0-100 (VideoToolbox) | `28` |
| `--video-preset` | — | Encoder preset (`medium`, `p4`, `fast`, etc.) | `medium` |
| `--audio-bitrate` | — | Audio bitrate | `160k` |

### Control and Safety

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--dry-run` | — | Scan and report only; do not transcode | `False` |
| `--backup` | — | Keep `.plex_compress_backup` originals | `False` |
| `--limit` | — | Max files to process | unlimited |
| `--parallel-jobs` | — | Concurrent transcodes | `1` |
| `--force` | — | Re-process files already marked completed in state DB | `False` |
| `--no-verify-checksum` | — | Skip SHA-256 checksum when copying over network mounts | `False` |
| `--reset-failed` | — | Reset failed entries in state DB and retry them | `False` |
| `--min-file-age` | — | Skip files modified within last N seconds | `300` |
| `--no-file-locking` | — | Allow concurrent processing of same file (not recommended) | `False` |
| `--no-post-replace-verify` | — | Skip post-replace verification of final file | `False` |

### Intelligent Scan and Reporting

| Flag | Description | Default |
|------|-------------|---------|
| `--intelligent-scan` | Persist rich metadata, incremental re-scan, per-file savings prediction | `False` |
| `--report` | Generate comprehensive report and exit | `False` |
| `--report-format` | `text` or `json` | `text` |

### Autonomous Operation

| Flag | Description | Default |
|------|-------------|---------|
| `--watch` | Monitor library for new files and auto-process | `False` |
| `--watch-interval` | Polling interval in seconds for watch mode | `60` |
| `--health-check` | Run pre-flight validation and exit | `False` |

### Persistence

| Flag | Description | Default |
|------|-------------|---------|
| `--state-db` | SQLite resume database path | `~/.plex_compress/state.db` |
| `--log` | Log file path | `None` (stdout only) |
| `--verbose` / `-v` | Enable debug logging | `False` |

## Usage Examples

### Health Check (Pre-Flight)

```bash
python3 -m plex_compress --health-check --video-encoder hevc_nvenc --verbose
```

Validates ffmpeg, ffprobe, hardware encoder, temp space, state DB, and audio filter chain before any destructive work.

### Scan and Estimate Savings

```bash
python3 -m plex_compress /mnt/plex/TV \
  --dry-run \
  --video-encoder hevc_nvenc \
  --verbose
```

### Intelligent Scan (Recommended)

```bash
python3 -m plex_compress /mnt/plex/TV \
  --dry-run \
  --intelligent-scan \
  --video-encoder hevc_nvenc
```

The intelligent scanner:
- **Persists rich metadata** for every file (codec, resolution, bitrate, channels, duration)
- **Skips unchanged files** on re-scan by comparing `mtime:size` hash — no re-probing
- **Predicts per-file savings** based on source codec and bitrate tier
- **Sorts the queue by predicted savings** — biggest space wins are processed first
- **Tracks prediction accuracy** so the model improves over time

### Generate a Report

```bash
# Human-readable report
python3 -m plex_compress --report

# JSON for scripting
python3 -m plex_compress --report --report-format json

# Use a specific state DB
python3 -m plex_compress --report --state-db /path/to/custom.db
```

Report includes:
- **Summary**: total/completed/failed/skipped, space saved, prediction accuracy
- **By codec**: per-source-codec breakdown (count, total saved, average saved)
- **By resolution**: per-resolution breakdown
- **Top pending**: highest-value candidates waiting to be processed
- **Scan history**: recent scan snapshots for trend analysis

### Transcode a Single File (Test Quality)

```bash
python3 -m plex_compress /mnt/plex/TV \
  --file "/mnt/plex/TV/S.W.A.T./Season 01/S01E02.mkv" \
  --output-dir /tmp/test-output \
  --video-encoder hevc_nvenc \
  --video-quality 28 \
  --video-preset p4
```

This leaves the original untouched and writes the result to `/tmp/test-output/`.

### Transcode Only Season 1 Files

```bash
python3 -m plex_compress /mnt/plex/TV \
  --include-pattern "S01*" \
  --video-encoder hevc_nvenc \
  --backup
```

### Output to a Different Directory (Non-Destructive)

```bash
python3 -m plex_compress /mnt/plex/TV \
  --output-dir /mnt/plex-compressed/TV \
  --video-encoder hevc_nvenc \
  --no-verify-checksum
```

Preserves the original directory structure under the output directory.

### Force Re-Process Already-Completed Files

```bash
python3 -m plex_compress /mnt/plex/TV \
  --force \
  --video-encoder hevc_nvenc \
  --limit 5
```

### Overnight Batch with Resume

```bash
python3 -m plex_compress /mnt/plex/TV \
  --video-encoder hevc_nvenc \
  --video-quality 28 \
  --video-preset p4 \
  --parallel-jobs 2 \
  --backup \
  --no-verify-checksum \
  --log ~/.plex_compress/overnight.log
```

If interrupted (Ctrl-C), re-run the same command and it resumes where it left off using the state database.

### Autonomous Watch Mode

```bash
python3 -m plex_compress /mnt/plex/TV \
  --watch \
  --watch-interval 300 \
  --video-encoder hevc_nvenc \
  --backup \
  --log ~/.plex_compress/watch.log
```

Runs indefinitely, polling the library every 5 minutes for new files and auto-processing them. Safe to run as a systemd service or tmux session.

### Watch Mode with Intelligent Scanning

```bash
python3 -m plex_compress /mnt/plex/TV \
  --watch \
  --watch-interval 300 \
  --intelligent-scan \
  --video-encoder hevc_nvenc \
  --backup
```

Only processes new or changed files. Already-scanned files are skipped via hash comparison.

## NVIDIA RTX 2070 Super (Turing) Tuning

The RTX 2070 Super has a dedicated **NVDEC/NVENC** chip. Recommended settings:

```bash
python3 -m plex_compress /mnt/plex \
  --video-encoder hevc_nvenc \
  --video-quality 28 \
  --video-preset p4 \
  --parallel-jobs 2
```

- **`--video-quality 28`**: CQ 28 is visually transparent for 1080p TV content.
- **`--video-preset p4`**: Balanced quality/speed. Turing scales well from `p1` (fastest) to `p7` (best).
- **`--parallel-jobs 2`**: The 2070 Super NVENC session limit is high; 2 jobs keeps GPU utilization ~80-95% without stalling PCIe copy.
- **B-frames**: Automatically enabled (`-bf 4`) for Turing+ HEVC NVENC.

## Arch Linux Host Workflow

1. **Mount Plex share** (e.g. via NFS or CIFS in `/etc/fstab`):
   ```
   //nas/plex /mnt/plex cifs credentials=/etc/.smbcred,uid=sam,gid=sam 0 0
   ```

2. **Health check before any batch:**
   ```bash
   python3 -m plex_compress --health-check --video-encoder hevc_nvenc
   ```

3. **Intelligent dry-run to estimate savings:**
   ```bash
   python3 -m plex_compress /mnt/plex/TV --dry-run --intelligent-scan --video-encoder hevc_nvenc
   ```

4. **Batch transcode overnight:**
   ```bash
   ./scripts/run-nvenc.sh
   ```

5. **Check progress with a report:**
   ```bash
   python3 -m plex_compress --report
   ```

Logs and state DB are written to `~/.plex_compress/` so you can resume after reboot or interrupt.

## Safety Architecture

### Data Loss Prevention

- **Atomic replacement**: temp file written to same filesystem, then `os.replace()` atomically swaps. No partial writes possible.
- **Backup mode**: `--backup` keeps original as `.plex_compress_backup`. Post-replace verification can rollback if the new file is corrupt.
- **Post-replace verification**: after atomic swap, the final file is re-probed to confirm it's valid. If not, and a backup exists, automatic rollback occurs.
- **File locking**: prevents multiple processes from transcoding the same file simultaneously.
- **File age guard**: skips files modified within the last 5 minutes, avoiding race conditions with downloaders (Sonarr/Radarr) still writing the file.

### Quality Verification

Every output is verified before the original is replaced:

- **Video codec**: must be HEVC (not H.264 passthrough)
- **Duration**: output within ±2 seconds of input (with auto-retry on transient GPU failures)
- **Audio streams**: no audio tracks dropped
- **Audio layout**: default track must be stereo (no >2 channel surround leaking through)
- **Subtitle streams**: no subtitle tracks dropped
- **Attachments**: no font/image attachments dropped
- **Chapters**: all chapters preserved
- **Size guard**: rejects if output > 110% of input (catches egregious bloat)
- **Efficiency guard**: rejects if output > 95% of input (skips already-efficient sources)

### Stream Preservation

- **Default audio track**: downmixed to stereo + EBU R128 loudnorm (`-16 LUFS`, `-1.5 dBTP`, `11 LRA`)
- **Other audio tracks**: copied as-is (commentary, alternate languages, descriptive audio all preserved)
- **Subtitles**: copied without re-encoding
- **Attachments**: copied (fonts, cover images)
- **Chapters**: preserved with `-map_chapters 0`
- **Metadata**: preserved with `-map_metadata 0` (titles, language tags, disposition flags)

### Resume and Concurrency

- **SQLite state DB**: tracks every file's status (`pending`/`in_progress`/`completed`/`failed`/`skipped`)
- **WAL mode**: SQLite Write-Ahead Logging + 5-second busy timeout for safe concurrent access
- **Stale job reset**: auto-resets `in_progress` entries to `pending` on startup (crash recovery)
- **Retry logic**: files failing with duration mismatch are retried once before being marked failed

### Intelligent Features

- **Incremental scan**: unchanged files are skipped on re-scan via `mtime:size` hash comparison
- **Per-file savings prediction**: estimates space savings based on source codec, bitrate, and audio channel count
- **Priority queue**: pending files sorted by predicted savings — process the biggest wins first
- **Prediction accuracy tracking**: compares predicted vs actual savings to improve estimates over time
- **Scan history**: records every scan with candidate counts and estimated savings for trend analysis
- **Rich metadata**: persists codec, resolution, bitrate, channels, duration for every file

## State Database Schema

The SQLite database (`~/.plex_compress/state.db`) uses a versioned schema:

### `files` table
| Column | Type | Description |
|--------|------|-------------|
| `path` | TEXT | Unique file path |
| `status` | TEXT | `pending` / `in_progress` / `completed` / `failed` / `skipped` |
| `original_size` | INTEGER | Source file size in bytes |
| `output_size` | INTEGER | Transcoded file size in bytes |
| `video_codec` | TEXT | Source video codec (e.g. `h264`) |
| `video_width` | INTEGER | Video width in pixels |
| `video_height` | INTEGER | Video height in pixels |
| `video_bitrate` | INTEGER | Source video bitrate (bps) |
| `audio_codec` | TEXT | Source audio codec (e.g. `ac3`) |
| `audio_channels` | INTEGER | Source audio channel count |
| `duration` | REAL | Duration in seconds |
| `predicted_savings_bytes` | INTEGER | Estimated savings before transcoding |
| `actual_savings_bytes` | INTEGER | Realized savings after transcoding |
| `scan_hash` | TEXT | `mtime:size` hash for incremental scanning |

### `scans` table
Records each library scan for trend analysis.

### `sessions` table
Records each batch session for resume and statistics.

## Tests

```bash
# Run all tests
python3 -m pytest tests/ -v

# Run with coverage
python3 -m pytest tests/ --cov=plex_compress --cov-report=term-missing

# Run only intelligence tests
python3 -m pytest tests/test_intelligence.py -v
```

## License

MIT
