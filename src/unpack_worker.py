"""
Audio Processing Pipeline - Unpack Worker

Downloads archives from S3, extracts tar archives, converts MP3 to Opus,
and queues files for transcription. Runs on GPU VMs alongside the GPU worker.

S3 Flow:
1. Pop JSON job from queue:unpack
2. Download archive from S3 to scratch directory
3. Extract and convert MP3 → Opus in scratch
4. Set batch tracking keys in Redis
5. Queue transcription jobs with batch_id
6. Delete archive (keep opus for GPU worker)
"""

import json
import subprocess
import tarfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import LOCAL, REDIS, PROCESSING
from .s3_utils import download_archive, cleanup_scratch
from .utils import setup_logger, get_redis_client, detect_archive_type, get_audio_duration

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

    Handles mislabeled .tar.gz files that are actually plain tar,
    as well as .tar.xz (LZMA) compressed archives.

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
        elif archive_type == "tar.xz":
            with tarfile.open(archive_path, "r:xz") as tar:
                tar.extractall(path=extract_dir, filter="data")
        elif archive_type in ("tar", "gzip", "xz"):
            # Try plain tar first (common case for mislabeled files)
            try:
                with tarfile.open(archive_path, "r:") as tar:
                    tar.extractall(path=extract_dir, filter="data")
            except tarfile.ReadError:
                # Fall back to gzip if plain tar fails
                try:
                    with tarfile.open(archive_path, "r:gz") as tar:
                        tar.extractall(path=extract_dir, filter="data")
                except tarfile.ReadError:
                    # Fall back to xz/lzma if gzip also fails
                    with tarfile.open(archive_path, "r:xz") as tar:
                        tar.extractall(path=extract_dir, filter="data")
        else:
            logger.error(f"Unknown archive type for {archive_path.name}")
            return False

        return True

    except Exception as e:
        logger.error(f"Failed to extract {archive_path.name}: {e}")
        return False


def process_job(job: dict, redis_client) -> dict:
    """
    Process a job from the unpack queue: download from S3, extract, convert, queue.

    S3 Flow:
    1. Download archive from S3 to scratch directory
    2. Extract tar to scratch directory
    3. Convert MP3 → Opus in scratch
    4. Set batch tracking keys in Redis
    5. Queue transcription jobs with batch_id
    6. Delete archive (keep opus for GPU worker)

    Args:
        job: Job dict with keys: batch_id, s3_key, original_filename, transferred_at
        redis_client: Redis client instance

    Returns:
        Dict with processing statistics
    """
    batch_id = job["batch_id"]
    s3_key = job["s3_key"]
    original_filename = job.get("original_filename", "unknown")

    stats = {
        "batch_id": batch_id,
        "original_filename": original_filename,
        "mp3_found": 0,
        "converted": 0,
        "failed": 0,
        "queued": 0,
    }

    scratch_dir = None

    try:
        # 1. Download archive from S3 to scratch
        logger.info(f"Batch {batch_id}: downloading from S3 ({s3_key})")
        archive_path = download_archive(s3_key, batch_id)
        scratch_dir = archive_path.parent  # /data/scratch/{batch_id}/

        # 2. Extract archive to scratch directory
        logger.info(f"Batch {batch_id}: extracting archive")
        if not extract_archive(archive_path, scratch_dir):
            stats["failed"] = -1  # Indicate extraction failure
            raise RuntimeError(f"Failed to extract archive for batch {batch_id}")

        # 3. Find all MP3 files
        mp3_files = list(scratch_dir.rglob("*.mp3"))
        mp3_files.extend(scratch_dir.rglob("*.MP3"))  # Case insensitive
        stats["mp3_found"] = len(mp3_files)

        if not mp3_files:
            logger.warning(f"Batch {batch_id}: no MP3 files found in archive")
            # Clean up and return - not a fatal error
            archive_path.unlink(missing_ok=True)
            return stats

        logger.info(f"Batch {batch_id}: found {len(mp3_files)} MP3 files")

        # 4. Build conversion tasks (output to same scratch directory)
        conversion_tasks = []
        for mp3_path in mp3_files:
            # Create opus filename in scratch: originalname.opus
            opus_name = f"{mp3_path.stem}.opus"
            opus_path = scratch_dir / opus_name
            conversion_tasks.append((mp3_path, opus_path))

        # 5. Parallel conversion
        opus_results = []
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
                        opus_results.append({
                            "opus_path": result["opus_path"],
                            "original_filename": result["original_filename"],
                            "file_size_bytes": result["file_size_bytes"],
                        })
                        stats["converted"] += 1
                    else:
                        stats["failed"] += 1
                        logger.warning(f"Batch {batch_id}: failed to convert {mp3_path.name}")
                except Exception as e:
                    stats["failed"] += 1
                    logger.error(f"Batch {batch_id}: conversion error for {mp3_path.name}: {e}")

        logger.info(
            f"Batch {batch_id}: conversion complete - "
            f"{stats['converted']} succeeded, {stats['failed']} failed"
        )

        if not opus_results:
            logger.error(f"Batch {batch_id}: no files converted successfully")
            raise RuntimeError(f"No files converted for batch {batch_id}")

        # 6. Set batch tracking keys in Redis
        redis_client.set(f"batch:{batch_id}:total", len(opus_results))
        redis_client.set(f"batch:{batch_id}:processed", 0)
        redis_client.set(f"batch:{batch_id}:s3_key", s3_key)
        logger.info(f"Batch {batch_id}: set tracking keys (total={len(opus_results)})")

        # 7. Queue transcription jobs with batch_id
        for opus_info in opus_results:
            transcribe_job = {
                "batch_id": batch_id,
                "opus_path": opus_info["opus_path"],
                "original_filename": opus_info["original_filename"],
            }
            redis_client.lpush(REDIS["QUEUES"]["TRANSCRIBE"], json.dumps(transcribe_job))
            stats["queued"] += 1

        logger.info(f"Batch {batch_id}: queued {stats['queued']} files for transcription")

        # 8. Delete archive file (keep opus files for GPU worker)
        try:
            archive_path.unlink()
            logger.debug(f"Batch {batch_id}: deleted archive from scratch")
        except Exception as e:
            logger.warning(f"Batch {batch_id}: failed to delete archive: {e}")

        # Delete extracted MP3 files (no longer needed)
        for mp3_path in mp3_files:
            try:
                mp3_path.unlink(missing_ok=True)
            except Exception:
                pass  # Best effort cleanup

        return stats

    except Exception as e:
        logger.error(f"Batch {batch_id} failed: {e}")

        # Push to failed queue for investigation
        failed_job = {
            **job,
            "error": str(e),
            "failed_at": datetime.utcnow().isoformat() + "Z",
        }
        redis_client.lpush(REDIS["QUEUES"]["FAILED"], json.dumps(failed_job))

        # Cleanup scratch directory on failure
        if scratch_dir:
            cleanup_scratch(batch_id)

        raise


def main():
    """Main loop - block on Redis queue for jobs to process."""
    logger.info("Unpack worker starting (S3 mode)...")

    # Ensure scratch directory exists
    LOCAL["SCRATCH_ROOT"].mkdir(parents=True, exist_ok=True)

    redis_client = get_redis_client()
    logger.info("Connected to Redis, waiting for jobs...")

    total_processed = 0
    total_converted = 0

    while True:
        try:
            # Block until a job is available (timeout=0 means infinite wait)
            result = redis_client.brpop(REDIS["QUEUES"]["UNPACK"], timeout=0)

            if result is None:
                continue

            _, job_data = result

            # Parse JSON job payload
            try:
                job = json.loads(job_data)
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in job: {e} - data: {job_data[:200]}")
                redis_client.lpush(REDIS["QUEUES"]["FAILED"], job_data)
                continue

            batch_id = job.get("batch_id", "unknown")
            logger.info(f"Received job: batch_id={batch_id}")

            try:
                stats = process_job(job, redis_client)
                total_processed += 1
                total_converted += stats.get("converted", 0)

                logger.info(
                    f"Batch {stats['batch_id']}: {stats['converted']} converted, "
                    f"{stats['queued']} queued | "
                    f"Total: {total_processed} batches, {total_converted} files"
                )

            except Exception as e:
                logger.error(f"Failed processing batch {batch_id}: {e}", exc_info=True)
                # Error handling already done in process_job

        except KeyboardInterrupt:
            logger.info("Shutdown requested, exiting...")
            break
        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)
            # Brief pause before retrying
            time.sleep(5)


if __name__ == "__main__":
    main()
