"""Command-line interface for plex_compress."""

import argparse
import signal
import sys
from typing import Optional

from .config import Config
from .scanner import scan_library
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
    parser.add_argument("library_path", help="Root path of the library to scan/transcode")
    parser.add_argument("--dry-run", action="store_true", help="Scan only, do not transcode")
    parser.add_argument("--backup", action="store_true", help="Keep original files as .backup")
    parser.add_argument("--limit", type=int, default=None, help="Max files to process")
    parser.add_argument("--temp-dir", default=None, help="Temp directory for transcoding")
    parser.add_argument("--state-db", default=None, help="Path to state SQLite DB")
    parser.add_argument("--log", default=None, help="Path to log file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument("--reset-failed", action="store_true", help="Reset failed entries and retry")
    parser.add_argument("--video-quality", type=int, default=28, help="Video quality (CRF/CQ 0-51 for x265/NVENC, 0-100 for VideoToolbox; default 28)")
    parser.add_argument("--video-encoder", default="libx265", choices=["libx265", "hevc_videotoolbox", "hevc_nvenc"], help="Video encoder (default libx265)")
    parser.add_argument("--video-preset", default="medium", help="Encoder preset (default medium)")
    parser.add_argument("--parallel-jobs", type=int, default=1, help="Number of parallel transcodes (default 1)")
    parser.add_argument("--audio-bitrate", default="160k", help="Audio bitrate (default 160k)")
    parser.add_argument("--exclude", action="append", default=[], help="Directory names to exclude")
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
        audio_bitrate=args.audio_bitrate,
        exclusions=args.exclude,
    )

    logger = setup_logging(cfg.verbose, cfg.log_path)
    state = StateDB(cfg.state_db_path)

    if args.reset_failed:
        state.reset_failed()
        logger.info("Reset failed entries.")

    logger.info(f"Scanning library: {cfg.library_path}")
    report = scan_library(cfg)

    logger.info(f"Total files: {report['total_files']}")
    logger.info(f"Candidates: {len(report['candidates'])}")
    logger.info(f"Already optimal: {report['already_optimal']}")
    logger.info(f"Skipped: {len(report['skipped'])}")
    logger.info(f"Estimated savings: {report['estimated_savings_gb']:.1f} GB")

    if cfg.dry_run:
        # Print first 20 candidates
        for p in report["candidates"][:20]:
            logger.info(f"  [CANDIDATE] {p}")
        for p, reason in report["skipped"][:20]:
            logger.info(f"  [SKIPPED] {p}: {reason}")
        return 0

    # Process candidates
    candidates = report["candidates"]
    if cfg.limit:
        candidates = candidates[:cfg.limit]

    success = 0
    failed = 0
    skipped = 0

    for path in candidates:
        if not _running:
            logger.info("Stopping gracefully.")
            break

        existing_status = state.get_status(path)
        if existing_status == "completed":
            logger.info(f"Skipping already completed: {path}")
            skipped += 1
            continue

        state.mark_started(path)
        ok = transcode_file(path, cfg, state, logger)
        if ok:
            success += 1
        else:
            failed += 1

    stats = state.get_stats()
    logger.info(
        f"Batch complete: {success} succeeded, {failed} failed, {skipped} skipped. "
        f"Total saved: {stats['saved_bytes'] / 1024 / 1024:.1f} MB"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
