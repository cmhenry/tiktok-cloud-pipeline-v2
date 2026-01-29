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

Prerequisites:
- ffmpeg installed (for generating test audio)
- All services running (redis, postgres, unpack-worker, gpu-worker)
- Environment variables configured (S3, Redis, Postgres)

Usage:
    python -m tests.test_integration
    python -m tests.test_integration --keep-data
    python -m tests.test_integration --timeout 600
"""

import argparse
import json
import os
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

from src.config import REDIS, S3, POSTGRES


class IntegrationTest:
    """Run full pipeline integration test."""

    def __init__(
        self,
        timeout: int = 300,
        keep_data: bool = False,
        num_files: int = 2,
        audio_duration: int = 5,
    ):
        self.timeout = timeout
        self.keep_data = keep_data
        self.num_files = num_files
        self.audio_duration = audio_duration

        self.batch_id = f"test_{uuid.uuid4().hex[:8]}"
        self.s3_key = f"archives/{self.batch_id}.tar"
        self.test_files = []
        self.temp_dir = None
        self.created_audio_ids = []

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
            from src.s3_utils import get_s3_client

            client = get_s3_client()

            # List objects in processed/ prefix
            response = client.list_objects(
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
        for audio_id in self.created_audio_ids:
            try:
                # Find and delete processed opus files
                response = client.list_objects(
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
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            self.log(f"  Deleted temp directory: {self.temp_dir}")

        self.log("Cleanup complete", "OK")

    def run(self) -> bool:
        """
        Run the full integration test.

        Returns:
            True if all tests passed, False otherwise
        """
        self.log("=" * 60)
        self.log("Full Pipeline Integration Test")
        self.log("=" * 60)
        self.log(f"Batch ID: {self.batch_id}")
        self.log(f"Files: {self.num_files}")
        self.log(f"Timeout: {self.timeout}s")
        print()

        try:
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
            completed = self.wait_for_completion()
            print()

            if not completed:
                self.log("Test FAILED: timeout waiting for completion", "FAIL")
                return False

            # Phase 5: Verify results
            results = self.verify_results()
            print()

            # Phase 6: Check Redis cleanup
            self.verify_redis_cleanup()
            print()

            return results["success"]

        except Exception as e:
            self.log(f"Test FAILED with exception: {e}", "FAIL")
            import traceback
            traceback.print_exc()
            return False

        finally:
            self.cleanup()


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

    args = parser.parse_args()

    test = IntegrationTest(
        timeout=args.timeout,
        keep_data=args.keep_data,
        num_files=args.num_files,
        audio_duration=args.audio_duration,
    )

    success = test.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
