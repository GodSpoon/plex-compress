"""Command-line interface for plex_compress."""

import argparse
import fnmatch
import json
import os
import signal
import sys
from typing import Optional

from .config import Config
from .scanner import scan_library, is_candidate
from .transcoder import transcode_file
from .state import StateDB
from .utils import setup_logging
from . import __version__


_running = True


def _signal_handler(signum, frame):
    global _running
    _running = False
    print("\nInterrupted. Finishing current file and exiting...")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transcode Plex library to space-efficient HEVC with stereo audio."
    )
    parser.add_argument("library_path", nargs="?", default="", help="Root path of the library to scan/transcode (optional when --file is used)")
    parser.add_argument("--dry-run", action="store_true", help="Scan only, do not transcode")
    parser.add_argument("--backup", action="store_true", help="Keep original files as .backup")
    parser.add_argument("--limit", type=int, default=None, help="Max files to process")
    parser.add_argument("--temp-dir", default=None, help="Temp directory for transcoding")
    parser.add_argument("--state-db", default=None, help="Path to state SQLite DB")
    parser.add_argument("--log", default=None, help="Path to log file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument("--no-verify-checksum", action="store_true", help="Skip checksum verification when copying files to temp")
    parser.add_argument("--reset-failed", action="store_true", help="Reset failed entries and retry")
    parser.add_argument("--video-quality", type=int, default=28, help="Video quality (CRF/CQ 0-51 for x265/NVENC, 0-100 for VideoToolbox; default 28)")
    parser.add_argument("--video-encoder", default="libx265", choices=["libx265", "hevc_videotoolbox", "hevc_nvenc"], help="Video encoder (default libx265)")
    parser.add_argument("--video-preset", default="medium", help="Encoder preset (default medium)")
    parser.add_argument("--parallel-jobs", type=int, default=1, help="Number of parallel transcodes (default 1)")
    parser.add_argument("--audio-bitrate", default="160k", help="Audio bitrate (default 160k)")
    parser.add_argument("--exclude", action="append", default=[], help="Directory names to exclude")
    parser.add_argument("--file", "-f", default=None, help="Process a single file instead of scanning a library")
    parser.add_argument("--include-pattern", default=None, help="Glob pattern to filter scanned files (e.g. '*.mkv', 'S01*')")
    parser.add_argument("--output-dir", "-o", default=None, help="Write outputs to this directory instead of replacing originals in-place")
    parser.add_argument("--force", action="store_true", help="Re-process files already marked as completed in state DB")
    parser.add_argument("--health-check", action="store_true", help="Run pre-flight health check and exit")
    parser.add_argument("--min-file-age", type=float, default=None, help="Skip files modified within last N seconds (default: 300)")
    parser.add_argument("--no-file-locking", action="store_true", help="Disable file locking (allows concurrent processing of same file)")
    parser.add_argument("--no-post-replace-verify", action="store_true", help="Skip post-replace verification of final file")
    parser.add_argument("--watch", action="store_true", help="Watch mode: monitor library for new files and auto-process")
    parser.add_argument("--watch-interval", type=float, default=60.0, help="Polling interval in seconds for watch mode (default: 60)")
    parser.add_argument("--intelligent-scan", action="store_true", help="Use intelligent scan with metadata persistence and incremental detection")
    parser.add_argument("--report", action="store_true", help="Generate comprehensive transcoding report and exit")
    parser.add_argument("--report-format", default="text", choices=["text", "json"], help="Report output format")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _format_report(report: dict, fmt: str = "text") -> str:
    if fmt == "json":
        return json.dumps(report, indent=2, default=str)

    lines = []
    lines.append("=" * 60)
    lines.append("PLEX COMPRESS TRANSCODING REPORT")
    lines.append("=" * 60)

    summary = report.get("summary", {})
    lines.append(f"\n--- Summary ---")
    lines.append(f"Total files tracked:    {summary.get('total', 0)}")
    lines.append(f"Completed:              {summary.get('completed', 0)}")
    lines.append(f"Failed:                 {summary.get('failed', 0)}")
    lines.append(f"Skipped:                {summary.get('skipped', 0)}")
    lines.append(f"In progress:            {summary.get('in_progress', 0)}")
    orig_gb = summary.get('original_size_bytes', 0) / (1024 ** 3)
    out_gb = summary.get('output_size_bytes', 0) / (1024 ** 3)
    saved_gb = summary.get('saved_bytes', 0) / (1024 ** 3)
    lines.append(f"Original size:          {orig_gb:.1f} GB")
    lines.append(f"Output size:            {out_gb:.1f} GB")
    lines.append(f"Space saved:            {saved_gb:.1f} GB")
    pred_gb = summary.get('predicted_savings_bytes', 0) / (1024 ** 3)
    actual_gb = summary.get('actual_savings_bytes', 0) / (1024 ** 3)
    err_gb = summary.get('prediction_error_bytes', 0) / (1024 ** 3)
    lines.append(f"Predicted savings:      {pred_gb:.1f} GB")
    lines.append(f"Actual savings:         {actual_gb:.1f} GB")
    lines.append(f"Prediction error:       {err_gb:+.1f} GB")

    by_codec = report.get("by_codec", [])
    if by_codec:
        lines.append(f"\n--- By Source Video Codec ---")
        for row in by_codec:
            codec = row.get("video_codec", "unknown")
            count = row.get("count", 0)
            saved = row.get("saved", 0) / (1024 ** 3)
            avg = row.get("avg_saved", 0) / (1024 ** 2)
            lines.append(f"  {codec:12s}  {count:4d} files  {saved:8.1f} GB total  {avg:6.1f} MB avg")

    by_res = report.get("by_resolution", [])
    if by_res:
        lines.append(f"\n--- By Resolution ---")
        for row in by_res:
            w = row.get("video_width", 0)
            h = row.get("video_height", 0)
            count = row.get("count", 0)
            saved = row.get("saved", 0) / (1024 ** 3)
            lines.append(f"  {w}x{h:4d}  {count:4d} files  {saved:8.1f} GB")

    top = report.get("top_pending", [])
    if top:
        lines.append(f"\n--- Top 10 Pending Candidates (by predicted savings) ---")
        for i, row in enumerate(top[:10], 1):
            path = os.path.basename(row.get("path", "unknown"))
            vc = row.get("video_codec", "?")
            res = f"{row.get('video_width', '?')}x{row.get('video_height', '?')}"
            pred_mb = (row.get("predicted_savings_bytes") or 0) / (1024 ** 2)
            lines.append(f"  {i:2d}. {path:40s} {vc:8s} {res:10s} ~{pred_mb:6.0f} MB")

    scans = report.get("scan_history", [])
    if scans:
        lines.append(f"\n--- Recent Scan History ---")
        for row in scans[:5]:
            ts = row.get("scanned_at", "?")
            total = row.get("total_files", 0)
            cand = row.get("candidates", 0)
            est = row.get("estimated_savings_gb", 0)
            lines.append(f"  {ts}  {total:5d} files  {cand:4d} candidates  ~{est:6.1f} GB est.")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    cfg = Config(
        library_path=args.library_path,
        dry_run=args.dry_run,
        keep_backup=args.backup,
        limit=args.limit,
        temp_dir=args.temp_dir or Config().temp_dir,
        state_db_path=args.state_db or Config().state_db_path,
        log_path=args.log,
        verbose=args.verbose,
        video_quality=args.video_quality,
        video_encoder=args.video_encoder,
        video_preset=args.video_preset,
        parallel_jobs=args.parallel_jobs,
        verify_checksum=not args.no_verify_checksum,
        audio_bitrate=args.audio_bitrate,
        output_dir=args.output_dir,
        single_file=args.file,
        include_pattern=args.include_pattern,
        force=args.force,
        exclusions=args.exclude,
        min_file_age_seconds=args.min_file_age if args.min_file_age is not None else Config().min_file_age_seconds,
        enable_file_locking=not args.no_file_locking,
        post_replace_verify=not args.no_post_replace_verify,
    )

    logger = setup_logging(cfg.verbose, cfg.log_path)

    # Health check mode
    if args.health_check:
        from .health import run_health_check
        ok, _messages = run_health_check(cfg, logger)
        return 0 if ok else 1

    state = StateDB(cfg.state_db_path)

    if args.reset_failed:
        state.reset_failed()
        logger.info("Reset failed entries.")

    # Report mode
    if args.report:
        from .intelligence import generate_report
        report = generate_report(state)
        output = _format_report(report, fmt=args.report_format)
        print(output)
        return 0

    # Watch mode
    if args.watch:
        if not cfg.library_path or not os.path.isdir(cfg.library_path):
            logger.error(f"Library path required for watch mode: {cfg.library_path}")
            return 1
        from .watch import LibraryWatcher
        from .transcoder import transcode_file

        watcher = LibraryWatcher(cfg, state, logger, intelligent=args.intelligent_scan)

        def _process(path: str) -> bool:
            return transcode_file(path, cfg, state, logger)

        # Set up signal handler to stop watcher
        def _watch_signal_handler(signum, frame):
            watcher.stop()
            logger.info("Watch mode shutting down...")

        signal.signal(signal.SIGINT, _watch_signal_handler)
        signal.signal(signal.SIGTERM, _watch_signal_handler)

        watcher.run(_process, interval=args.watch_interval)
        return 0

    # Single-file mode bypasses scanning
    if cfg.single_file:
        if not os.path.isfile(cfg.single_file):
            logger.error(f"File not found: {cfg.single_file}")
            return 1
        ok, reason, _probe = is_candidate(cfg.single_file, cfg)
        if not ok:
            logger.info(f"Skipping single file: {cfg.single_file} ({reason})")
            return 0
        report = {
            "total_files": 1,
            "candidates": [cfg.single_file],
            "skipped": [],
            "already_optimal": 0,
            "estimated_savings_gb": 0.0,
        }
        logger.info(f"Single file mode: {cfg.single_file}")
    else:
        if not cfg.library_path or not os.path.isdir(cfg.library_path):
            logger.error(f"Library path required and must be a directory: {cfg.library_path}")
            return 1
        logger.info(f"Scanning library: {cfg.library_path}")

        if args.intelligent_scan:
            from .intelligence import scan_library_intelligent
            report = scan_library_intelligent(cfg, state=state, force=cfg.force, logger=logger)
        else:
            report = scan_library(cfg, state=state, force=cfg.force)

    # Apply include-pattern filter
    candidates = report["candidates"]
    if cfg.include_pattern:
        candidates = [p for p in candidates if fnmatch.fnmatch(os.path.basename(p), cfg.include_pattern)]
        logger.info(f"After pattern filter '{cfg.include_pattern}': {len(candidates)} files")

    logger.info(f"Total files: {report['total_files']}")
    logger.info(f"Candidates: {len(candidates)}")
    logger.info(f"Already optimal: {report['already_optimal']}")
    logger.info(f"Skipped: {len(report['skipped'])}")
    logger.info(f"Estimated savings: {report['estimated_savings_gb']:.1f} GB")

    # Print per-codec breakdown if available (intelligent scan)
    if "by_codec" in report:
        logger.info("Breakdown by source codec:")
        for codec, data in report["by_codec"].items():
            logger.info(f"  {codec}: {data['count']} files, ~{data['predicted_savings'] / (1024**3):.1f} GB predicted savings")

    if cfg.dry_run:
        # Print first 20 candidates
        for p in candidates[:20]:
            logger.info(f"  [CANDIDATE] {p}")
        for p, reason in report["skipped"][:20]:
            logger.info(f"  [SKIPPED] {p}: {reason}")
        return 0

    # Process candidates
    if cfg.limit:
        candidates = candidates[:cfg.limit]

    success = 0
    failed = 0
    skipped_count = 0

    for path in candidates:
        if not _running:
            logger.info("Stopping gracefully.")
            break

        existing_status = state.get_status(path)
        if existing_status == "completed" and not cfg.force:
            logger.info(f"Skipping already completed: {path}")
            skipped_count += 1
            continue

        state.mark_started(path)
        ok = transcode_file(path, cfg, state, logger)
        if ok:
            success += 1
        else:
            failed += 1

    stats = state.get_stats()
    logger.info(
        f"Batch complete: {success} succeeded, {failed} failed, {skipped_count} skipped. "
        f"Total saved: {stats['saved_bytes'] / 1024 / 1024:.1f} MB"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
