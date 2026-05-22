# Plex Compress — Test Plan

## Phase 1: Automated Unit Tests (run locally, no NAS)

### `test_probe.py`
- [ ] Parse ffprobe JSON with single video, single audio, no subtitles.
- [ ] Parse ffprobe JSON with multiple audio tracks (AAC + Opus).
- [ ] Parse ffprobe JSON with ASS + SubRip subtitles.
- [ ] Handle missing/empty ffprobe output gracefully.
- [ ] Extract video bitrate when reported in container vs. stream.

### `test_scanner.py`
- [ ] Identify H.264 + AC3 5.1 as candidate.
- [ ] Skip HEVC + stereo AAC (already optimal).
- [ ] Skip file < 200 MB.
- [ ] Skip file with duration < 5 min.
- [ ] Respect user exclusion list.
- [ ] Compute estimated savings correctly.

### `test_audio.py`
- [ ] Audio filter string contains `aresample=ocl=stereo` or `-ac 2` path.
- [ ] Audio filter string contains `loudnorm=I=-16:TP=-1.5:LRA=11`.
- [ ] AAC encoder settings are `-c:a aac_at -b:a 160k` or `-c:a aac -b:a 160k`.
- [ ] Custom pan mode generates valid ffmpeg pan expression.

### `test_transcoder.py`
- [ ] Build ffmpeg command with correct input, output, maps, and flags.
- [ ] Verify command includes `-map 0:v:0`, `-map 0:a:0?`, `-map 0:s?`.
- [ ] Verify output path is in temp dir, not final path.
- [ ] Verify backup/restore logic on simulated failure.

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

## Phase 3: Real-World Validation (requires user approval)

### RW-01: Dry-Run Full Library Scan
**Command:** `python -m plex_compress /Volumes/media/Video/TV Shows --dry-run`

**Acceptance:**
- Tool completes scan without error.
- Reports: total files scanned, candidates found, already-optimal count, skip reasons.
- Reports estimated space savings with breakdown by codec.
- Output saved to `scan_report.json` for review.

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
**Command:** `python -m plex_compress /Volumes/media/Video/TV Shows --limit 10 --backup`

**Acceptance:**
- All 10 complete without crash.
- Total time logged; average speed ≥ real-time.
- Average space savings ≥ 30% for H.264 sources.
- No failed verifications.
- State DB shows all 10 as `completed`.

### RW-05: Subtitle Preservation Check
**Sample:** Any file with ASS subtitles (e.g., `S.W.A.T.`).

**Steps:**
1. Transcode file.
2. Play in Plex with subtitles enabled.
3. Verify subtitle styling (colors, positions) is preserved.

**Acceptance:**
- Subtitles display correctly.
- ASS formatting (if any) retained in MKV.

## Test Execution Sign-Off

| Phase | Tester | Date | Result | Notes |
|---|---|---|---|---|
| Phase 1: Unit Tests | Automated | | | |
| Phase 2: Integration Tests | Developer | | | |
| Phase 3: RW-01 Dry Run | User | | | |
| Phase 3: RW-02 Pilot | User | | | |
| Phase 3: RW-03 ABX | User | | | |
| Phase 3: RW-04 Batch | User | | | |
| Phase 3: RW-05 Subtitles | User | | | |

**No library-wide transcoding shall proceed until all Phase 3 tests are signed off by the user.**

## Regression Test Commands

```bash
# Run all unit tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=plex_compress --cov-report=term-missing

# Integration test on sample
pytest tests/ -k integration -v --sample=/path/to/sample.mkv

# Full dry-run
python -m plex_compress "/Volumes/media/Video/TV Shows" --dry-run --verbose
```

## Known Limitations to Document

1. **DTS-HD / TrueHD:** If library contains these, `ffmpeg` may not decode them without additional libraries. Tool will log a warning and skip.
2. **HDR:** HEVC VideoToolbox preserves HDR metadata pass-through, but tone-mapping is not applied. HDR sources should be tested separately.
3. **Multiple audio tracks:** Tool processes only the default audio track. Secondary tracks (commentary, dubs) are skipped by default to save space. Can be enabled via flag.
4. **Hardware session limits:** VideoToolbox has a limit on concurrent sessions. Tool defaults to 1 parallel job.
5. **Network reliability:** Large file copies over Wi-Fi may be slow or interrupted. Tool verifies checksums after copy.
