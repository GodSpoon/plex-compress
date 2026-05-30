# Plex Compress — Test Plan

## Phase 1: Automated Unit Tests (run locally, no NAS)

### `test_probe.py`
- [x] Parse ffprobe JSON with single video, single audio, no subtitles.
- [x] Parse ffprobe JSON with multiple audio tracks (AAC + Opus).
- [x] Parse ffprobe JSON with ASS + SubRip subtitles.
- [x] Handle missing/empty ffprobe output gracefully.
- [x] Extract video bitrate when reported in container vs. stream.

### `test_scanner.py`
- [x] Identify H.264 + AC3 5.1 as candidate.
- [x] Skip HEVC + stereo AAC (already optimal).
- [x] Skip file < 200 MB.
- [x] Skip file with duration < 5 min.
- [x] Respect user exclusion list.
- [x] Compute estimated savings correctly.

### `test_audio.py`
- [x] Audio filter string contains `loudnorm=I=-16:TP=-1.5:LRA=11`.
- [x] AAC encoder settings are `-c:a aac -b:a 160k`.
- [x] Custom pan mode generates valid ffmpeg pan expression.

### `test_transcoder.py`
- [x] Build ffmpeg command with correct input, output, maps, and flags.
- [x] Verify command includes `-map 0:v:0`, `-map 0:a:0?`, `-map 0:s?`.
- [x] Verify per-stream audio mapping (default track downmixed, others copied).
- [x] Verify output path is in temp dir, not final path.
- [x] Verify backup/restore logic on simulated failure.

### `test_intelligence.py`
- [x] Predict savings for H.264 at various bitrates (low/medium/high/ultra).
- [x] Predict higher savings for high-bitrate sources.
- [x] Predict lower savings for stereo sources (no audio overhead reduction).
- [x] Extract all probe metadata (codec, resolution, bitrate, channels, duration).
- [x] Compute scan hash from file mtime and size.
- [x] Incremental scan skips unchanged files via hash comparison.
- [x] Intelligent scan persists rich metadata to state DB.
- [x] Priority queue returns candidates sorted by predicted savings (DESC).
- [x] Report generation includes summary, by-codec, by-resolution, top-pending, scan-history.
- [x] State DB v2 migration adds new columns without data loss.

### Web UI Tests (run locally, no NAS)

#### WT-01: Server Startup
**Command:** `python3 -m plex_compress.webui --host 0.0.0.0 --port 8765`

**Acceptance:**
- Server starts without error.
- `GET /` returns `200` with HTML.
- `GET /static/style.css` returns `200` with `text/css`.
- `GET /static/app.js` returns `200` with `application/javascript`.
- `GET /api/status` returns `200` with valid JSON structure.

#### WT-02: API Endpoints
**Commands:**
```bash
curl -s http://localhost:8765/api/status | python3 -m json.tool
curl -s http://localhost:8765/api/queue | python3 -m json.tool
curl -s http://localhost:8765/api/recent | python3 -m json.tool
curl -s http://localhost:8765/api/failed | python3 -m json.tool
curl -s http://localhost:8765/api/report | python3 -m json.tool
curl -s http://localhost:8765/api/config | python3 -m json.tool
curl -s http://localhost:8765/api/extensions | python3 -m json.tool
curl -s http://localhost:8765/api/logs | python3 -m json.tool
```

**Acceptance:**
- All endpoints return `200`.
- Response JSON matches expected structure.
- `/api/status` contains: `stats`, `session`, `recent`, `failed`, `shows`, `currently_running`, `runner`.
- `/api/report` contains: `summary`, `by_codec`, `by_resolution`, `top_pending`, `scan_history`.

#### WT-03: Query Parameter Handling
**Commands:**
```bash
curl -s "http://localhost:8765/api/logs?limit=10"
curl -s "http://localhost:8765/api/logs?lines=10"
curl -s "http://localhost:8765/api/logs?limit=abc"
curl -s "http://localhost:8765/api/logs?limit="
```

**Acceptance:**
- `?limit=10` returns 10 log lines.
- `?lines=10` returns 10 log lines (backward compat).
- `?limit=abc` returns default (100) without crashing.
- `?limit=` returns default (100) without crashing.

#### WT-04: Log Streaming
**Command:** `curl -s http://localhost:8765/api/logs`

**Steps:**
1. Start server with empty state.
2. Call `POST /api/health-check`.
3. Wait 3 seconds.
4. Call `GET /api/logs`.

**Acceptance:**
- Logs contain health check entries.
- Each log entry has: `time`, `level`, `message`, `raw`.
- No duplicate timestamps in `message` field.

#### WT-05: Config Persistence
**Commands:**
```bash
curl -s -X POST http://localhost:8765/api/config \
  -H "Content-Type: application/json" \
  -d '{"video_encoder": "hevc_nvenc", "video_quality": 28}'
curl -s http://localhost:8765/api/config | grep video_encoder
```

**Acceptance:**
- POST returns `{"ok": true}`.
- Subsequent GET reflects the updated value.
- Config survives server restart.

#### WT-06: Action Endpoints
**Commands:**
```bash
curl -s -X POST http://localhost:8765/api/health-check -H "Content-Type: application/json" -d '{}'
curl -s -X POST http://localhost:8765/api/scan -H "Content-Type: application/json" -d '{"intelligent": true}'
curl -s -X POST http://localhost:8765/api/stop -H "Content-Type: application/json" -d '{}'
```

**Acceptance:**
- All return `200` with `{"ok": bool, "message": str}`.
- Health check starts async job; status reflects running state.
- Scan populates queue and updates stats.
- Stop terminates running job.

#### WT-07: SSE Events
**Command:** `curl -s http://localhost:8765/api/events`

**Steps:**
1. Connect to SSE endpoint.
2. Trigger `POST /api/health-check`.
3. Observe stream for `progress` and `finished` events.

**Acceptance:**
- SSE stream stays open.
- Events are JSON with `type`, `data`, `time` fields.
- `progress` events contain `type`, `current_file` (if applicable), `message`.
- `finished` events contain `ok`, `message`.

#### WT-08: Dry Run Safety
**Command:**
```bash
curl -s -X POST http://localhost:8765/api/transcode \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'
```

**Acceptance:**
- Server respects `dry_run: true`.
- No actual file modifications occur.
- Job completes with status reflecting scan-only behavior.

---

## Phase 2: Integration Tests (require ffmpeg + sample file)

### IT-01: 30-Second Sample Transcode
**Prerequisite:** A sample file cut from library (e.g., `S.W.A.T. S01E01`).

1. Create 30-second segment:
   ```bash
   ffmpeg -ss 00:05:00 -t 30 -i input.mkv -c copy sample.mkv
   ```
2. Run transcode on `sample.mkv`.
3. Verify output:
   - Video codec = `hevc` (or `h265`).
   - Audio codec = `aac`, channels = 2, layout = `stereo`.
   - Duration matches 30 seconds (±0.1 s).
   - Subtitle stream present if input had subtitles.
   - File size < 80% of original sample.

### IT-02: Audio Normalization Validation
1. Extract output audio to WAV.
2. Run `ffmpeg -i output.mkv -af loudnorm=print_format=json -f null -`.
3. Assert output loudness is within ±1 LUFS of -16.
4. Assert true peak ≤ -1.0 dBTP.

### IT-03: Atomic Replacement Safety
1. Transcode a copy of a sample file.
2. Simulate a crash after transcode but before cleanup.
3. Verify original file is still intact and output is in temp dir only.
4. Verify restore script can recover original if backup kept.

### IT-04: Resume Capability
1. Process 3 files, stop midway through the 2nd.
2. Re-run tool.
3. Verify file 1 is skipped, file 2 is restarted or resumed, file 3 is pending.

### IT-05: Per-Stream Audio Mapping
1. Create a sample with 2+ audio tracks (e.g., default AC3 5.1 + commentary AAC 2.0).
2. Transcode the sample.
3. Verify:
   - Default track is AAC stereo with loudnorm applied.
   - Commentary track is copied as-is (original codec, channels preserved).

### IT-06: Incremental Scan Performance
1. Run intelligent scan on a library with 100+ files.
2. Re-run intelligent scan without changing any files.
3. Verify scan completes in < 10% of original time (no re-probing).

## Phase 3: Real-World Validation (requires user approval)

### RW-01: Dry-Run Full Library Scan
**Command:** `python -m plex_compress "/Volumes/media/Video/TV Shows" --dry-run --intelligent-scan`

**Acceptance:**
- Tool completes scan without error.
- Reports: total files scanned, candidates found, already-optimal count, skip reasons.
- Reports estimated space savings with breakdown by codec.
- Reports per-resolution breakdown.
- Persists metadata to state DB for incremental re-scan.
- Re-scan completes significantly faster (unchanged files skipped).

### RW-02: Single-Episode Pilot Test (3 shows)
**Shows:**
1. `S.W.A.T. S01E01` — action, loud effects, 5.1 AC3.
2. `Adventure Time - Fionna & Cake S01E01` — animation, dialogue + music, 5.1 E-AC3.
3. `A Discovery of Witches S01E01` — drama, dialogue-heavy, 5.1 AC3.

**Steps:**
1. Transcode each with `--backup`.
2. Watch each on Plex (Apple TV or preferred client).
3. Confirm direct play (no transcoding indicator).
4. Evaluate dialogue clarity vs. original.

**Acceptance:**
- All three direct-play.
- Dialogue is clear and well-balanced with music/effects.
- No audible artifacts (clipping, pumping, sync issues).
- File size reduced by ≥25%.

### RW-03: ABX Audio Comparison
**Setup:**
- Original file: let Plex transcode 5.1→stereo on the fly.
- Compressed file: pre-downmixed stereo AAC.
- Extract 60-second clip from a dialogue-heavy scene.

**Method:**
- Use `ffmpeg` to extract audio from both to WAV.
- Use ABX test tool or blind A/B in headphones.

**Acceptance:**
- Pre-downmixed audio is preferred or indistinguishable.
- Pre-downmixed audio is measurably louder and more consistent.

### RW-04: Overnight Batch Test (10 episodes)
**Command:** `python -m plex_compress "/Volumes/media/Video/TV Shows" --limit 10 --backup --intelligent-scan`

**Acceptance:**
- All 10 complete without crash.
- Total time logged; average speed ≥ real-time.
- Average space savings ≥ 30% for H.264 sources.
- No failed verifications.
- State DB shows all 10 as `completed`.
- `--report` shows accurate prediction vs actual savings.

### RW-05: Subtitle Preservation Check
**Sample:** Any file with ASS subtitles (e.g., `S.W.A.T.`).

**Steps:**
1. Transcode file.
2. Play in Plex with subtitles enabled.
3. Verify subtitle styling (colors, positions) is preserved.

**Acceptance:**
- Subtitles display correctly.
- ASS formatting (if any) retained in MKV.

### RW-06: Report Validation
**Command:** `python -m plex_compress --report --report-format json`

**Acceptance:**
- Report contains all sections: summary, by_codec, by_resolution, top_pending, scan_history.
- Summary shows accurate completed/failed/skipped counts.
- Saved bytes matches actual file size differences.
- Prediction error is within ±20% of actual savings.
- JSON output is valid and parseable.

## Test Execution Sign-Off

| Phase | Tester | Date | Result | Notes |
|---|---|---|---|---|
| Phase 1: Unit Tests | Automated | | | |
| Web UI: Server/API Tests | Automated | | | |
| Web UI: Browser Validation | Developer | | | |
| Phase 2: Integration Tests | Developer | | | |
| Phase 3: RW-01 Dry Run | User | | | |
| Phase 3: RW-02 Pilot | User | | | |
| Phase 3: RW-03 ABX | User | | | |
| Phase 3: RW-04 Batch | User | | | |
| Phase 3: RW-05 Subtitles | User | | | |
| Phase 3: RW-06 Report | User | | | |

**No library-wide transcoding shall proceed until all Phase 3 tests are signed off by the user.**

## Regression Test Commands

```bash
# Run all unit tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=plex_compress --cov-report=term-missing

# Integration test on sample
pytest tests/ -k integration -v --sample=/path/to/sample.mkv

# Full dry-run (intelligent)
python -m plex_compress "/Volumes/media/Video/TV Shows" --dry-run --intelligent-scan --verbose

# Generate report
python -m plex_compress --report --report-format json

# Test incremental scan speed
python -m plex_compress "/Volumes/media/Video/TV Shows" --dry-run --intelligent-scan
time python -m plex_compress "/Volumes/media/Video/TV Shows" --dry-run --intelligent-scan

# Start web UI
python -m plex_compress.webui --host 0.0.0.0 --port 8765

# Web UI API smoke test
curl -s http://localhost:8765/api/status | python3 -m json.tool
curl -s http://localhost:8765/api/report | python3 -m json.tool
curl -s "http://localhost:8765/api/logs?limit=10" | python3 -m json.tool

# Web UI action smoke test
curl -s -X POST http://localhost:8765/api/health-check -H "Content-Type: application/json" -d '{}'
curl -s http://localhost:8765/api/status | grep -q "has_job"
```

## Known Limitations to Document

1. **DTS-HD / TrueHD:** If library contains these, `ffmpeg` may not decode them without additional libraries. Tool will log a warning and skip.
2. **HDR:** HEVC VideoToolbox preserves HDR metadata pass-through, but tone-mapping is not applied. HDR sources should be tested separately.
3. **Multiple audio tracks:** Tool processes only the default audio track for downmixing. Secondary tracks (commentary, dubs) are copied as-is.
4. **Hardware session limits:** VideoToolbox has a limit on concurrent sessions. Tool defaults to 1 parallel job.
5. **Network reliability:** Large file copies over Wi-Fi may be slow or interrupted. Tool verifies checksums after copy.
6. **Savings prediction:** Model is heuristic-based (codec + bitrate tier). Accuracy improves with more completed transcodes but may vary for unusual content.
