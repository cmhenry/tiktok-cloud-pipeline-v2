"""
Audio Processing Pipeline - RA App Sync

Syncs high-confidence flagged items from pipeline tables to RA App's transcripts table.

This module provides both a CLI interface and programmatic API for syncing
processed audio transcripts to the RA App for research assistant review.

Usage:
    # CLI - sync with default threshold (0.7)
    python -m src.sync_to_ra_app

    # CLI - custom threshold and limit
    python -m src.sync_to_ra_app --threshold 0.8 --limit 500

    # Direct SQL (in psql)
    SELECT * FROM sync_pipeline_to_ra(0.7, 100);

    # Programmatic
    from src.sync_to_ra_app import sync
    result = sync(threshold=0.7, limit=100)
"""

import argparse
from typing import Optional

from .db import get_connection
from .utils import setup_logger

logger = setup_logger("sync_to_ra")


def sync(threshold: float = 0.7, limit: int = 100) -> dict:
    """
    Sync flagged items from pipeline to RA App transcripts table.

    Calls the sync_pipeline_to_ra() PostgreSQL function which:
    1. Queries pipeline_sync_candidates view for flagged items above threshold
    2. Skips items where meta_id already exists in RA App
    3. Creates creators if needed
    4. Inserts into RA App transcripts table
    5. Marks audio_files as synced

    Args:
        threshold: Minimum flag_score to sync (0.0-1.0, default 0.7)
        limit: Maximum items to sync per run (default 100)

    Returns:
        Dict with keys: synced, skipped, errors
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM sync_pipeline_to_ra(%s, %s)",
                (threshold, limit)
            )
            result = cur.fetchone()

            if result is None:
                return {"synced": 0, "skipped": 0, "errors": 0}

            return {
                "synced": result[0],
                "skipped": result[1],
                "errors": result[2]
            }


def get_sync_candidates(threshold: float = 0.7) -> dict:
    """
    Get counts of items eligible for sync.

    Args:
        threshold: Minimum flag_score threshold

    Returns:
        Dict with total_candidates, above_threshold, and breakdown by country
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Count all candidates
            cur.execute(
                """
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE label_1_pred >= %s) as above_threshold
                FROM pipeline_sync_candidates
                """,
                (threshold,)
            )
            counts = cur.fetchone()

            # Breakdown by country
            cur.execute(
                """
                SELECT country, COUNT(*) as count
                FROM pipeline_sync_candidates
                WHERE label_1_pred >= %s
                GROUP BY country
                ORDER BY count DESC
                """,
                (threshold,)
            )
            by_country = {row[0] or "unknown": row[1] for row in cur.fetchall()}

            return {
                "total_candidates": counts[0] or 0,
                "above_threshold": counts[1] or 0,
                "by_country": by_country
            }


def get_pipeline_stats() -> dict:
    """
    Get current pipeline processing statistics.

    Returns:
        Dict with total_processed, flagged_count, synced_count, pending_sync
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM check_pipeline_stats()")
            result = cur.fetchone()

            if result is None:
                return {
                    "total_processed": 0,
                    "flagged_count": 0,
                    "synced_count": 0,
                    "pending_sync": 0
                }

            return {
                "total_processed": result[0],
                "flagged_count": result[1],
                "synced_count": result[2],
                "pending_sync": result[3]
            }


def main():
    """CLI entry point for sync script."""
    parser = argparse.ArgumentParser(
        description="Sync high-confidence flagged items from pipeline to RA App"
    )
    parser.add_argument(
        "--threshold", "-t",
        type=float,
        default=0.7,
        help="Minimum flag_score to sync (default: 0.7)"
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=100,
        help="Max items to sync per run (default: 100)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be synced without actually syncing"
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show pipeline statistics only"
    )

    args = parser.parse_args()

    if args.stats:
        # Show stats only
        logger.info("Pipeline statistics (last 24 hours):")
        stats = get_pipeline_stats()
        logger.info(f"  Total processed: {stats['total_processed']}")
        logger.info(f"  Flagged: {stats['flagged_count']}")
        logger.info(f"  Synced to RA: {stats['synced_count']}")
        logger.info(f"  Pending sync: {stats['pending_sync']}")
        return

    if args.dry_run:
        # Show candidates without syncing
        logger.info(f"Dry run - checking candidates with threshold >= {args.threshold}")
        candidates = get_sync_candidates(args.threshold)
        logger.info(f"  Total candidates: {candidates['total_candidates']}")
        logger.info(f"  Above threshold: {candidates['above_threshold']}")
        if candidates['by_country']:
            logger.info("  By country:")
            for country, count in candidates['by_country'].items():
                logger.info(f"    {country}: {count}")
        return

    # Perform sync
    logger.info(f"Syncing items with flag_score >= {args.threshold} (limit: {args.limit})")

    result = sync(args.threshold, args.limit)

    logger.info(
        f"Sync complete: {result['synced']} synced, "
        f"{result['skipped']} skipped (existing), "
        f"{result['errors']} errors"
    )

    # Show remaining
    stats = get_pipeline_stats()
    if stats['pending_sync'] > 0:
        logger.info(f"Remaining items pending sync: {stats['pending_sync']}")


if __name__ == "__main__":
    main()
