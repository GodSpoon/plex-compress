"""Command-line interface for plex_compress."""

import argparse
import dataclasses
import fnmatch
import os
import signal
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
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


def _process_one_worker(path: str, cfg_dict: dict) -> tuple:
    """Worker function (module-level so it's picklable for ProcessPoolExecutor).

    Reconstructs Config + StateDB + logger in the worker process. Returns
    (path, ok, error_message). Never raises — exceptions are caught and
    returned as error strings so the main process can log them.
    """
    import logging
    from .config import Config as _Config
    from .state import StateDB as _StateDB
    from .transcoder import transcode_file as _transcode_file
    from .utils import setup_logging as _setup_logging

    cfg = _Config(**cfg_dict)
    logger = _setup_logging(verbose=cfg.verbose, log_path=cfg.log_path)
    state = _StateDB(cfg.state_db_path)
    try:
        ok = _transcode_file(path, cfg, state, logger)
        return (path, ok, None)
    except Exception as e:
        return (path, False, f"{type(e).__name__}: {e}")


def _process_batch(candidates: list, cfg: Config, logger) -> tuple:
    """Process candidates serially or in parallel based on cfg.parallel_jobs.

    Returns (success, failed, skipped) counts.
    """
    global _running
    success = failed = skipped = 0

    if cfg.parallel_jobs <= 1:
        # Sequential path (preserves the original behavior for single-job users)
        for path in candidates:
            if not _running:
                logger.info("Stopping gracefully.")
                break
            ok = transcode_file(path, cfg, _state_for_main(cfg, logger), logger)
            if ok:
                success += 1
            else:
                failed += 1
        return success, failed, skipped

    # Parallel path: ProcessPoolExecutor
    cfg_dict = dataclasses.asdict(cfg)
    # logger isn't picklable; remove it (workers create their own)
    cfg_dict.pop("log_path", None)
    # also drop the logger itself if it got serialized in somehow
    cfg_dict = {k: v for k, v in cfg_dict.items() if k != "logger"}

    logger.info(f"Dispatching {len(candidates)} files to {cfg.parallel_jobs} parallel workers")
    with ProcessPoolExecutor(max_workers=cfg.parallel_jobs) as ex:
        futures = {ex.submit(_process_one_worker, p, cfg_dict): p for p in candidates}
        try:
            for fut in as_completed(futures):
                if not _running:
                    logger.info("Interrupt received; cancelling pending work...")
                    for f in futures:
                        f.cancel()
                    break
                path = futures[fut]
                try:
                    _p, ok, err = fut.result()
                    if ok:
                        success += 1
                    else:
                        failed += 1
                        if err:
                            logger.error(f"Failed: {path}: {err}")
                except Exception as e:
                    failed += 1
                    logger.error(f"Worker exception for {path}: {e}")
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt; cancelling pending work...")
            for f in futures:
                f.cancel()
            raise
    return success, failed, skipped


def _state_for_main(cfg: Config, logger) -> StateDB:
    """Helper: a single StateDB for the main process (used in sequential mode
    and for pre-existing status checks before dispatching to workers)."""
    return StateDB(cfg.state_db_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transcode Plex library to space-efficient HEVC with stereo audio."
    )
    parser.add_argument("library_path", nargs="?", default="", help="Root path of the library to scan/transcode (optional when --file is used)")
    parser.add_argument("--dry-run", action="store_true", help="Scan only, do not transcode")
    parser.add_argument("--full-scan", action="store_true", help="Re-probe every file (ignore the incremental skip cache); 'completed' files are still skipped unless --force")
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
    parser.add_argument("--calibrate", action="store_true", help="Run a short encoder calibration transcode to measure achieved fps, then exit")
    parser.add_argument("--calibrate-sample", default=None, help="Path to a sample video file to use for calibration (default: synthetic testsrc)")
    parser.add_argument("--calibrate-duration", type=float, default=5.0, help="Calibration sample duration in seconds (default: 5)")
    parser.add_argument("--min-file-age", type=float, default=None, help="Skip files modified within last N seconds (default: 300)")
    parser.add_argument("--no-file-locking", action="store_true", help="Disable file locking (allows concurrent processing of same file)")
    parser.add_argument("--no-post-replace-verify", action="store_true", help="Skip post-replace verification of final file")
    parser.add_argument("--watch", action="store_true", help="Watch mode: monitor library for new files and auto-process")
    parser.add_argument("--watch-interval", type=float, default=60.0, help="Polling interval in seconds for watch mode (default: 60)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


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

    # Calibration mode: run a short transcode to measure achieved fps
    # and cache it for future time-estimation runs.
    if args.calibrate:
        from .calibrate import calibrate_encoder
        try:
            result = calibrate_encoder(
                cfg,
                sample_path=args.calibrate_sample,
                logger=logger,
                duration_s=args.calibrate_duration,
            )
        except Exception as e:
            logger.error(f"Calibration failed: {e}")
            return 1
        logger.info(
            f"Calibration complete: {result['achieved_fps_steady']:.1f} fps steady "
            f"@ {result['width']}x{result['height']} (encoder={result['encoder']}, "
            f"quality={result['quality']}, preset={result['preset']})"
        )
        return 0

    state = StateDB(cfg.state_db_path)

    if args.reset_failed:
        state.reset_failed()
        logger.info("Reset failed entries.")

    # Watch mode
    if args.watch:
        if not cfg.library_path or not os.path.isdir(cfg.library_path):
            logger.error(f"Library path required for watch mode: {cfg.library_path}")
            return 1
        from .watch import LibraryWatcher

        watcher = LibraryWatcher(cfg, state, logger)

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
        report = scan_library(cfg, state=state, force=cfg.force, full_scan=args.full_scan, logger=logger)

    # Apply include-pattern filter
    candidates = report["candidates"]
    if cfg.include_pattern:
        candidates = [p for p in candidates if fnmatch.fnmatch(os.path.basename(p), cfg.include_pattern)]
        logger.info(f"After pattern filter '{cfg.include_pattern}': {len(candidates)} files")

    errors = report.get("errors", [])
    logger.info(f"Total files: {report['total_files']}")
    logger.info(f"Candidates: {len(candidates)}")
    logger.info(f"Already optimal: {report['already_optimal']}")
    logger.info(f"Skipped: {len(report['skipped'])}")
    if errors:
        logger.info(f"Unreadable/broken: {len(errors)}")
    if 'probed' in report:
        logger.info(f"Probed this scan: {report['probed']} (cached: {report.get('cached', 0)})")
    logger.info(f"Estimated savings: {report['estimated_savings_gb']:.1f} GB")
    if 'estimated_output_gb' in report:
        logger.info(f"Estimated output size: {report['estimated_output_gb']:.1f} GB")
    if 'estimated_transcode_hours' in report:
        logger.info(
            f"Estimated transcode time: {report['estimated_transcode_hours']:.1f} hours "
            f"@ {report.get('transcode_fps_basis', 'unknown fps')}"
        )
    if 'estimated_savings_by_codec' in report and report['estimated_savings_by_codec']:
        codec_lines = ", ".join(
            f"{codec}={gb:.1f}GB"
            for codec, gb in sorted(report['estimated_savings_by_codec'].items(), key=lambda x: -x[1])
        )
        logger.info(f"Estimated savings by source codec: {codec_lines}")
    # Surface prediction accuracy from previously completed encodes.
    acc = state.get_prediction_accuracy() if state is not None else None
    if acc and acc.get("samples", 0) >= 1:
        logger.info(
            f"Prediction accuracy (from {acc['samples']} prior encodes): "
            f"predicted={acc['predicted_savings_bytes'] / 1024**3:.2f} GB, "
            f"actual={acc['actual_savings_bytes'] / 1024**3:.2f} GB "
            f"({acc['relative_error_pct']:+.1f}% error)"
        )

    if cfg.dry_run:
        # Print first 20 candidates
        for p in candidates[:20]:
            logger.info(f"  [CANDIDATE] {p}")
        for p, reason in report["skipped"][:20]:
            logger.info(f"  [SKIPPED] {p}: {reason}")
        for p, reason in errors:
            logger.info(f"  [UNREADABLE] {p}: {reason.splitlines()[0] if reason else ''}")
        return 0

    # Process candidates
    if cfg.limit:
        candidates = candidates[:cfg.limit]

    success = 0
    failed = 0
    skipped = 0

    # Pre-filter: drop already-completed (unless --force) and report the count.
    if not cfg.force:
        to_process = []
        for p in candidates:
            if state.get_status(p) == "completed":
                logger.info(f"Skipping already completed: {p}")
                skipped += 1
            else:
                to_process.append(p)
        candidates = to_process

    if not candidates:
        logger.info("No candidates to process.")
    else:
        success, failed, _ = _process_batch(candidates, cfg, logger)

    stats = state.get_stats()
    logger.info(
        f"Batch complete: {success} succeeded, {failed} failed, {skipped} skipped. "
        f"Total saved: {stats['saved_bytes'] / 1024 / 1024:.1f} MB"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
