# Plex Compress — Implementation Plan

## 1. Objective

Create a tool that scans a Plex TV library, identifies files that would benefit from transcoding, and converts them to a more space-efficient format with stereo-normalized audio optimized for 2.0/2.1 playback.

## 2. Library Analysis

| Metric | Value |
|---|---|
| Total files | ~8,481 video files |
| Total used storage | 6.3 TB |
| NAS free space | 577 GB (92% full) |
| Primary container | MKV (~95%), MP4 (~5%) |
| Primary video codec | H.264 1080p (~90% of sampled files) |
| Primary audio codec | AC3 / E-AC3 5.1(side) (~85% of sampled files) |
| Subtitle formats | ASS, SubRip, mov_text |
| Already optimized | Some HEVC + stereo AAC/Opus (e.g., Dorohedoro) |

## 3. Target Format & Compatibility

### 3.1 Video: HEVC (H.265) via Apple VideoToolbox

**Why HEVC:**
- **Space efficiency:** ~40–55% bitrate reduction vs. H.264 at equivalent visual quality.
- **Hardware encode:** M5 Pro has `hevc_videotoolbox` — fast, power-efficient, real-time for 1080p.
- **Plex direct play:** Supported by Apple TV 4K, iOS, Android, Roku, Shield, most Smart TVs (2018+).
- **Web browsers:** May require transcode — acceptable trade-off for a TV library.

**Why not AV1:**
- `libsvtav1` is software-only on M5 Pro → ~0.5–2 fps for 1080p.
- Limited Plex client support; many devices would force transcoding.
- Unsuitable for a 6 TB library on a single MacBook.

**Why not H.264:**
- No meaningful space savings when source is already H.264.
- Would waste transcode effort for marginal gain.

**Encoder settings:**
```
-c:v hevc_videotoolbox -q:v 75 -allow_sw 1 -tag:v hvc1
```
- `-q:v 75`: High quality (Apple scale ~0–100). Visually transparent for 1080p TV content.
- `-allow_sw 1`: Graceful fallback if hardware session limits are hit.
- `-tag:v hvc1`: Improves MP4/MOV compatibility; harmless in MKV.

### 3.2 Audio: AAC-LC Stereo @ 160 kbps with loudnorm

**Why downmix:**
- 5.1 audio on stereo systems often has quiet dialogue and unbalanced effects.
- AC3/E-AC3 @ 320–640 kbps is wasteful when only 2 channels are used.

**Downmix strategy:**
- Use ffmpeg’s built-in `-ac 2` downmixer (ATSC coefficients, layout-aware for 5.1/5.1(side)/7.1).
- Follow with `loudnorm` EBU R128 normalization.

**Why not custom pan coefficients:**
- Custom `pan=stereo|...` is layout-specific (5.1 vs. 5.1(side) vs. 7.1).
- `-ac 2` automatically adapts to any input layout and is tested across millions of files.
- `loudnorm` compensates for the perceived quietness automatically.

**Normalization target:**
```
loudnorm=I=-16:TP=-1.5:LRA=11
```
- `I=-16 LUFS`: Louder than broadcast (-23), ideal for home/TV viewing.
- `TP=-1.5 dBTP`: Prevents inter-sample clipping.
- `LRA=11`: Moderate dynamic range, good for TV dialogue.

**Why AAC-LC:**
- Universally direct-playable across every Plex client.
- Good quality at 160 kbps stereo.
- Opus is slightly better but not direct-playable on all Smart TVs / Roku.

### 3.3 Container: MKV

**Why MKV:**
- Preserves ASS/SubRip subtitles without conversion.
- Supports multiple audio tracks if we ever add an option to keep originals.
- Modern Plex clients direct-play MKV + HEVC + AAC without issue.

## 4. Workflow Design

### 4.1 Per-File Pipeline

```
1. SCAN      → ffprobe source, decide if candidate
2. COPY      → rsync/cp source to local MacBook temp
3. TRANSCODE → ffmpeg: HEVC video + AAC stereo audio + copied subtitles
4. VERIFY    → ffprobe output + duration check + decode validation
5. REPLACE   → atomic: move original to .backup, move output to final path
6. CLEANUP   → remove .backup on success, or restore on failure
```

### 4.2 Why Local Temp on MacBook?

- NAS has only 577 GB free.
- Transcoding creates a new file alongside the original; doing this on the NAS risks filling it.
- MacBook has 192 GB free — enough for several episodes in parallel.
- Network copy overhead is acceptable for TV episodes (~1–2 GB each).

### 4.3 Skip Criteria (do not transcode)

| Condition | Reason |
|---|---|
| Video already HEVC or AV1 | No space savings; re-encoding would degrade quality. |
| Audio already stereo AAC + video already HEVC | Already optimal. |
| File size < 200 MB | Likely already low-bitrate; not worth CPU time. |
| Duration < 5 min | Extras, trailers, etc. |
| Failed verification last run | Don’t retry until manually cleared. |
| User-specified exclusion path | Override list. |

### 4.4 Safety & Resume

- SQLite state database tracks every file: hash, status, original size, output size, timestamp.
- `--dry-run` mode shows what would happen without touching files.
- `--backup` option keeps originals in a `.plex_compress_backup` directory.
- SIGINT/SIGTERM graceful shutdown: finish current file, clean temp.

## 5. Project Structure

```
plex_compress/
├── __init__.py
├── config.py       # Defaults, constants, validation
├── probe.py        # ffprobe JSON wrapper
├── scanner.py      # Library scan + candidate selection
├── audio.py        # Audio filter string builder
├── video.py        # Video encoder string builder
├── transcoder.py   # Single-file transcode + verify + replace
├── state.py        # SQLite persistence
├── cli.py          # argparse entrypoint
└── utils.py        # Temp dirs, logging, file ops

tests/
├── conftest.py
├── test_probe.py
├── test_scanner.py
├── test_audio.py
├── test_transcoder.py
└── fixtures/
    └── sample_probe.json
```

## 6. Testing Strategy

### 6.1 Unit Tests (local, no NAS required)
- Probe parsing from captured JSON.
- Candidate selection logic with mocked probes.
- Audio filter string correctness.
- Video command builder correctness.
- State database CRUD.

### 6.2 Integration Tests (require real ffmpeg + sample file)
- Transcode a 30-second sample cut from a real library file.
- Verify output codecs, channel layout, duration match.
- Verify loudnorm produced non-clipped, normalized audio.
- Verify subtitle streams copied.
- Verify atomic replacement works and is rollback-safe.

### 6.3 Real-World Validation (require user approval)
1. Dry-run scan of full library → report candidate count and estimated savings.
2. Transcode 1 episode from 3 different shows (action, dialogue-heavy, animated).
3. Playback test on Plex (Apple TV, iPhone, web) → confirm direct play.
4. ABX listening test → compare original 5.1 downmixed by Plex vs. pre-downmixed stereo.
5. Batch test: 10 episodes overnight → measure speed, stability, actual savings.

## 7. Performance Estimates

| Parameter | Estimate |
|---|---|
| hevc_videotoolbox 1080p speed | ~60–120 fps (real-time to 2× real-time) |
| Time per 45 min episode | ~5–15 minutes |
| Estimated candidates | ~6,000–7,500 files |
| Estimated time full library | ~40–120 hours (can run incrementally) |
| Expected space savings | ~2.0–2.5 TB (30–40% of current H.264 volume) |

## 8. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Hardware encoder produces blocky artifacts on dark scenes | Quality set to 75; verify visually on test batch. |
| loudnorm causes dialogue pumping | LRA=11 prevents over-compression; test with dialogue-heavy content. |
| Network interruption during copy/replace | Verify file checksum after copy; atomic move on same filesystem. |
| ffmpeg crash / corrupted output | Mandatory verification pass; keep backup until verified. |
| Plex re-scans and loses watch status | Plex preserves watch status by metadata hash if filenames unchanged. |

## 9. Decision Log

| Decision | Chosen | Rejected | Rationale |
|---|---|---|---|
| Video codec | HEVC (VideoToolbox) | AV1, H.264 | Speed + compression + compatibility balance. |
| Audio codec | AAC-LC 160 kbps | Opus, AC3 stereo | Maximum client compatibility. |
| Downmix method | `-ac 2` + `loudnorm` | Custom pan RFC 7845 | Layout-agnostic, simpler, loudnorm fixes level. |
| Container | MKV | MP4 | Subtitle preservation; negligible Plex impact. |
| Temp location | MacBook local | NAS | NAS nearly full; avoid double-space usage. |
| Keep original 5.1? | No (default) | Yes | User wants space savings; can add flag later. |
