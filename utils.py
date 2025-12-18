"""
Audio Processing Pipeline - Utilities Module

Shared utilities for logging, Redis client, archive detection, and file operations.
"""

import logging
import shutil
import time
from pathlib import Path
from typing import Optional

import magic
import redis

from config import REDIS, LOGGING


def setup_logger(name: str, log_dir: Optional[Path] = None) -> logging.Logger:
    """
    Set up a logger with consistent format across workers.

    Args:
        name: Logger name (typically worker name)
        log_dir: Directory for log files. If None, uses LOGGING["DIR"]

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        fmt=LOGGING["FORMAT"],
        datefmt=LOGGING["DATE_FORMAT"]
    )

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler
    log_dir = log_dir or LOGGING["DIR"]
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / f"{name}.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def get_redis_client(
    max_retries: int = 5,
    retry_delay: float = 1.0
) -> redis.Redis:
    """
    Get Redis client with retry logic.

    Args:
        max_retries: Maximum number of connection attempts
        retry_delay: Delay between retries in seconds

    Returns:
        Connected Redis client

    Raises:
        redis.ConnectionError: If connection fails after all retries
    """
    client = redis.Redis(
        host=REDIS["HOST"],
        port=REDIS["PORT"],
        decode_responses=True
    )

    last_error = None
    for attempt in range(max_retries):
        try:
            client.ping()
            return client
        except redis.ConnectionError as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))

    raise redis.ConnectionError(
        f"Failed to connect to Redis after {max_retries} attempts: {last_error}"
    )


def detect_archive_type(path: Path) -> str:
    """
    Content-based detection of archive type using magic bytes.

    Important: Our .tar.gz files are often actually uncompressed tar!
    This function detects the actual format regardless of file extension.

    Args:
        path: Path to the archive file

    Returns:
        'tar', 'gzip', 'tar.gz', or 'unknown'
    """
    mime = magic.Magic(mime=True)
    file_type = mime.from_file(str(path))

    if file_type == "application/gzip":
        # Check if it's a gzipped tar by looking at decompressed content
        # Read first few bytes of gzip to check for tar header
        import gzip
        try:
            with gzip.open(path, 'rb') as f:
                header = f.read(512)
                if len(header) >= 257 and header[257:262] == b'ustar':
                    return "tar.gz"
        except Exception:
            pass
        return "gzip"
    elif file_type == "application/x-tar":
        return "tar"
    elif "tar" in file_type.lower():
        return "tar"
    else:
        # Fallback: check for tar magic bytes directly
        try:
            with open(path, 'rb') as f:
                header = f.read(512)
                if len(header) >= 257 and header[257:262] == b'ustar':
                    return "tar"
        except Exception:
            pass
        return "unknown"


def safe_move(src: Path, dst: Path) -> bool:
    """
    Atomically move a file with verification.

    Args:
        src: Source file path
        dst: Destination file path

    Returns:
        True if move successful, False otherwise
    """
    if not src.exists():
        return False

    # Ensure destination directory exists
    dst.parent.mkdir(parents=True, exist_ok=True)

    # Get source size for verification
    src_size = src.stat().st_size

    try:
        # Try atomic move first (same filesystem)
        shutil.move(str(src), str(dst))

        # Verify destination exists and size matches
        if dst.exists() and dst.stat().st_size == src_size:
            return True
        else:
            return False
    except Exception:
        # If move fails, try copy + delete
        try:
            shutil.copy2(str(src), str(dst))
            if dst.exists() and dst.stat().st_size == src_size:
                src.unlink()
                return True
            else:
                # Cleanup failed copy
                if dst.exists():
                    dst.unlink()
                return False
        except Exception:
            return False


def get_audio_duration(path: Path) -> Optional[float]:
    """
    Get audio file duration in seconds using ffprobe.

    Args:
        path: Path to audio file

    Returns:
        Duration in seconds, or None if detection fails
    """
    import subprocess

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path)
            ],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception:
        pass

    return None
