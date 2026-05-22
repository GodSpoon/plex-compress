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
