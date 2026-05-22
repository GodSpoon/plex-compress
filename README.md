# plex_compress

Transcode Plex libraries to space-efficient HEVC (H.265) with stereo-normalized audio.

- **Video:** HEVC via `libx265` (CPU), `hevc_videotoolbox` (Apple Silicon), or **`hevc_nvenc`** (NVIDIA RTX 20-series+)
- **Audio:** AAC-LC stereo @ 160 kbps with EBU R128 loudnorm (`I=-16 LUFS`)
- **Container:** MKV (subtitle preservation)

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

### Persistence

| Flag | Description | Default |
|------|-------------|---------|
| `--state-db` | SQLite resume database path | `~/.plex_compress/state.db` |
| `--log` | Log file path | `None` (stdout only) |
| `--verbose` / `-v` | Enable debug logging | `False` |

## Usage Examples

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

2. **Dry-run to estimate savings:**
   ```bash
   ./scripts/dry-run.sh
   ```

3. **Batch transcode overnight:**
   ```bash
   ./scripts/run-nvenc.sh
   ```

Logs and state DB are written to `~/.plex_compress/` so you can resume after reboot or interrupt.

## Safety

- **Atomic replacement**: original is either kept as `.backup` or removed only after successful verification.
- **Verification pass**: every output is re-probed for codec, duration (+/-2 s), channel layout, and subtitle preservation.
- **Size guard**: if output is > 110% of input, the transcode is rejected.
- **Resume**: SQLite state DB tracks every file; re-run resumes where it left off.
- **Non-destructive mode**: use `--output-dir` to write to a separate directory, leaving originals untouched.

## Tests

```bash
python3 -m pytest tests/ -v
```

## License

MIT
