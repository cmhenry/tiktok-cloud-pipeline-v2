#!/usr/bin/env python3
"""
Full Pipeline Integration Test

Tests the complete processing pipeline:
1. Creates test archive with synthetic audio
2. Uploads to S3
3. Pushes job to Redis unpack queue
4. Monitors batch progress via Redis tracking keys
5. Waits for completion (with timeout)
6. Verifies database records
7. Verifies S3 processed uploads
8. Cleans up test data

With --with-transfer, tests from the transfer stage using real files on AWS EC2:
1. Checks SSH connectivity to tt-zrh
2. Discovers or selects a source file on the remote host
3. Transfers the file via SCP to local staging
4. Uploads to S3
5. Pushes job to Redis unpack queue
6. Monitors and verifies through unpack stage (no GPU)

Prerequisites:
- ffmpeg installed (for generating test audio)
- All services running (redis, postgres, unpack-worker, gpu-worker)
- Environment variables configured (S3, Redis, Postgres)
- For --with-transfer: SSH access to tt-zrh configured

Usage:
    python -m tests.test_integration
    python -m tests.test_integration --keep-data
    python -m tests.test_integration --timeout 600
    python -m tests.test_integration --with-transfer
    python -m tests.test_integration --with-transfer --dry-run
    python -m tests.test_integration --with-transfer --source-file /mnt/hub/export/sound_buffer/archive.tar
    python -m tests.test_integration --with-transfer --skip-gpu-verify
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import AWS, REDIS, S3, POSTGRES, LOCAL


class IntegrationTest:
    """Run full pipeline integration test."""

    def __init__(
        self,
        timeout: int = 300,
        keep_data: bool = False,
        num_files: int = 2,
        audio_duration: int = 5,
        with_transfer: bool = False,
        source_file: str = None,
        skip_gpu_verify: bool = False,
        dry_run: bool = False,
    ):
        self.timeout = timeout
        self.keep_data = keep_data
        self.num_files = num_files
        self.audio_duration = audio_duration
        self.with_transfer = with_transfer
        self.source_file = source_file
        self.skip_gpu_verify = skip_gpu_verify or with_transfer
        self.dry_run = dry_run

        self.batch_id = f"test_{uuid.uuid4().hex[:8]}"
        self.s3_key = f"archives/{self.batch_id}.tar"
        self.test_files = []
        self.temp_dir = None
        self.created_audio_ids = []
        self.staging_dir = None
        self.transferred_file = None

    def log(self, msg: str, level: str = "INFO"):
        """Print timestamped log message."""
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = {"INFO": "[*]", "OK": "[+]", "FAIL": "[-]", "WARN": "[!]", "WAIT": "[~]"}
        print(f"{ts} {prefix.get(level, '[*]')} {msg}")

    def create_test_archive(self) -> Path:
        """
        Create a test archive with synthetic audio files.

        Uses ffmpeg to generate sine wave audio at different frequencies.

        Returns:
            Path to created tar archive
        """
        self.log(f"Creating test archive with {self.num_files} audio files...")

        self.temp_dir = tempfile.mkdtemp(prefix="pipeline_test_")
        audio_dir = Path(self.temp_dir) / "test_audio"
        audio_dir.mkdir()

        frequencies = [440, 880, 660, 550]  # Different tones for variety

        for i in range(self.num_files):
            freq = frequencies[i % len(frequencies)]
            mp3_path = audio_dir / f"test_audio_{i + 1}.mp3"

            # Generate sine wave audio
            cmd = [
                "ffmpeg",
                "-y",
                "-f", "lavfi",
                "-i", f"sine=frequency={freq}:duration={self.audio_duration}",
                "-codec:a", "libmp3lame",
                "-q:a", "2",
                str(mp3_path),
            ]

            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.returncode != 0:
                raise RuntimeError(f"Failed to create test audio: {result.stderr.decode()}")

            self.test_files.append(mp3_path.name)
            self.log(f"  Created: {mp3_path.name} ({freq}Hz, {self.audio_duration}s)")

        # Create tar archive
        archive_path = Path(self.temp_dir) / f"{self.batch_id}.tar"
        with tarfile.open(archive_path, "w") as tar:
            tar.add(audio_dir, arcname="test_audio")

        archive_size = archive_path.stat().st_size
        self.log(f"Archive created: {archive_path.name} ({archive_size / 1024:.1f} KB)", "OK")

        return archive_path

    def upload_to_s3(self, archive_path: Path) -> str:
        """
        Upload test archive to S3.

        Returns:
            S3 key of uploaded archive
        """
        self.log(f"Uploading to S3: {self.s3_key}")

        from src.s3_utils import upload_archive

        s3_key = upload_archive(archive_path, self.batch_id)
        self.log(f"Uploaded to s3://{S3['BUCKET']}/{s3_key}", "OK")

        return s3_key

    def push_to_queue(self):
        """Push job to Redis unpack queue."""
        self.log("Pushing job to Redis unpack queue...")

        import redis

        client = redis.Redis(
            host=REDIS["HOST"],
            port=REDIS["PORT"],
            decode_responses=True,
        )

        job = {
            "batch_id": self.batch_id,
            "s3_key": self.s3_key,
            "original_filename": f"{self.batch_id}.tar",
            "transferred_at": datetime.utcnow().isoformat() + "Z",
        }

        client.lpush(REDIS["QUEUES"]["UNPACK"], json.dumps(job))
        self.log(f"Job queued: batch_id={self.batch_id}", "OK")

    def check_ssh_connectivity(self) -> bool:
        """Verify SSH connectivity to tt-zrh."""
        self.log("Checking SSH connectivity to tt-zrh...")

        ssh_config = str(AWS["SSH_CONFIG_FILE"])
        cmd = ["ssh", "-F", ssh_config, "-o", "ConnectTimeout=10", AWS["HOST"], "echo", "ok"]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and "ok" in result.stdout:
                self.log("SSH connectivity to tt-zrh verified", "OK")
                return True
            else:
                self.log(f"SSH check failed: rc={result.returncode}, stderr={result.stderr.strip()}", "FAIL")
                return False
        except subprocess.TimeoutExpired:
            self.log("SSH connectivity check timed out", "FAIL")
            return False
        except Exception as e:
            self.log(f"SSH connectivity check error: {e}", "FAIL")
            return False

    def discover_source_file(self) -> str:
        """
        Discover a source file on tt-zrh to use for testing.

        Uses list_files() from transfer_sounds to find archives in the
        remote source directory.

        Returns:
            Full path to a source file on the remote host.

        Raises:
            RuntimeError: If no files are found.
        """
        if self.source_file:
            self.log(f"Using specified source file: {self.source_file}")
            from src.transfer_sounds import file_exists
            if not file_exists(self.source_file, AWS["HOST"]):
                raise RuntimeError(f"Specified source file does not exist: {self.source_file}")
            return self.source_file

        self.log(f"Discovering source files on {AWS['HOST']}:{AWS['SOURCE_DIR']}...")

        from src.transfer_sounds import list_files

        files = list_files(AWS["SOURCE_DIR"], AWS["HOST"], latency_min=10)
        # Filter out lock files
        files = [f for f in files if f and not f.endswith(".lock")]

        if not files:
            raise RuntimeError(
                f"No source files found on {AWS['HOST']}:{AWS['SOURCE_DIR']}. "
                "Use --source-file to specify a file path explicitly."
            )

        selected = files[0]
        self.log(f"Found {len(files)} file(s), selected: {selected}", "OK")
        return selected

    def transfer_from_aws(self, source_path: str) -> Path:
        """
        Transfer a file from tt-zrh to local staging via SCP.

        Args:
            source_path: Full path to the file on the remote host.

        Returns:
            Path to the local transferred file.

        Raises:
            RuntimeError: If the transfer fails.
        """
        self.log(f"Transferring {os.path.basename(source_path)} from {AWS['HOST']}...")

        from src.transfer_sounds import transfer_sound_zrh, setup_logger as transfer_setup_logger

        # Create a local staging directory
        self.staging_dir = tempfile.mkdtemp(prefix="pipeline_test_staging_")
        self.log(f"  Staging directory: {self.staging_dir}")

        # Set up a logger for the transfer function
        logger = transfer_setup_logger(
            "test_transfer", log_directory="", to_stdout=True
        )

        result = transfer_sound_zrh(
            source_path=source_path,
            dest_path=self.staging_dir,
            source_host=AWS["HOST"],
            secure=True,
            logger=logger,
        )

        if not result:
            raise RuntimeError(f"Transfer failed for {source_path}")

        filename = os.path.basename(source_path)
        local_path = Path(self.staging_dir) / filename
        if not local_path.exists():
            raise RuntimeError(f"Transfer reported success but file not found: {local_path}")

        file_size = local_path.stat().st_size
        self.transferred_file = local_path
        self.log(f"  Transferred: {filename} ({file_size / 1024 / 1024:.1f} MB)", "OK")
        return local_path

    def verify_unpack_results(self) -> dict:
        """
        Verify results through the unpack stage (no GPU verification).

        Checks:
        1. S3 archive was uploaded (HEAD object check)
        2. Redis batch tracking keys were set
        3. Jobs appeared on list:transcribe queue
        4. Scratch directory contains converted .opus files

        Returns:
            Dict with verification results.
        """
        self.log("Verifying unpack results (no GPU)...")

        results = {
            "s3_archive": False,
            "redis_batch_total": False,
            "transcribe_queue_jobs": 0,
            "opus_files": 0,
            "errors": [],
        }

        # 1. Check S3 archive exists
        try:
            from src.s3_utils import get_archive_size
            size = get_archive_size(self.s3_key)
            if size and size > 0:
                results["s3_archive"] = True
                self.log(f"  S3 archive exists: {self.s3_key} ({size / 1024 / 1024:.1f} MB)", "OK")
            else:
                self.log(f"  S3 archive not found: {self.s3_key}", "FAIL")
        except Exception as e:
            results["errors"].append(f"S3 check: {e}")
            self.log(f"  S3 archive check error: {e}", "FAIL")

        # 2. Check Redis batch tracking keys
        try:
            import redis
            client = redis.Redis(
                host=REDIS["HOST"], port=REDIS["PORT"], decode_responses=True
            )

            total = client.get(f"batch:{self.batch_id}:total")
            processed = client.get(f"batch:{self.batch_id}:processed")
            s3_key = client.get(f"batch:{self.batch_id}:s3_key")

            if total is not None:
                results["redis_batch_total"] = True
                self.log(f"  Redis batch:total = {total}", "OK")
            else:
                self.log("  Redis batch:total not set", "FAIL")

            self.log(f"  Redis batch:processed = {processed}")
            self.log(f"  Redis batch:s3_key = {s3_key}")

            # 3. Check transcribe queue for jobs from this batch
            queue_len = client.llen(REDIS["QUEUES"]["TRANSCRIBE"])
            results["transcribe_queue_jobs"] = queue_len
            if queue_len > 0:
                self.log(f"  Transcribe queue depth: {queue_len}", "OK")
            else:
                self.log("  Transcribe queue is empty (unpack may still be running)", "WARN")

        except Exception as e:
            results["errors"].append(f"Redis check: {e}")
            self.log(f"  Redis check error: {e}", "FAIL")

        # 4. Check scratch directory for opus files
        try:
            scratch_dir = LOCAL["SCRATCH_ROOT"] / self.batch_id
            if scratch_dir.exists():
                opus_files = list(scratch_dir.glob("**/*.opus"))
                results["opus_files"] = len(opus_files)
                if opus_files:
                    self.log(f"  Scratch opus files: {len(opus_files)}", "OK")
                    for f in opus_files[:5]:
                        self.log(f"    {f.name} ({f.stat().st_size / 1024:.1f} KB)")
                    if len(opus_files) > 5:
                        self.log(f"    ... and {len(opus_files) - 5} more")
                else:
                    self.log("  No opus files in scratch directory", "WARN")
            else:
                self.log(f"  Scratch directory not found: {scratch_dir}", "WARN")
        except Exception as e:
            results["errors"].append(f"Scratch check: {e}")
            self.log(f"  Scratch directory check error: {e}", "FAIL")

        # Summary
        self.log("=" * 50)
        self.log("Unpack Verification Summary")
        self.log("=" * 50)
        self.log(f"S3 archive uploaded: {'YES' if results['s3_archive'] else 'NO'}")
        self.log(f"Redis batch:total set: {'YES' if results['redis_batch_total'] else 'NO'}")
        self.log(f"Transcribe queue depth: {results['transcribe_queue_jobs']}")
        self.log(f"Opus files in scratch: {results['opus_files']}")

        if results["errors"]:
            self.log(f"Errors: {results['errors']}", "FAIL")

        success = results["s3_archive"] and results["redis_batch_total"]
        if success:
            self.log("Unpack verifications PASSED", "OK")
        else:
            self.log("Unpack verifications FAILED", "FAIL")

        results["success"] = success
        return results

    def wait_for_unpack_completion(self) -> bool:
        """
        Wait for the unpack stage to complete (no GPU).

        Considers the unpack done when batch:{id}:total is set,
        indicating the unpack worker has processed the archive and
        queued individual files for transcription.

        Returns:
            True if unpack completed, False if timeout.
        """
        self.log(f"Waiting for unpack completion (timeout: {self.timeout}s)...")

        import redis

        client = redis.Redis(
            host=REDIS["HOST"], port=REDIS["PORT"], decode_responses=True
        )

        start_time = time.time()
        last_status = None

        while time.time() - start_time < self.timeout:
            total = client.get(f"batch:{self.batch_id}:total")
            processed = client.get(f"batch:{self.batch_id}:processed")
            queue_depth = client.llen(REDIS["QUEUES"]["TRANSCRIBE"])

            elapsed = int(time.time() - start_time)
            status = f"total={total}, processed={processed}, transcribe_queue={queue_depth}"

            if status != last_status:
                self.log(f"[{elapsed}s] {status}", "WAIT")
                last_status = status

            # Unpack is done when batch:total is set (the unpack worker sets it
            # after extracting the archive and queuing individual files)
            if total is not None:
                self.log(f"Unpack complete: {total} files extracted", "OK")
                return True

            time.sleep(5)

        self.log(f"Timeout after {self.timeout}s", "FAIL")
        return False

    def wait_for_completion(self) -> bool:
        """
        Monitor batch progress and wait for completion.

        Tracks Redis batch keys:
        - batch:{batch_id}:total - expected file count
        - batch:{batch_id}:processed - completed count

        Returns:
            True if batch completed, False if timeout
        """
        self.log(f"Waiting for batch completion (timeout: {self.timeout}s)...")

        import redis

        client = redis.Redis(
            host=REDIS["HOST"],
            port=REDIS["PORT"],
            decode_responses=True,
        )

        start_time = time.time()
        last_status = None

        while time.time() - start_time < self.timeout:
            # Check if batch tracking keys exist
            total = client.get(f"batch:{self.batch_id}:total")
            processed = client.get(f"batch:{self.batch_id}:processed")

            # Check transcribe queue depth
            queue_depth = client.llen(REDIS["QUEUES"]["TRANSCRIBE"])

            elapsed = int(time.time() - start_time)
            status = f"total={total}, processed={processed}, queue={queue_depth}"

            if status != last_status:
                self.log(f"[{elapsed}s] {status}", "WAIT")
                last_status = status

            # Check for completion
            if total is not None and processed is not None:
                if int(processed) >= int(total):
                    self.log(f"Batch complete: {processed}/{total} files processed", "OK")
                    return True

            # Also check database for our test files
            completed_count = self._check_db_completion()
            if completed_count >= self.num_files:
                self.log(f"All {completed_count} files found in database", "OK")
                return True

            time.sleep(5)

        self.log(f"Timeout after {self.timeout}s", "FAIL")
        return False

    def _check_db_completion(self) -> int:
        """Check how many files from our batch are in the database."""
        try:
            import psycopg2

            conn = psycopg2.connect(
                host=POSTGRES["HOST"],
                port=POSTGRES["PORT"],
                dbname=POSTGRES["DATABASE"],
                user=POSTGRES["USER"],
                password=POSTGRES["PASSWORD"],
            )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM audio_files
                    WHERE archive_source = %s
                    """,
                    (self.batch_id,),
                )
                count = cur.fetchone()[0]

            conn.close()
            return count

        except Exception:
            return 0

    def verify_results(self) -> dict:
        """
        Verify processing results in database and S3.

        Returns:
            Dict with verification results
        """
        self.log("Verifying results...")

        results = {
            "audio_files": 0,
            "transcripts": 0,
            "classifications": 0,
            "s3_files": 0,
            "errors": [],
        }

        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor

            conn = psycopg2.connect(
                host=POSTGRES["HOST"],
                port=POSTGRES["PORT"],
                dbname=POSTGRES["DATABASE"],
                user=POSTGRES["USER"],
                password=POSTGRES["PASSWORD"],
            )

            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Check audio_files
                cur.execute(
                    """
                    SELECT id, original_filename, status, s3_opus_path
                    FROM audio_files
                    WHERE archive_source = %s
                    """,
                    (self.batch_id,),
                )
                audio_files = cur.fetchall()
                results["audio_files"] = len(audio_files)

                for af in audio_files:
                    self.created_audio_ids.append(af["id"])
                    self.log(f"  Audio: {af['original_filename']} (id={af['id']}, status={af['status']})")

                # Check transcripts
                cur.execute(
                    """
                    SELECT t.audio_file_id, t.transcript_text, t.language
                    FROM pipeline_transcripts t
                    JOIN audio_files a ON a.id = t.audio_file_id
                    WHERE a.archive_source = %s
                    """,
                    (self.batch_id,),
                )
                transcripts = cur.fetchall()
                results["transcripts"] = len(transcripts)

                for t in transcripts:
                    text_preview = (t["transcript_text"] or "")[:50]
                    self.log(f"  Transcript: audio_id={t['audio_file_id']}, lang={t['language']}, text={text_preview}...")

                # Check classifications
                cur.execute(
                    """
                    SELECT c.audio_file_id, c.flagged, c.flag_score, c.flag_category
                    FROM pipeline_classifications c
                    JOIN audio_files a ON a.id = c.audio_file_id
                    WHERE a.archive_source = %s
                    """,
                    (self.batch_id,),
                )
                classifications = cur.fetchall()
                results["classifications"] = len(classifications)

                for c in classifications:
                    status = "FLAGGED" if c["flagged"] else "OK"
                    self.log(f"  Classification: audio_id={c['audio_file_id']}, {status}, score={c['flag_score']:.2f}")

            conn.close()

            # Verify S3 processed files
            self.log("Checking S3 for processed files...")
            from src.s3_utils import get_s3_list_client

            list_client = get_s3_list_client()

            # List objects in processed/ prefix
            response = list_client.list_objects(
                Bucket=S3["BUCKET"],
                Prefix=S3["PROCESSED_PREFIX"],
                MaxKeys=100,
            )

            # Look for our audio IDs
            for obj in response.get("Contents", []):
                key = obj["Key"]
                for audio_id in self.created_audio_ids:
                    if f"/{audio_id}.opus" in key:
                        results["s3_files"] += 1
                        self.log(f"  S3: {key}")

        except Exception as e:
            results["errors"].append(str(e))
            self.log(f"Verification error: {e}", "FAIL")

        # Summary
        self.log("=" * 50)
        self.log("Verification Summary")
        self.log("=" * 50)
        self.log(f"Audio files: {results['audio_files']}/{self.num_files}")
        self.log(f"Transcripts: {results['transcripts']}/{self.num_files}")
        self.log(f"Classifications: {results['classifications']}/{self.num_files}")
        self.log(f"S3 processed files: {results['s3_files']}/{self.num_files}")

        if results["errors"]:
            self.log(f"Errors: {results['errors']}", "FAIL")

        # Determine success
        success = (
            results["audio_files"] == self.num_files
            and results["transcripts"] == self.num_files
            and results["classifications"] == self.num_files
        )

        if success:
            self.log("All verifications PASSED", "OK")
        else:
            self.log("Some verifications FAILED", "FAIL")

        results["success"] = success
        return results

    def verify_redis_cleanup(self) -> bool:
        """Verify Redis batch tracking keys are cleaned up."""
        self.log("Checking Redis cleanup...")

        import redis

        client = redis.Redis(
            host=REDIS["HOST"],
            port=REDIS["PORT"],
            decode_responses=True,
        )

        keys = [
            f"batch:{self.batch_id}:total",
            f"batch:{self.batch_id}:processed",
            f"batch:{self.batch_id}:s3_key",
        ]

        remaining = []
        for key in keys:
            if client.exists(key):
                remaining.append(key)

        if remaining:
            self.log(f"Redis keys still exist (may be cleaned by worker): {remaining}", "WARN")
            return False

        self.log("Redis batch keys cleaned up", "OK")
        return True

    def cleanup(self):
        """Clean up test data from S3 and database."""
        if self.keep_data:
            self.log("Keeping test data (--keep-data specified)")
            return

        self.log("Cleaning up test data...")

        # Clean S3 archive
        try:
            from src.s3_utils import get_s3_client

            client = get_s3_client()
            client.delete_object(Bucket=S3["BUCKET"], Key=self.s3_key)
            self.log(f"  Deleted S3 archive: {self.s3_key}")
        except Exception as e:
            self.log(f"  Failed to delete S3 archive: {e}", "WARN")

        # Clean S3 processed files
        from src.s3_utils import get_s3_list_client

        list_client = get_s3_list_client()
        for audio_id in self.created_audio_ids:
            try:
                # Find and delete processed opus files
                response = list_client.list_objects(
                    Bucket=S3["BUCKET"],
                    Prefix=f"{S3['PROCESSED_PREFIX']}",
                    MaxKeys=1000,
                )
                for obj in response.get("Contents", []):
                    if f"/{audio_id}.opus" in obj["Key"]:
                        client.delete_object(Bucket=S3["BUCKET"], Key=obj["Key"])
                        self.log(f"  Deleted S3 processed: {obj['Key']}")
            except Exception as e:
                self.log(f"  Failed to delete S3 processed file: {e}", "WARN")

        # Clean database records
        try:
            import psycopg2

            conn = psycopg2.connect(
                host=POSTGRES["HOST"],
                port=POSTGRES["PORT"],
                dbname=POSTGRES["DATABASE"],
                user=POSTGRES["USER"],
                password=POSTGRES["PASSWORD"],
            )

            with conn.cursor() as cur:
                # Delete in correct order due to foreign keys
                cur.execute(
                    "DELETE FROM pipeline_classifications WHERE audio_file_id IN (SELECT id FROM audio_files WHERE archive_source = %s)",
                    (self.batch_id,),
                )
                cur.execute(
                    "DELETE FROM pipeline_transcripts WHERE audio_file_id IN (SELECT id FROM audio_files WHERE archive_source = %s)",
                    (self.batch_id,),
                )
                cur.execute(
                    "DELETE FROM audio_files WHERE archive_source = %s",
                    (self.batch_id,),
                )
                conn.commit()
                self.log("  Deleted database records")

            conn.close()

        except Exception as e:
            self.log(f"  Failed to clean database: {e}", "WARN")

        # Clean Redis keys
        try:
            import redis

            client = redis.Redis(
                host=REDIS["HOST"],
                port=REDIS["PORT"],
                decode_responses=True,
            )

            keys = client.keys(f"batch:{self.batch_id}:*")
            if keys:
                client.delete(*keys)
                self.log(f"  Deleted Redis keys: {keys}")

        except Exception as e:
            self.log(f"  Failed to clean Redis: {e}", "WARN")

        # Clean temp directory
        if self.temp_dir and Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            self.log(f"  Deleted temp directory: {self.temp_dir}")

        # Clean staging directory (transfer mode)
        if self.staging_dir and Path(self.staging_dir).exists():
            shutil.rmtree(self.staging_dir, ignore_errors=True)
            self.log(f"  Deleted staging directory: {self.staging_dir}")

        # Clean scratch directory for this batch
        scratch_dir = LOCAL["SCRATCH_ROOT"] / self.batch_id
        if scratch_dir.exists():
            shutil.rmtree(scratch_dir, ignore_errors=True)
            self.log(f"  Deleted scratch directory: {scratch_dir}")

        self.log("Cleanup complete", "OK")

    def run(self) -> bool:
        """
        Run the full integration test.

        Returns:
            True if all tests passed, False otherwise
        """
        mode = "Transfer + Unpack" if self.with_transfer else "Full Pipeline"
        dry_run_tag = " [DRY RUN]" if self.dry_run else ""
        self.log("=" * 60)
        self.log(f"{mode} Integration Test{dry_run_tag}")
        self.log("=" * 60)
        self.log(f"Batch ID: {self.batch_id}")
        if not self.with_transfer:
            self.log(f"Files: {self.num_files}")
        self.log(f"Timeout: {self.timeout}s")
        if self.dry_run:
            self.log("Mode: DRY RUN (no data will be transferred, uploaded, or queued)")
        if self.skip_gpu_verify:
            self.log("GPU verification: SKIPPED")
        print()

        try:
            if self.with_transfer:
                return self._run_transfer_flow()
            else:
                return self._run_synthetic_flow()

        except Exception as e:
            self.log(f"Test FAILED with exception: {e}", "FAIL")
            import traceback
            traceback.print_exc()
            return False

        finally:
            self.cleanup()

    def _run_transfer_flow(self) -> bool:
        """Run the transfer-first integration test flow."""

        # Phase 1: SSH connectivity check
        if not self.check_ssh_connectivity():
            self.log("Test FAILED: cannot reach tt-zrh via SSH", "FAIL")
            return False
        print()

        # Phase 2: Discover source file
        source_path = self.discover_source_file()
        print()

        if self.dry_run:
            return self._dry_run_transfer_report(source_path)

        # Phase 3: Transfer file from AWS
        local_archive = self.transfer_from_aws(source_path)
        print()

        # Phase 4: Upload to S3
        self.upload_to_s3(local_archive)
        print()

        # Phase 5: Push to unpack queue
        self.push_to_queue()
        print()

        # Phase 6: Wait for unpack to complete
        completed = self.wait_for_unpack_completion()
        print()

        if not completed:
            self.log("Test FAILED: timeout waiting for unpack completion", "FAIL")
            return False

        # Phase 7: Verify unpack results
        results = self.verify_unpack_results()
        print()

        return results["success"]

    def _dry_run_transfer_report(self, source_path: str) -> bool:
        """Show what the transfer flow would do without executing anything."""
        from src.transfer_sounds import get_file_size

        filename = os.path.basename(source_path)
        file_size = get_file_size(source_path, AWS["HOST"])
        size_str = f"{file_size / 1024 / 1024:.1f} MB" if file_size else "unknown size"

        self.log("[DRY RUN] Would execute the following steps:")
        self.log(f"  1. Transfer {filename} ({size_str}) from {AWS['HOST']} to local staging")
        self.log(f"  2. Upload to S3 as s3://{S3['BUCKET']}/{self.s3_key}")
        self.log(f"  3. Push unpack job to Redis queue '{REDIS['QUEUES']['UNPACK']}' with batch_id={self.batch_id}")
        self.log(f"  4. Wait up to {self.timeout}s for unpack worker to process the archive")
        self.log(f"  5. Verify: S3 archive exists, Redis batch keys set, transcribe queue populated, opus files in scratch")
        if not self.keep_data:
            self.log("  6. Cleanup: delete S3 archive, Redis keys, staging dir, scratch dir")
        else:
            self.log("  6. Cleanup: SKIPPED (--keep-data)")
        print()
        self.log("Dry run complete -- no data was modified", "OK")
        return True

    def _run_synthetic_flow(self) -> bool:
        """Run the original synthetic audio integration test flow."""

        # Phase 1: Create test archive
        archive_path = self.create_test_archive()
        print()

        # Phase 2: Upload to S3
        self.upload_to_s3(archive_path)
        print()

        # Phase 3: Push to queue
        self.push_to_queue()
        print()

        # Phase 4: Wait for completion
        if self.skip_gpu_verify:
            completed = self.wait_for_unpack_completion()
        else:
            completed = self.wait_for_completion()
        print()

        if not completed:
            self.log("Test FAILED: timeout waiting for completion", "FAIL")
            return False

        # Phase 5: Verify results
        if self.skip_gpu_verify:
            results = self.verify_unpack_results()
        else:
            results = self.verify_results()
        print()

        # Phase 6: Check Redis cleanup (only for full pipeline)
        if not self.skip_gpu_verify:
            self.verify_redis_cleanup()
            print()

        return results["success"]


def main():
    parser = argparse.ArgumentParser(
        description="Run full pipeline integration test"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout in seconds (default: 300)",
    )
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="Keep test data after completion (don't cleanup)",
    )
    parser.add_argument(
        "--num-files",
        type=int,
        default=2,
        help="Number of test audio files to create (default: 2)",
    )
    parser.add_argument(
        "--audio-duration",
        type=int,
        default=5,
        help="Duration of test audio in seconds (default: 5)",
    )
    parser.add_argument(
        "--with-transfer",
        action="store_true",
        help="Test from transfer stage: SCP real file from tt-zrh, upload to S3, unpack",
    )
    parser.add_argument(
        "--source-file",
        type=str,
        default=None,
        help="Path to a specific file on tt-zrh (used with --with-transfer)",
    )
    parser.add_argument(
        "--skip-gpu-verify",
        action="store_true",
        help="Skip DB verification of transcripts/classifications (verify unpack only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without transferring, uploading, or queuing anything",
    )

    args = parser.parse_args()

    test = IntegrationTest(
        timeout=args.timeout,
        keep_data=args.keep_data,
        num_files=args.num_files,
        audio_duration=args.audio_duration,
        with_transfer=args.with_transfer,
        source_file=args.source_file,
        skip_gpu_verify=args.skip_gpu_verify,
        dry_run=args.dry_run,
    )

    success = test.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
