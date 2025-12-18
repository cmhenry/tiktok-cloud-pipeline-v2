"""
Audio Processing Pipeline - Database Module

PostgreSQL connection pool and CRUD operations for audio processing pipeline.
"""

from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2 import pool
from psycopg2.extras import execute_values, RealDictCursor

from config import get_postgres_dsn

_pool: Optional[pool.ThreadedConnectionPool] = None


def get_db_pool(min_conn: int = 2, max_conn: int = 10) -> pool.ThreadedConnectionPool:
    """
    Initialize and return the connection pool.

    Args:
        min_conn: Minimum number of connections in pool
        max_conn: Maximum number of connections in pool

    Returns:
        ThreadedConnectionPool instance
    """
    global _pool
    if _pool is None:
        _pool = pool.ThreadedConnectionPool(
            minconn=min_conn,
            maxconn=max_conn,
            dsn=get_postgres_dsn()
        )
    return _pool


def close_db_pool():
    """Close all connections in the pool."""
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


@contextmanager
def get_connection():
    """
    Get a connection from the pool as a context manager.

    Yields:
        psycopg2 connection object

    Example:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
    """
    db_pool = get_db_pool()
    conn = db_pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)


def insert_audio_file(
    original_filename: str,
    opus_path: str,
    archive_source: str,
    duration_seconds: Optional[float],
    file_size_bytes: int,
) -> int:
    """
    Insert a single audio file record.

    Args:
        original_filename: Original MP3 filename
        opus_path: Path to converted opus file
        archive_source: Source archive name
        duration_seconds: Audio duration in seconds
        file_size_bytes: File size in bytes

    Returns:
        New record ID
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audio_files
                    (original_filename, opus_path, archive_source, duration_seconds, file_size_bytes)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (original_filename, opus_path, archive_source, duration_seconds, file_size_bytes)
            )
            return cur.fetchone()[0]


def bulk_insert_audio_files(records: list[dict]) -> list[int]:
    """
    Batch insert multiple audio file records.

    Args:
        records: List of dicts with keys:
            - original_filename
            - opus_path
            - archive_source
            - duration_seconds
            - file_size_bytes

    Returns:
        List of new record IDs
    """
    if not records:
        return []

    with get_connection() as conn:
        with conn.cursor() as cur:
            values = [
                (
                    r["original_filename"],
                    r["opus_path"],
                    r["archive_source"],
                    r.get("duration_seconds"),
                    r["file_size_bytes"],
                )
                for r in records
            ]
            result = execute_values(
                cur,
                """
                INSERT INTO audio_files
                    (original_filename, opus_path, archive_source, duration_seconds, file_size_bytes)
                VALUES %s
                RETURNING id
                """,
                values,
                fetch=True
            )
            return [row[0] for row in result]


def insert_transcript(
    audio_id: int,
    text: str,
    language: str,
    confidence: float
):
    """
    Insert a transcript record.

    Args:
        audio_id: Foreign key to audio_files
        text: Transcribed text
        language: Detected language code
        confidence: Transcription confidence score
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO transcripts
                    (audio_file_id, transcript_text, language, confidence)
                VALUES (%s, %s, %s, %s)
                """,
                (audio_id, text, language, confidence)
            )


def insert_classification(
    audio_id: int,
    flagged: bool,
    score: float,
    category: Optional[str]
):
    """
    Insert a classification result.

    Args:
        audio_id: Foreign key to audio_files
        flagged: Whether content was flagged
        score: Classification confidence score (0.0-1.0)
        category: Flag category if flagged
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO classifications
                    (audio_file_id, flagged, flag_score, flag_category)
                VALUES (%s, %s, %s, %s)
                """,
                (audio_id, flagged, score, category)
            )


def update_audio_status(audio_id: int, status: str):
    """
    Update the processing status of an audio file.

    Args:
        audio_id: Audio file record ID
        status: New status (pending, transcribed, flagged, reviewed, failed)
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE audio_files
                SET status = %s, processed_at = NOW()
                WHERE id = %s
                """,
                (status, audio_id)
            )


def get_pending_flagged(limit: int = 100) -> list[dict]:
    """
    Get flagged items awaiting RA review.

    Args:
        limit: Maximum number of records to return

    Returns:
        List of flagged items with transcript and classification details
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    af.id,
                    af.original_filename,
                    af.opus_path,
                    t.transcript_text,
                    c.flag_score,
                    c.flag_category,
                    af.created_at
                FROM audio_files af
                JOIN transcripts t ON t.audio_file_id = af.id
                JOIN classifications c ON c.audio_file_id = af.id
                WHERE c.flagged = true
                  AND af.status = 'flagged'
                  AND af.created_at > NOW() - INTERVAL '24 hours'
                ORDER BY c.flag_score DESC
                LIMIT %s
                """,
                (limit,)
            )
            return cur.fetchall()


def get_processing_stats() -> dict:
    """
    Get current processing statistics.

    Returns:
        Dict with counts by status and totals
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    status,
                    COUNT(*) as count
                FROM audio_files
                WHERE created_at > NOW() - INTERVAL '24 hours'
                GROUP BY status
                """
            )
            status_counts = {row["status"]: row["count"] for row in cur.fetchall()}

            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE c.flagged = true) as flagged_count,
                    COUNT(*) as total_classified
                FROM classifications c
                JOIN audio_files af ON af.id = c.audio_file_id
                WHERE af.created_at > NOW() - INTERVAL '24 hours'
                """
            )
            classification_stats = cur.fetchone()

            return {
                "status_counts": status_counts,
                "flagged_count": classification_stats["flagged_count"] or 0,
                "total_classified": classification_stats["total_classified"] or 0,
            }
