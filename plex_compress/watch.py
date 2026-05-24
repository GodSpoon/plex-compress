"""Watch mode for autonomous plex_compress operation."""

import os
import time
from typing import Callable, Optional

from .config import Config
from .scanner import find_video_files
from .state import StateDB


class LibraryWatcher:
    """Polls a library path for new or modified video files."""

    def __init__(self, cfg: Config, state: StateDB, logger):
        self.cfg = cfg
        self.state = state
        self.logger = logger
        self._running = True

    def stop(self):
        """Signal the watcher to stop."""
        self._running = False

    def _find_candidates(self) -> list:
        """Find all candidate files that are not yet completed."""
        from .scanner import is_candidate

        files = find_video_files(self.cfg.library_path, self.cfg.exclusions)
        candidates = []
        for path in files:
            # Skip completed files
            status = self.state.get_status(path)
            if status == "completed":
                continue
            # Skip in-progress files
            if status == "in_progress":
                continue
            # Skip recently modified files
            from .utils import is_file_recently_modified
            if is_file_recently_modified(path, self.cfg.min_file_age_seconds):
                continue

            ok, reason, _probe = is_candidate(path, self.cfg)
            if ok:
                candidates.append(path)
            elif status is None:
                # Only log skip reason for new files (not already tracked)
                self.logger.debug(f"Skipping {path}: {reason}")

        return candidates

    def run(self, process_callback: Callable[[str], bool], interval: float = 60.0) -> None:
        """Run the watcher loop.

        process_callback: function(path) -> bool, called for each candidate file.
        interval: polling interval in seconds.
        """
        self.logger.info(
            f"Watch mode started: monitoring {self.cfg.library_path} "
            f"every {interval}s"
        )

        while self._running:
            try:
                candidates = self._find_candidates()
                if candidates:
                    self.logger.info(f"Watch mode: found {len(candidates)} new candidate(s)")
                    for path in candidates:
                        if not self._running:
                            break
                        try:
                            process_callback(path)
                        except Exception as e:
                            self.logger.error(f"Error processing {path}: {e}")
                else:
                    self.logger.debug("Watch mode: no new candidates")
            except Exception as e:
                self.logger.error(f"Watch mode error: {e}")

            # Sleep with interruptibility
            sleep_start = time.time()
            while self._running and (time.time() - sleep_start) < interval:
                time.sleep(1)

        self.logger.info("Watch mode stopped.")
