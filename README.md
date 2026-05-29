# plex_compress

Transcode Plex libraries to space-efficient HEVC (H.265) with stereo-normalized audio.

- **Video:** HEVC via `libx265` (CPU), `hevc_videotoolbox` (Apple Silicon), or **`hevc_nvenc`** (NVIDIA RTX 20-series+)
- **Audio:** Default track -> AAC-LC stereo @ 160 kbps with EBU R128 loudnorm (`I=-16 LUFS`). All other audio tracks copied as-is.
- **Container:** MKV (subtitle/attachment/chapter preservation)

## Why

Plex libraries accumulate large H.264 + 5.1 AC3/E-AC3 files. Pre-transcoding to HEVC + stereo AAC saves **30-45%** disk space, eliminates on-the-fly audio downmixing, and direct-plays on virtually every client.

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
```

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

3. **Dry-run to estimate savings:**
   ```bash
   ./scripts/dry-run.sh
   ```

4. **Batch transcode overnight:**
   ```bash
   ./scripts/run-nvenc.sh
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

## Web UI

A built-in lightweight web dashboard is available for setup, monitoring, and operation.

### Start the Web UI

```bash
# Default: http://0.0.0.0:8765/
python3 -m plex_compress.webui

# Or use the convenience script
python3 scripts/webui.py

# Custom host/port
python3 -m plex_compress.webui --host 127.0.0.1 --port 8080
```

### Features

- **Dashboard** — real-time stats, progress, current activity, recent/failed files, per-show breakdown
- **Queue** — pending candidates sorted by predicted space savings
- **Library** — searchable/filterable view of all tracked files
- **Reports** — charts by codec and resolution, scan history, prediction accuracy
- **Configuration** — full settings form with encoder presets (NVENC, VideoToolbox, CPU)
- **Live Logs** — streaming log tail via Server-Sent Events
- **Extensions** — drop `.py` files into `~/.plex_compress/webui/extensions/` to add custom routes and event listeners

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | Current stats, runner state, recent files |
| GET | `/api/queue` | Top pending candidates |
| GET | `/api/recent` | Recent completed files |
| GET | `/api/failed` | Failed files |
| GET | `/api/report` | Full report (summary, by codec, by resolution, scan history) |
| GET | `/api/logs` | Recent buffered log lines |
| GET | `/api/events` | SSE stream for real-time updates |
| GET | `/api/config` | Current configuration |
| POST | `/api/config` | Update configuration |
| POST | `/api/health-check` | Run pre-flight health check |
| POST | `/api/scan` | Start scan (dry-run or intelligent) |
| POST | `/api/transcode` | Start batch transcode |
| POST | `/api/watch` | Start/stop watch mode |
| POST | `/api/stop` | Stop current operation |
| POST | `/api/reset-failed` | Reset failed entries to pending |
| GET | `/api/extensions` | List loaded extensions |

### Extending the Web UI

Create `~/.plex_compress/webui/extensions/my_plugin.py`:

```python
def register(app):
    # Add a custom route
    app.add_route("GET", "/api/hello", lambda h, p, m: h._send_json({"hello": "world"}))
    # Listen to events
    app.add_event_listener("finished", lambda payload: print("Job finished!"))
```

## Tests

```bash
python3 -m pytest tests/ -v
```

## License

MIT
