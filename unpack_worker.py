"""
Audio Processing Pipeline - Unpack Worker

Extracts tar archives, converts MP3 to Opus, and queues files for transcription.
Runs on GPU VMs alongside the GPU worker as a separate process.
"""

import json
import shutil
import subprocess
import tarfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import PATHS, REDIS, PROCESSING
from db import bulk_insert_audio_files
from utils import setup_logger, get_redis_client, detect_archive_type, get_audio_duration

logger = setup_logger("unpack_worker")


def convert_mp3_to_opus(args: tuple[Path, Path]) -> Optional[dict]:
    """
    Convert a single MP3 file to Opus format.

    This function is designed to run in a separate process via ProcessPoolExecutor.

    Args:
        args: Tuple of (mp3_path, opus_path)

    Returns:
        Dict with conversion result, or None on failure
    """
    mp3_path, opus_path = args

    # Ensure output directory exists
    opus_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",  # Overwrite output
        "-i", str(mp3_path),
        "-c:a", "libopus",
        "-b:a", PROCESSING["OPUS_BITRATE"],
        "-vn",  # No video
        str(opus_path)
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=120  # 2 minute timeout per file
        )

        if result.returncode == 0 and opus_path.exists():
            return {
                "original_filename": mp3_path.name,
                "opus_path": str(opus_path),
                "file_size_bytes": opus_path.stat().st_size,
                "success": True,
            }
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass

    return None


def extract_archive(archive_path: Path, extract_dir: Path) -> bool:
    """
    Extract an archive using content-based type detection.

    Handles mislabeled .tar.gz files that are actually plain tar.

    Args:
        archive_path: Path to the archive file
        extract_dir: Directory to extract into

    Returns:
        True if extraction succeeded, False otherwise
    """
    archive_type = detect_archive_type(archive_path)
    logger.debug(f"Detected archive type: {archive_type} for {archive_path.name}")

    try:
        if archive_type == "tar.gz":
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(path=extract_dir, filter="data")
        elif archive_type in ("tar", "gzip"):
            # Try plain tar first (common case for mislabeled files)
            try:
                with tarfile.open(archive_path, "r:") as tar:
                    tar.extractall(path=extract_dir, filter="data")
            except tarfile.ReadError:
                # Fall back to gzip if plain tar fails
                with tarfile.open(archive_path, "r:gz") as tar:
                    tar.extractall(path=extract_dir, filter="data")
        else:
            logger.error(f"Unknown archive type for {archive_path.name}")
            return False

        return True

    except Exception as e:
        logger.error(f"Failed to extract {archive_path.name}: {e}")
        return False


def process_archive(archive_path: str) -> dict:
    """
    Process a single archive: extract, convert, insert to DB, queue for transcription.

    Args:
        archive_path: Path to the tar archive

    Returns:
        Dict with processing statistics
    """
    archive_path = Path(archive_path)
    archive_name = archive_path.stem

    # Strip common suffixes for cleaner directory name
    for suffix in [".tar", ".gz", ".tar.gz", ".tgz"]:
        if archive_name.endswith(suffix.replace(".", "")):
            archive_name = archive_name[:-len(suffix.replace(".", ""))]
            break

    stats = {
        "archive": archive_path.name,
        "mp3_found": 0,
        "converted": 0,
        "failed": 0,
        "queued": 0,
    }

    # 1. Create temp extraction directory
    extract_dir = PATHS["UNPACKED_DIR"] / archive_name
    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 2. Extract archive
        logger.info(f"Extracting {archive_path.name} to {extract_dir}")
        if not extract_archive(archive_path, extract_dir):
            stats["failed"] = -1  # Indicate extraction failure
            return stats

        # 3. Find all MP3 files
        mp3_files = list(extract_dir.rglob("*.mp3"))
        mp3_files.extend(extract_dir.rglob("*.MP3"))  # Case insensitive
        stats["mp3_found"] = len(mp3_files)

        if not mp3_files:
            logger.warning(f"No MP3 files found in {archive_path.name}")
            return stats

        logger.info(f"Found {len(mp3_files)} MP3 files in {archive_path.name}")

        # 4. Prepare output directory (organized by date)
        today = datetime.now().strftime("%Y-%m-%d")
        output_dir = PATHS["AUDIO_DIR"] / today
        output_dir.mkdir(parents=True, exist_ok=True)

        # 5. Build conversion tasks
        conversion_tasks = []
        for mp3_path in mp3_files:
            # Create unique opus filename: archive_originalname.opus
            opus_name = f"{archive_name}_{mp3_path.stem}.opus"
            opus_path = output_dir / opus_name
            conversion_tasks.append((mp3_path, opus_path))

        # 6. Parallel conversion
        records = []
        with ProcessPoolExecutor(max_workers=PROCESSING["FFMPEG_WORKERS"]) as executor:
            futures = {
                executor.submit(convert_mp3_to_opus, task): task
                for task in conversion_tasks
            }

            for future in as_completed(futures):
                mp3_path, opus_path = futures[future]
                try:
                    result = future.result()
                    if result and result.get("success"):
                        # Get audio duration
                        duration = get_audio_duration(opus_path)

                        records.append({
                            "original_filename": result["original_filename"],
                            "opus_path": result["opus_path"],
                            "archive_source": archive_path.name,
                            "duration_seconds": duration,
                            "file_size_bytes": result["file_size_bytes"],
                        })
                        stats["converted"] += 1
                    else:
                        stats["failed"] += 1
                        logger.warning(f"Failed to convert {mp3_path.name}")
                except Exception as e:
                    stats["failed"] += 1
                    logger.error(f"Conversion error for {mp3_path.name}: {e}")

        logger.info(
            f"Conversion complete: {stats['converted']} succeeded, "
            f"{stats['failed']} failed out of {stats['mp3_found']}"
        )

        # 7. Bulk insert to database
        if records:
            try:
                audio_ids = bulk_insert_audio_files(records)
                logger.info(f"Inserted {len(audio_ids)} records to database")

                # 8. Queue for transcription
                redis_client = get_redis_client()
                for audio_id, record in zip(audio_ids, records):
                    msg = json.dumps({
                        "audio_id": audio_id,
                        "opus_path": record["opus_path"],
                        "original_filename": record["original_filename"],
                    })
                    redis_client.lpush(REDIS["QUEUES"]["TRANSCRIBE"], msg)
                    stats["queued"] += 1

                logger.info(f"Queued {stats['queued']} files for transcription")

            except Exception as e:
                logger.error(f"Database/queue error: {e}")
                # Don't re-raise - we want to continue to cleanup

        # 9. Move archive to processed directory
        processed_path = PATHS["PROCESSED_DIR"] / archive_path.name
        try:
            shutil.move(str(archive_path), str(processed_path))
            logger.debug(f"Moved archive to {processed_path}")
        except Exception as e:
            logger.warning(f"Failed to move archive to processed: {e}")

    finally:
        # 10. Cleanup extraction directory
        try:
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
                logger.debug(f"Cleaned up {extract_dir}")
        except Exception as e:
            logger.warning(f"Failed to cleanup {extract_dir}: {e}")

    return stats


def main():
    """Main loop - block on Redis queue for archives to process."""
    logger.info("Unpack worker starting...")

    # Ensure paths exist
    from config import ensure_paths_exist
    ensure_paths_exist()

    redis_client = get_redis_client()
    logger.info("Connected to Redis, waiting for archives...")

    total_processed = 0
    total_converted = 0

    while True:
        try:
            # Block until an archive is available (timeout=0 means infinite wait)
            result = redis_client.brpop(REDIS["QUEUES"]["UNPACK"], timeout=0)

            if result is None:
                continue

            _, archive_path = result
            logger.info(f"Received archive: {archive_path}")

            try:
                stats = process_archive(archive_path)
                total_processed += 1
                total_converted += stats.get("converted", 0)

                logger.info(
                    f"Archive {stats['archive']}: {stats['converted']} converted, "
                    f"{stats['queued']} queued | "
                    f"Total: {total_processed} archives, {total_converted} files"
                )

            except Exception as e:
                logger.error(f"Failed processing {archive_path}: {e}", exc_info=True)
                # Push to failed queue for later inspection
                redis_client.lpush(REDIS["QUEUES"]["FAILED"], archive_path)

        except KeyboardInterrupt:
            logger.info("Shutdown requested, exiting...")
            break
        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)
            # Brief pause before retrying
            import time
            time.sleep(5)


if __name__ == "__main__":
    main()
