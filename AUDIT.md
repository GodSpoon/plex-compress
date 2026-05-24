# Plex Compress Safety Audit

## Executive Summary

Current safety posture: **MODERATE**. Core protections exist (atomic replace, verification, state tracking) but several critical gaps remain that could cause data loss, quality degradation, or incorrect audio processing when running autonomously.

## Current Safety Mechanisms (Working Well)

| Mechanism | Status | Notes |
|-----------|--------|-------|
| Atomic in-place replacement | OK | `os.replace()` after temp copy prevents partial writes |
| Output verification | OK | Checks codec, duration, stream counts, attachments, chapters |
| Size guards | OK | 110% bloat guard + 95% "not worth it" guard |
| Checksum on copy | OK | Optional SHA-256 verification temp->local |
| State DB resume | OK | SQLite tracks per-file status across restarts |
| Retry on transient failure | OK | One retry for DurationError (GPU hiccups) |
| Graceful shutdown | OK | SIGINT/SIGTERM handler finishes current file |
| Temp space pre-check | OK | Requires 2x file size free before starting |
| Stale in_progress reset | OK | Auto-resets crashed jobs on DB init |

## Critical Gaps

### 1. AUDIO DESTRUCTION: All audio tracks re-encoded and normalized
**Severity: CRITICAL**

The current ffmpeg command applies `-af loudnorm` and `-ac 2` to **ALL** audio tracks via `-map 0:a?`:

- Commentary tracks get destroyed (re-encoded to AAC, normalized, downmixed)
- Alternate language tracks get destroyed
- Descriptive audio tracks get destroyed
- The `loudnorm` filter is applied globally, meaning ALL tracks get the same normalization

**Fix needed**: Per-track audio mapping:
- Default audio track: downmix to stereo + EBU R128 normalize
- All other audio tracks: copy as-is (preserve original codec, channels, layout)

### 2. No concurrent file locking
**Severity: HIGH**

Multiple plex_compress instances (or parallel_jobs > 1) could process the same file simultaneously, leading to:
- Race conditions on state DB updates
- Concurrent writes to `.plex_compress_tmp`
- Duplicate transcoding wasting GPU time

**Fix needed**: File-based or SQLite advisory locking per input file.

### 3. No watch/monitor mode for autonomous operation
**Severity: HIGH**

User wants "runs autonomously and just converts added media when it appears." Currently requires manual `run-by-size.sh` execution.

**Fix needed**: A `--watch` mode using `watchdog` (inotify) or periodic polling that detects new/modified files and auto-processes them.

### 4. No post-replace verification
**Severity: MEDIUM**

After `os.replace(final_tmp, path)`, the code never verifies the replaced file is:
- Actually readable by ffprobe
- Has the expected codec
- Hasn't been corrupted by filesystem issues

If `os.replace()` succeeds but the filesystem had issues, the original is gone and the new file might be corrupt.

**Fix needed**: Re-probe the final file after replacement and verify it matches expectations.

### 5. No file age check (incomplete writes)
**Severity: MEDIUM**

If a file is still being written (e.g., Sonarr/Radarr still copying), plex_compress could start processing a partial file.

**Fix needed**: Skip files modified within last N minutes (configurable, default 5).

### 6. No health check / pre-flight validation
**Severity: MEDIUM**

Before processing a library, doesn't verify:
- ffmpeg can run and produce valid output
- Hardware encoder is available
- Temp directory is writable
- State DB is accessible

**Fix needed**: `plex_compress --health-check` command.

### 7. State DB has no concurrency control
**Severity: MEDIUM**

SQLite in default mode with multiple processes can get "database is locked" errors.

**Fix needed**: Enable WAL mode, add busy timeout, or use file locking.

### 8. Backup retention not configurable
**Severity: LOW**

`keep_backup=True` keeps backups forever. No auto-cleanup.

**Fix needed**: Configurable backup retention (days), auto-cleanup after verification.

### 9. No rate limiting / thermal protection
**Severity: LOW**

Continuous batch processing can overheat GPUs or saturate storage.

**Fix needed**: Optional cooldown between files, max daily file limit.

## Audio Requirements Analysis

User goals:
1. "normalized audio that forgoes 5.1 surround sound"
2. "consistent and pleasant audio that isn't too quiet or gets too loud/quiet on normal 2.1 or TV/mobile speakers"
3. "doesn't break audio streams/subtitles or other important metadata items"

Current behavior: ALL audio tracks become AAC stereo normalized.

Desired behavior (safe default):
- **Default audio track**: AAC stereo, EBU R128 loudnorm (-16 LUFS, -1.5dBTP, 11 LRA), ATSC downmix
- **Other audio tracks**: Copied as-is (preserve codec, channels, layout)
- **Subtitles**: Copied as-is
- **Attachments**: Copied as-is
- **Chapters**: Preserved
- **Metadata**: Preserved

This preserves all audio options while ensuring the primary listening experience is optimized for TV/mobile.

## Autonomous Operation Requirements

For "set it and forget it" operation:

1. **Watch mode**: Monitor library paths for new files
2. **Smart scheduling**: Process during off-peak hours, respect thermal limits
3. **Self-healing**: Auto-retry failures, stale job cleanup
4. **Notification**: Alert on failures or completion
5. **Safe defaults**: Conservative quality, backups enabled by default
6. **Incremental**: Only process new/changed files, skip already-optimal

## Recommended Changes (Priority Order)

### P1 - Audio Preservation (Critical)
- Refactor `build_ffmpeg_command` to use per-stream audio mapping
- Default track: encode with normalization
- Other tracks: `-c:a:<n> copy`

### P1 - File Locking (Critical)
- Add file-based lock per input file
- Prevent concurrent processing

### P2 - Watch Mode (High)
- Add `--watch` CLI flag
- Use `watchdog` library for inotify-based monitoring
- Or polling mode for network filesystems

### P2 - Post-Replace Verification (High)
- Re-probe final file after `os.replace()`
- Verify codec, duration, streams

### P2 - File Age Guard (High)
- Skip files modified < 5 minutes ago
- Configurable threshold

### P3 - Health Check (Medium)
- Add `--health-check` command
- Verify ffmpeg/ffprobe, encoders, temp space

### P3 - State DB Concurrency (Medium)
- Enable SQLite WAL mode
- Add busy timeout

### P3 - Backup Retention (Medium)
- Auto-cleanup backups after N days
- Default: keep for 7 days

### P4 - Rate Limiting (Low)
- Optional cooldown between files
- Optional daily file limit

## Testing Requirements

- Test multi-audio-track files (verify non-default tracks are copied)
- Test concurrent execution (verify locking works)
- Test watch mode (verify file detection)
- Test post-replace verification (simulate corruption)
- Test file age guard (verify recently modified files skipped)
- Test health check (verify all components detected)
