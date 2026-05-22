# plex_compress

Transcode Plex libraries to space-efficient HEVC (H.265) with stereo-normalized audio.

- **Video:** HEVC via `libx265` (CPU), `hevc_videotoolbox` (Apple Silicon), or **`hevc_nvenc`** (NVIDIA RTX 20-series+)
- **Audio:** AAC-LC stereo @ 160 kbps with EBU R128 loudnorm (`I=-16 LUFS`)
- **Container:** MKV (subtitle preservation)

## Why

Plex libraries accumulate large H.264 + 5.1 AC3/E-AC3 files. Pre-transcoding to HEVC + stereo AAC saves **30–45%** disk space, eliminates on-the-fly audio downmixing, and direct-plays on virtually every client.

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

## Usage

```bash
# Dry-run scan only
python -m plex_compress /mnt/plex/TV --dry-run --video-encoder hevc_nvenc

# Transcode with NVENC, keep backups, limit 10
python -m plex_compress /mnt/plex/TV \
  --video-encoder hevc_nvenc \
  --video-quality 28 \
  --video-preset p4 \
  --parallel-jobs 2 \
  --backup \
  --limit 10
```

### CLI Options

| Flag | Description | Default |
|------|-------------|---------|
| `--video-encoder` | `libx265`, `hevc_videotoolbox`, `hevc_nvenc` | `libx265` |
| `--video-quality` | CRF/CQ 0-51 (x265/NVENC), 0-100 (VideoToolbox) | `28` |
| `--video-preset` | Encoder preset (`medium`, `p4`, `fast`, etc.) | `medium` |
| `--parallel-jobs` | Concurrent transcodes | `1` |
| `--audio-bitrate` | Audio bitrate | `160k` |
| `--backup` | Keep `.plex_compress_backup` originals | `False` |
| `--limit` | Max files to process | unlimited |
| `--temp-dir` | Local temp directory | `~/tmp/plex_compress` |
| `--state-db` | SQLite state DB path | `~/.plex_compress/state.db` |
| `--dry-run` | Scan and report only | `False` |
| `--exclude` | Directory names to skip | repeatable |
| `--no-verify-checksum` | Skip checksum when copying over network mounts | `False` |
## NVIDIA RTX 2070 Super (Turing) Tuning

The RTX 2070 Super has a dedicated **NVDEC/ NVENC** chip. Recommended settings:

```bash
python -m plex_compress /mnt/plex \
  --video-encoder hevc_nvenc \
  --video-quality 28 \
  --video-preset p4 \
  --parallel-jobs 2
```

- **`--video-quality 28`**: CQ 28 is visually transparent for 1080p TV content.
- **`--video-preset p4`**: Balanced quality/speed. Turing scales well from `p1` (fastest) to `p7` (best).
- **`--parallel-jobs 2`**: The 2070 Super NVENC session limit is high; 2 jobs keeps GPU utilization ~80–95% without stalling PCIe copy.
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
- **Verification pass**: every output is re-probed for codec, duration (±2 s), channel layout, and subtitle preservation.
- **Size guard**: if output is > 110% of input, the transcode is rejected.
- **Resume**: SQLite state DB tracks every file; re-run resumes where it left off.

## Tests

```bash
python -m pytest tests/ -v
```

## License

MIT
