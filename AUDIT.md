# Plex Compress Safety Audit

## Executive Summary

Current safety posture: **MODERATE**. Core protections exist (atomic replace, verification, state tracking, per-stream audio mapping) but several gaps remain that could cause data loss, quality degradation, or incorrect audio processing when running autonomously.

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
| Per-stream audio mapping | OK | Default track downmixed; all others copied as-is |
| File age guard | OK | Skips files modified within last 5 minutes |
| Health check | OK | Pre-flight validation of ffmpeg, encoder, temp space |
| SQLite WAL mode | OK | Write-Ahead Logging + 5-second busy timeout |
| Watch mode | OK | Polling-based autonomous file detection |
| Post-replace verification | PARTIAL | Verifies temp output, but not final file after `os.replace()` |
| Intelligent scan | OK | Incremental re-scan, metadata persistence, savings prediction |

## Resolved Issues

### ~~1. AUDIO DESTRUCTION: All audio tracks re-encoded and normalized~~
**Status: FIXED**

The transcoder now uses per-stream audio mapping:
- Default audio track: downmixed to stereo + EBU R128 normalize
- All other audio tracks: copied as-is (`-c:a:<n> copy`)

### ~~3. No watch/monitor mode for autonomous operation~~
**Status: FIXED**

`--watch` mode with `--watch-interval` is implemented. Polling-based detection of new/modified files.

### ~~5. No file age check (incomplete writes)~~
**Status: FIXED**

`--min-file-age` (default 300s) skips recently modified files. Configurable.

### ~~6. No health check / pre-flight validation~~
**Status: FIXED**

`--health-check` validates ffmpeg, ffprobe, hardware encoder, temp space, state DB, and audio filter chain.

### ~~7. State DB has no concurrency control~~
**Status: FIXED**

SQLite WAL mode + 5-second busy timeout enabled.

## Remaining Gaps

### 2. No concurrent file locking
**Severity: HIGH**

Multiple plex_compress instances (or parallel_jobs > 1) could process the same file simultaneously, leading to:
- Race conditions on state DB updates
- Concurrent writes to `.plex_compress_tmp`
- Duplicate transcoding wasting GPU time

**Fix needed**: File-based or SQLite advisory locking per input file.

### 4. No post-replace verification
**Severity: MEDIUM**

After `os.replace(final_tmp, path)`, the code never verifies the replaced file is:
- Actually readable by ffprobe
- Has the expected codec
- Hasn't been corrupted by filesystem issues

If `os.replace()` succeeds but the filesystem had issues, the original is gone and the new file might be corrupt.

**Fix needed**: Re-probe the final file after replacement and verify it matches expectations.

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

Current behavior:
- **Default audio track**: AAC stereo, EBU R128 loudnorm (-16 LUFS, -1.5dBTP, 11 LRA), ATSC downmix
- **Other audio tracks**: Copied as-is (preserve codec, channels, layout)
- **Subtitles**: Copied as-is
- **Attachments**: Copied as-is
- **Chapters**: Preserved
- **Metadata**: Preserved

This preserves all audio options while ensuring the primary listening experience is optimized for TV/mobile.

## Autonomous Operation Requirements

For "set it and forget it" operation:

1. **Watch mode**: Monitor library paths for new files ✅
2. **Smart scheduling**: Process during off-peak hours, respect thermal limits
3. **Self-healing**: Auto-retry failures, stale job cleanup ✅
4. **Notification**: Alert on failures or completion
5. **Safe defaults**: Conservative quality, backups enabled by default ✅
6. **Incremental**: Only process new/changed files, skip already-optimal ✅

## Intelligent Features (New)

### Incremental Scan
- Compares `mtime:size` hash against state DB
- Unchanged files skip re-probing entirely
- Significant speedup on large libraries for re-scans

### Savings Prediction
- Per-file estimate based on source codec + bitrate tier
- H.264: 30% (low) / 40% (medium) / 50% (high) / 55% (ultra bitrate)
- Accounts for audio overhead reduction (5.1→stereo)
- Tracks prediction accuracy over time

### Priority Queue
- Pending files sorted by predicted savings (DESC)
- Biggest space wins processed first
- Configurable via `--limit`

### Rich Reporting
- `--report` generates comprehensive statistics
- Per-codec and per-resolution breakdowns
- Top pending candidates
- Scan history for trend analysis

## Web UI (New)

### Architecture
- **Server**: `ThreadingHTTPServer` (stdlib only) — no external dependencies
- **Frontend**: Vanilla JS SPA — no build step, no framework
- **Styling**: Three-layer CSS tokens (Primitive → Semantic → Component)
- **Real-time**: Server-Sent Events for live progress/log streaming
- **Charts**: Chart.js loaded from CDN

### Security Considerations

| Concern | Status | Mitigation |
|---------|--------|------------|
| Path traversal on `/static/` | **FIXED** | `os.path.normpath()` + `..` prefix check |
| Query string injection | **FIXED** | Strip `?...` before route matching |
| Malformed `limit` param crash | **FIXED** | `try/except (ValueError, TypeError)` + clamp |
| Empty query param handling | **FIXED** | Empty string returns default instead of `""` |
| `dry_run` silently disabled | **FIXED** | Only override when explicitly provided in body |
| No auth / open by default | **ACCEPTED** | Local network use; bind to `127.0.0.1` for single-user |
| No HTTPS | **ACCEPTED** | Local network / reverse proxy responsibility |
| Config exposed via API | **ACCEPTED** | No secrets in config; paths are operational |
| File paths exposed in API | **ACCEPTED** | Required for operation; same user owns server |
| Extension system loads arbitrary `.py` | **ACCEPTED** | User-controlled `~/.plex_compress/webui/extensions/` |

### Accessibility (Known Gaps)
- Config form inputs lack explicit `for` attributes
- Command palette lacks `role="dialog"` and focus trap
- Toasts lack `aria-live` region
- Mobile toggle lacks `aria-expanded`
- Navigation items lack `aria-current="page"`

## Recommended Changes (Priority Order)

### P1 - File Locking (Critical)
- Add file-based lock per input file
- Prevent concurrent processing

### P2 - Post-Replace Verification (High)
- Re-probe final file after `os.replace()`
- Verify codec, duration, streams

### P3 - Backup Retention (Medium)
- Auto-cleanup backups after N days
- Default: keep for 7 days

### P4 - Rate Limiting (Low)
- Optional cooldown between files
- Optional daily file limit

### P5 - Web UI Accessibility (Low)
- Add `for` attributes to config form labels
- Add ARIA roles to command palette
- Add `aria-live` to toast container

## Testing Requirements

- Test multi-audio-track files (verify non-default tracks are copied) ✅
- Test concurrent execution (verify locking works)
- Test watch mode (verify file detection) ✅
- Test post-replace verification (simulate corruption)
- Test file age guard (verify recently modified files skipped) ✅
- Test health check (verify all components detected) ✅
- Test incremental scan (verify unchanged files skipped) ✅
- Test savings prediction (verify estimate accuracy) ✅
- Test priority queue (verify highest-savings first) ✅
- Test Web UI API endpoints (verify all return 200 + correct structure) ✅
- Test Web UI logs endpoint (verify malformed params handled) ✅
- Test Web UI static files (verify CSS/JS serve correctly) ✅
- Test Web UI query params (verify `?lines=` and `?limit=` both work) ✅
