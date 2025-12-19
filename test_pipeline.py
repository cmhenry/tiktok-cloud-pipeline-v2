"""
Test Script - Single Archive Pipeline Test

Tests the complete worker pipeline (unpack, convert, transcribe, classify)
on a single .tar archive without requiring Redis or PostgreSQL.

Usage:
    python test_pipeline.py /path/to/archive.tar.gz [--keep-files] [--skip-gpu]
"""

import argparse
import shutil
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# Import functions from worker modules
from unpack_worker import extract_archive, convert_mp3_to_opus
from gpu_worker import GPUWorker
from utils import setup_logger, get_audio_duration

logger = setup_logger("test_pipeline", log_dir=Path.cwd())


def test_extraction(archive_path: Path, extract_dir: Path) -> list[Path]:
    """
    Test archive extraction.

    Returns:
        List of MP3 files found in the archive
    """
    logger.info(f"[1/4] Extracting archive: {archive_path.name}")

    if not extract_archive(archive_path, extract_dir):
        logger.error("Extraction failed!")
        return []

    # Find all MP3 files (case insensitive)
    mp3_files = list(extract_dir.rglob("*.mp3"))
    mp3_files.extend(extract_dir.rglob("*.MP3"))

    logger.info(f"      Found {len(mp3_files)} MP3 file(s)")
    for mp3 in mp3_files[:5]:  # Show first 5
        logger.info(f"        - {mp3.name}")
    if len(mp3_files) > 5:
        logger.info(f"        ... and {len(mp3_files) - 5} more")

    return mp3_files


def test_conversion(mp3_files: list[Path], output_dir: Path, max_workers: int = 4) -> list[dict]:
    """
    Test MP3 to Opus conversion.

    Returns:
        List of dicts with conversion results
    """
    logger.info(f"[2/4] Converting {len(mp3_files)} MP3 file(s) to Opus")

    # Prepare conversion tasks
    tasks = []
    for mp3_path in mp3_files:
        opus_name = f"{mp3_path.stem}.opus"
        opus_path = output_dir / opus_name
        tasks.append((mp3_path, opus_path))

    results = []
    succeeded = 0
    failed = 0

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(convert_mp3_to_opus, task): task for task in tasks}

        for future in as_completed(futures):
            mp3_path, opus_path = futures[future]
            try:
                result = future.result()
                if result and result.get("success"):
                    duration = get_audio_duration(opus_path)
                    results.append({
                        "original_filename": result["original_filename"],
                        "opus_path": str(opus_path),
                        "file_size_bytes": result["file_size_bytes"],
                        "duration_seconds": duration,
                    })
                    succeeded += 1
                else:
                    failed += 1
                    logger.warning(f"      Failed: {mp3_path.name}")
            except Exception as e:
                failed += 1
                logger.error(f"      Error converting {mp3_path.name}: {e}")

    logger.info(f"      Converted: {succeeded} succeeded, {failed} failed")

    return results


def test_transcription_and_classification(opus_files: list[dict], worker: GPUWorker) -> list[dict]:
    """
    Test transcription and classification on converted files.

    Returns:
        List of dicts with full results
    """
    logger.info(f"[3/4] Transcribing {len(opus_files)} audio file(s)")
    logger.info("[4/4] Classifying transcripts")

    results = []

    for i, file_info in enumerate(opus_files, 1):
        opus_path = file_info["opus_path"]
        original_name = file_info["original_filename"]

        logger.info(f"      Processing [{i}/{len(opus_files)}]: {original_name}")

        try:
            # Transcribe
            transcript_result = worker.transcribe(opus_path)

            # Classify
            classification_result = worker.classify(transcript_result["text"])

            result = {
                "original_filename": original_name,
                "opus_path": opus_path,
                "duration_seconds": file_info.get("duration_seconds"),
                "transcript": {
                    "text": transcript_result["text"],
                    "language": transcript_result["language"],
                    "confidence": transcript_result["confidence"],
                },
                "classification": {
                    "flagged": classification_result["flagged"],
                    "score": classification_result["score"],
                    "category": classification_result["category"],
                },
            }
            results.append(result)

            # Log summary
            status = "FLAGGED" if classification_result["flagged"] else "OK"
            text_preview = transcript_result["text"][:80] + "..." if len(transcript_result["text"]) > 80 else transcript_result["text"]
            logger.info(f"        [{status}] {transcript_result['language']} | {text_preview}")

        except Exception as e:
            logger.error(f"        Error: {e}")
            results.append({
                "original_filename": original_name,
                "opus_path": opus_path,
                "error": str(e),
            })

    return results


def print_summary(results: list[dict]):
    """Print a summary of all results."""
    print("\n" + "=" * 80)
    print("PIPELINE TEST SUMMARY")
    print("=" * 80)

    total = len(results)
    errors = sum(1 for r in results if "error" in r)
    flagged = sum(1 for r in results if r.get("classification", {}).get("flagged"))

    print(f"\nTotal files processed: {total}")
    print(f"  Successful: {total - errors}")
    print(f"  Errors: {errors}")
    print(f"  Flagged: {flagged}")

    if flagged > 0:
        print("\nFlagged content:")
        for r in results:
            if r.get("classification", {}).get("flagged"):
                print(f"  - {r['original_filename']}")
                print(f"    Category: {r['classification']['category']}")
                print(f"    Text: {r['transcript']['text'][:100]}...")

    print("\nDetailed results:")
    for r in results:
        print(f"\n  {r['original_filename']}:")
        if "error" in r:
            print(f"    ERROR: {r['error']}")
        else:
            print(f"    Duration: {r.get('duration_seconds', 'N/A')}s")
            print(f"    Language: {r['transcript']['language']}")
            print(f"    Confidence: {r['transcript']['confidence']:.2f}")
            print(f"    Flagged: {r['classification']['flagged']}")
            print(f"    Transcript: {r['transcript']['text'][:100]}{'...' if len(r['transcript']['text']) > 100 else ''}")

    print("\n" + "=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Test the audio processing pipeline on a single archive"
    )
    parser.add_argument(
        "archive",
        type=Path,
        help="Path to .tar or .tar.gz archive containing MP3 files"
    )
    parser.add_argument(
        "--keep-files",
        action="store_true",
        help="Keep extracted/converted files after test (default: cleanup)"
    )
    parser.add_argument(
        "--skip-gpu",
        action="store_true",
        help="Skip transcription and classification (test extraction/conversion only)"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for converted files (default: temp dir)"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel ffmpeg workers for conversion (default: 4)"
    )

    args = parser.parse_args()

    # Validate archive exists
    if not args.archive.exists():
        logger.error(f"Archive not found: {args.archive}")
        sys.exit(1)

    # Set up directories
    if args.output_dir:
        output_dir = args.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        extract_dir = output_dir / "extracted"
        opus_dir = output_dir / "opus"
        temp_dir = None
    else:
        temp_dir = tempfile.mkdtemp(prefix="pipeline_test_")
        output_dir = Path(temp_dir)
        extract_dir = output_dir / "extracted"
        opus_dir = output_dir / "opus"

    extract_dir.mkdir(parents=True, exist_ok=True)
    opus_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Testing pipeline with archive: {args.archive}")
    logger.info(f"Working directory: {output_dir}")

    try:
        # Step 1: Extract
        mp3_files = test_extraction(args.archive, extract_dir)
        if not mp3_files:
            logger.error("No MP3 files found - exiting")
            sys.exit(1)

        # Step 2: Convert
        opus_files = test_conversion(mp3_files, opus_dir, args.workers)
        if not opus_files:
            logger.error("No files converted - exiting")
            sys.exit(1)

        # Steps 3-4: Transcribe and Classify
        if args.skip_gpu:
            logger.info("[3/4] Skipping transcription (--skip-gpu)")
            logger.info("[4/4] Skipping classification (--skip-gpu)")
            results = opus_files  # Just return conversion results
        else:
            logger.info("Initializing GPU worker (this may take a minute)...")
            worker = GPUWorker()
            worker.initialize_models()
            results = test_transcription_and_classification(opus_files, worker)

        # Print summary
        print_summary(results)

        logger.info("Pipeline test complete!")

    finally:
        # Cleanup
        if not args.keep_files and temp_dir:
            logger.info(f"Cleaning up: {temp_dir}")
            shutil.rmtree(temp_dir, ignore_errors=True)
        elif args.keep_files:
            logger.info(f"Files kept at: {output_dir}")


if __name__ == "__main__":
    main()
