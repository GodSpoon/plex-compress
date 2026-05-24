"""Utility functions for file operations and logging."""

import hashlib
import logging
import os
import shutil
import tempfile
from typing import Optional

from . import CopyError, ReplaceError, ChecksumError


def setup_logging(verbose: bool = False, log_path: Optional[str] = None) -> logging.Logger:
    """Configure logging."""
    logger = logging.getLogger("plex_compress")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers = []

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler = logging.StreamHandler()
    handler.setFormatter(fmt)
    logger.addHandler(handler)

    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        fh = logging.FileHandler(log_path)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger


def file_checksum(path: str, block_size: int = 65536) -> str:
    """Compute SHA-256 checksum of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(block_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def copy_with_verify(src: str, dst: str) -> None:
    """Copy file and verify checksum."""
    shutil.copy2(src, dst)
    if file_checksum(src) != file_checksum(dst):
        os.remove(dst)
        raise ChecksumError(f"Checksum mismatch after copying {src} to {dst}")


def safe_move(src: str, dst: str) -> None:
    """Atomic-ish move using shutil."""
    shutil.move(src, dst)


def make_temp_path(base_dir: str, suffix: str = ".mkv") -> str:
    """Create a temporary file path."""
    fd, path = tempfile.mkstemp(suffix=suffix, dir=base_dir)
    os.close(fd)
    return path


def get_free_space_gb(path: str) -> float:
    """Return free space in GB for the filesystem containing path."""
    st = os.statvfs(path)
    return (st.f_bavail * st.f_frsize) / (1024 ** 3)


def acquire_file_lock(path: str) -> Optional[int]:
    """Acquire an exclusive non-blocking lock on a file.

    Returns the file descriptor on success, None if already locked.
    """
    import fcntl
    lock_path = path + ".plex_compress.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except (IOError, OSError):
        return None


def release_file_lock(fd: Optional[int], path: str) -> None:
    """Release a file lock and clean up the lock file."""
    import fcntl
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        lock_path = path + ".plex_compress.lock"
        if os.path.exists(lock_path):
            os.remove(lock_path)
    except OSError:
        pass


def is_file_recently_modified(path: str, min_age_seconds: float = 300.0) -> bool:
    """Return True if the file was modified within the last min_age_seconds."""
    import time
    try:
        mtime = os.path.getmtime(path)
        return (time.time() - mtime) < min_age_seconds
    except OSError:
        return False
