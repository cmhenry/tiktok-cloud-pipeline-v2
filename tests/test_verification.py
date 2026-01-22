#!/usr/bin/env python3
"""
Database and S3 Result Verification

Standalone verification tool for checking processing results.
Can be used to verify test batches or investigate production data.

Runs verification queries:
- Audio file status counts by batch
- Transcript completeness check
- Classification completeness check
- S3 processed file existence

Usage:
    python -m tests.test_verification --batch test_abc123
    python -m tests.test_verification --batch test_%  # All test batches
    python -m tests.test_verification --recent 24     # Last 24 hours
    python -m tests.test_verification --stats         # Overall stats
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import POSTGRES, S3


class ResultVerifier:
    """Verify processing results in database and S3."""

    def __init__(self):
        self.conn = None

    def connect(self):
        """Establish database connection."""
        import psycopg2
        from psycopg2.extras import RealDictCursor

        self.conn = psycopg2.connect(
            host=POSTGRES["HOST"],
            port=POSTGRES["PORT"],
            dbname=POSTGRES["DATABASE"],
            user=POSTGRES["USER"],
            password=POSTGRES["PASSWORD"],
            cursor_factory=RealDictCursor,
        )

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()

    def log(self, msg: str, level: str = "INFO"):
        """Print formatted log message."""
        prefix = {"INFO": "[*]", "OK": "[+]", "FAIL": "[-]", "WARN": "[!]", "DATA": "   "}
        print(f"{prefix.get(level, '[*]')} {msg}")

    def verify_batch(self, batch_pattern: str) -> dict:
        """
        Verify processing results for a batch pattern.

        Args:
            batch_pattern: Archive source pattern (supports SQL LIKE wildcards)

        Returns:
            Dict with verification results
        """
        self.log(f"Verifying batch: {batch_pattern}")
        self.log("=" * 60)

        results = {
            "batch_pattern": batch_pattern,
            "audio_files": {},
            "transcripts": {},
            "classifications": {},
            "issues": [],
        }

        with self.conn.cursor() as cur:
            # Query 1: Audio files status counts
            self.log("Audio files by status:")
            cur.execute(
                """
                SELECT
                    archive_source,
                    status,
                    COUNT(*) as count
                FROM audio_files
                WHERE archive_source LIKE %s
                GROUP BY archive_source, status
                ORDER BY archive_source, status
                """,
                (batch_pattern,),
            )

            for row in cur.fetchall():
                key = f"{row['archive_source']}:{row['status']}"
                results["audio_files"][key] = row["count"]
                self.log(f"  {row['archive_source']}: {row['status']} = {row['count']}", "DATA")

            if not results["audio_files"]:
                self.log("  No audio files found matching pattern", "WARN")
                results["issues"].append("No audio files found")

            print()

            # Query 2: Transcript completeness
            self.log("Transcript coverage:")
            cur.execute(
                """
                SELECT
                    a.archive_source,
                    COUNT(*) as total_files,
                    COUNT(t.id) as has_transcript,
                    COUNT(*) - COUNT(t.id) as missing_transcript
                FROM audio_files a
                LEFT JOIN pipeline_transcripts t ON t.audio_file_id = a.id
                WHERE a.archive_source LIKE %s
                GROUP BY a.archive_source
                ORDER BY a.archive_source
                """,
                (batch_pattern,),
            )

            for row in cur.fetchall():
                results["transcripts"][row["archive_source"]] = {
                    "total": row["total_files"],
                    "has_transcript": row["has_transcript"],
                    "missing": row["missing_transcript"],
                }
                status = "OK" if row["missing_transcript"] == 0 else "WARN"
                self.log(
                    f"  {row['archive_source']}: {row['has_transcript']}/{row['total_files']} "
                    f"(missing: {row['missing_transcript']})",
                    status,
                )
                if row["missing_transcript"] > 0:
                    results["issues"].append(f"Missing transcripts in {row['archive_source']}")

            print()

            # Query 3: Classification completeness
            self.log("Classification coverage:")
            cur.execute(
                """
                SELECT
                    a.archive_source,
                    COUNT(*) as total_files,
                    COUNT(c.id) as has_classification,
                    COUNT(*) FILTER (WHERE c.flagged = true) as flagged_count,
                    COUNT(*) - COUNT(c.id) as missing_classification
                FROM audio_files a
                LEFT JOIN pipeline_classifications c ON c.audio_file_id = a.id
                WHERE a.archive_source LIKE %s
                GROUP BY a.archive_source
                ORDER BY a.archive_source
                """,
                (batch_pattern,),
            )

            for row in cur.fetchall():
                results["classifications"][row["archive_source"]] = {
                    "total": row["total_files"],
                    "has_classification": row["has_classification"],
                    "flagged": row["flagged_count"],
                    "missing": row["missing_classification"],
                }
                status = "OK" if row["missing_classification"] == 0 else "WARN"
                self.log(
                    f"  {row['archive_source']}: {row['has_classification']}/{row['total_files']} "
                    f"(flagged: {row['flagged_count']}, missing: {row['missing_classification']})",
                    status,
                )
                if row["missing_classification"] > 0:
                    results["issues"].append(f"Missing classifications in {row['archive_source']}")

            print()

            # Query 4: Detailed view with all joins
            self.log("Detailed file status:")
            cur.execute(
                """
                SELECT
                    a.id,
                    a.original_filename,
                    a.status,
                    a.s3_opus_path,
                    t.transcript_text IS NOT NULL as has_transcript,
                    t.language,
                    c.flagged,
                    c.flag_score,
                    c.flag_category
                FROM audio_files a
                LEFT JOIN pipeline_transcripts t ON t.audio_file_id = a.id
                LEFT JOIN pipeline_classifications c ON c.audio_file_id = a.id
                WHERE a.archive_source LIKE %s
                ORDER BY a.id
                LIMIT 50
                """,
                (batch_pattern,),
            )

            rows = cur.fetchall()
            for row in rows:
                parts = [
                    f"id={row['id']}",
                    f"file={row['original_filename'][:30]}",
                    f"status={row['status']}",
                    f"transcript={'Y' if row['has_transcript'] else 'N'}",
                ]
                if row["has_transcript"]:
                    parts.append(f"lang={row['language']}")
                if row["flagged"] is not None:
                    parts.append(f"flagged={row['flagged']}")
                    if row["flagged"]:
                        parts.append(f"score={row['flag_score']:.2f}")
                        parts.append(f"cat={row['flag_category']}")

                self.log(f"  {', '.join(parts)}", "DATA")

            if len(rows) == 50:
                self.log("  ... (showing first 50 records)", "DATA")

        return results

    def verify_recent(self, hours: int = 24) -> dict:
        """
        Verify processing results from recent hours.

        Args:
            hours: Number of hours to look back

        Returns:
            Dict with verification results
        """
        self.log(f"Verifying records from last {hours} hours")
        self.log("=" * 60)

        cutoff = datetime.utcnow() - timedelta(hours=hours)
        results = {"period_hours": hours, "stats": {}}

        with self.conn.cursor() as cur:
            # Overall status counts
            cur.execute(
                """
                SELECT
                    status,
                    COUNT(*) as count
                FROM audio_files
                WHERE created_at > %s
                GROUP BY status
                ORDER BY count DESC
                """,
                (cutoff,),
            )

            self.log("Audio files by status:")
            for row in cur.fetchall():
                results["stats"][row["status"]] = row["count"]
                self.log(f"  {row['status']}: {row['count']}", "DATA")

            print()

            # Completeness check
            cur.execute(
                """
                SELECT
                    COUNT(*) as total,
                    COUNT(t.id) as with_transcript,
                    COUNT(c.id) as with_classification,
                    COUNT(*) FILTER (WHERE c.flagged = true) as flagged
                FROM audio_files a
                LEFT JOIN pipeline_transcripts t ON t.audio_file_id = a.id
                LEFT JOIN pipeline_classifications c ON c.audio_file_id = a.id
                WHERE a.created_at > %s
                """,
                (cutoff,),
            )

            row = cur.fetchone()
            results["total"] = row["total"]
            results["with_transcript"] = row["with_transcript"]
            results["with_classification"] = row["with_classification"]
            results["flagged"] = row["flagged"]

            self.log("Pipeline completeness:")
            self.log(f"  Total files: {row['total']}", "DATA")
            self.log(f"  With transcript: {row['with_transcript']} ({100*row['with_transcript']/max(row['total'],1):.1f}%)", "DATA")
            self.log(f"  With classification: {row['with_classification']} ({100*row['with_classification']/max(row['total'],1):.1f}%)", "DATA")
            self.log(f"  Flagged: {row['flagged']}", "DATA")

            print()

            # Batches processed
            cur.execute(
                """
                SELECT
                    archive_source,
                    COUNT(*) as file_count,
                    MIN(created_at) as started,
                    MAX(processed_at) as completed
                FROM audio_files
                WHERE created_at > %s
                GROUP BY archive_source
                ORDER BY MIN(created_at) DESC
                LIMIT 20
                """,
                (cutoff,),
            )

            self.log("Recent batches:")
            for row in cur.fetchall():
                started = row["started"].strftime("%H:%M:%S") if row["started"] else "N/A"
                completed = row["completed"].strftime("%H:%M:%S") if row["completed"] else "N/A"
                self.log(f"  {row['archive_source']}: {row['file_count']} files ({started} - {completed})", "DATA")

        return results

    def get_overall_stats(self) -> dict:
        """
        Get overall processing statistics.

        Returns:
            Dict with statistics
        """
        self.log("Overall Processing Statistics")
        self.log("=" * 60)

        stats = {}

        with self.conn.cursor() as cur:
            # Total counts
            cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM audio_files) as total_files,
                    (SELECT COUNT(*) FROM pipeline_transcripts) as total_transcripts,
                    (SELECT COUNT(*) FROM pipeline_classifications) as total_classifications,
                    (SELECT COUNT(*) FROM pipeline_classifications WHERE flagged = true) as total_flagged
                """
            )

            row = cur.fetchone()
            stats["total_files"] = row["total_files"]
            stats["total_transcripts"] = row["total_transcripts"]
            stats["total_classifications"] = row["total_classifications"]
            stats["total_flagged"] = row["total_flagged"]

            self.log("Total counts:")
            self.log(f"  Audio files: {row['total_files']:,}", "DATA")
            self.log(f"  Transcripts: {row['total_transcripts']:,}", "DATA")
            self.log(f"  Classifications: {row['total_classifications']:,}", "DATA")
            self.log(f"  Flagged: {row['total_flagged']:,}", "DATA")

            print()

            # Last 24h activity
            cur.execute(
                """
                SELECT
                    DATE_TRUNC('hour', created_at) as hour,
                    COUNT(*) as count
                FROM audio_files
                WHERE created_at > NOW() - INTERVAL '24 hours'
                GROUP BY DATE_TRUNC('hour', created_at)
                ORDER BY hour DESC
                LIMIT 24
                """
            )

            self.log("Files processed per hour (last 24h):")
            hourly = []
            for row in cur.fetchall():
                hour_str = row["hour"].strftime("%Y-%m-%d %H:00")
                hourly.append((hour_str, row["count"]))
                self.log(f"  {hour_str}: {row['count']}", "DATA")

            stats["hourly"] = hourly

            print()

            # Status distribution
            cur.execute(
                """
                SELECT status, COUNT(*) as count
                FROM audio_files
                GROUP BY status
                ORDER BY count DESC
                """
            )

            self.log("Status distribution:")
            status_counts = {}
            for row in cur.fetchall():
                status_counts[row["status"]] = row["count"]
                self.log(f"  {row['status']}: {row['count']:,}", "DATA")

            stats["status_counts"] = status_counts

        return stats

    def verify_s3_files(self, batch_pattern: str) -> dict:
        """
        Verify S3 processed files exist for a batch.

        Args:
            batch_pattern: Archive source pattern

        Returns:
            Dict with S3 verification results
        """
        self.log(f"Verifying S3 files for: {batch_pattern}")
        self.log("=" * 60)

        results = {"found": 0, "missing": 0, "files": []}

        # Get audio files with S3 paths
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, original_filename, s3_opus_path
                FROM audio_files
                WHERE archive_source LIKE %s
                  AND s3_opus_path IS NOT NULL
                """,
                (batch_pattern,),
            )
            files = cur.fetchall()

        if not files:
            self.log("No files with S3 paths found", "WARN")
            return results

        try:
            from src.s3_utils import get_s3_client

            client = get_s3_client()

            for f in files:
                try:
                    client.head_object(Bucket=S3["BUCKET"], Key=f["s3_opus_path"])
                    results["found"] += 1
                    results["files"].append({"id": f["id"], "path": f["s3_opus_path"], "exists": True})
                    self.log(f"  Found: {f['s3_opus_path']}", "OK")
                except Exception:
                    results["missing"] += 1
                    results["files"].append({"id": f["id"], "path": f["s3_opus_path"], "exists": False})
                    self.log(f"  Missing: {f['s3_opus_path']}", "FAIL")

        except Exception as e:
            self.log(f"S3 verification error: {e}", "FAIL")
            results["error"] = str(e)

        print()
        self.log(f"S3 Summary: {results['found']} found, {results['missing']} missing")

        return results


def main():
    parser = argparse.ArgumentParser(
        description="Verify processing results in database and S3"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--batch",
        help="Batch/archive source pattern (supports SQL LIKE wildcards)",
    )
    group.add_argument(
        "--recent",
        type=int,
        metavar="HOURS",
        help="Verify records from last N hours",
    )
    group.add_argument(
        "--stats",
        action="store_true",
        help="Show overall statistics",
    )

    parser.add_argument(
        "--s3",
        action="store_true",
        help="Also verify S3 file existence (slower)",
    )

    args = parser.parse_args()

    verifier = ResultVerifier()

    try:
        verifier.connect()

        if args.batch:
            results = verifier.verify_batch(args.batch)
            if args.s3:
                s3_results = verifier.verify_s3_files(args.batch)
                results["s3"] = s3_results

            # Exit with error if issues found
            if results.get("issues"):
                sys.exit(1)

        elif args.recent:
            verifier.verify_recent(args.recent)

        elif args.stats:
            verifier.get_overall_stats()

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    finally:
        verifier.close()


if __name__ == "__main__":
    main()
