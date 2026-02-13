#!/usr/bin/env python3
"""
Clear all data from the audio_pipeline S3 bucket.

Deletes every object except the `archives/` and `processed/` folder markers
so the bucket structure is preserved for future pipeline runs.

Usage:
    python -m scripts.clear_bucket          # dry-run (default)
    python -m scripts.clear_bucket --confirm # actually delete
"""

import argparse
import sys
from pathlib import Path

# Allow running as `python -m scripts.clear_bucket` from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import S3
from src.s3_utils import get_s3_client, get_s3_list_client

PRESERVE_KEYS = {"archives/", "processed/"}


def list_all_objects(client, bucket):
    """Paginate through every object in the bucket."""
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            yield obj["Key"]


def main():
    parser = argparse.ArgumentParser(description="Clear the audio_pipeline S3 bucket.")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually delete objects. Without this flag, runs in dry-run mode.",
    )
    args = parser.parse_args()

    bucket = S3["BUCKET"]
    list_client = get_s3_list_client()
    delete_client = get_s3_client()

    keys_to_delete = [
        key for key in list_all_objects(list_client, bucket)
        if key not in PRESERVE_KEYS
    ]

    if not keys_to_delete:
        print(f"Bucket '{bucket}' is already empty (nothing to delete).")
        return

    print(f"Found {len(keys_to_delete)} object(s) to delete in '{bucket}':")
    for key in keys_to_delete:
        print(f"  {key}")

    if not args.confirm:
        print("\nDry run — no objects were deleted. Re-run with --confirm to delete.")
        return

    # S3 delete_objects accepts up to 1000 keys per request
    deleted = 0
    for i in range(0, len(keys_to_delete), 1000):
        batch = keys_to_delete[i : i + 1000]
        resp = delete_client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
        )
        errors = resp.get("Errors", [])
        if errors:
            for err in errors:
                print(f"  ERROR deleting {err['Key']}: {err['Message']}", file=sys.stderr)
        deleted += len(batch) - len(errors)

    print(f"\nDeleted {deleted} object(s). Preserved folder markers: {PRESERVE_KEYS}")


if __name__ == "__main__":
    main()
