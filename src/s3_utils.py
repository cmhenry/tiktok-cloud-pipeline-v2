"""
Audio Processing Pipeline - S3 Utilities Module

S3 client operations for archive upload/download and processed file storage.
Configured for OpenStack Swift/S3-compatible endpoints with S3v4 signature.
"""

import shutil
from pathlib import Path
from typing import Optional

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from .config import S3, LOCAL
from .utils import setup_logger

logger = setup_logger("s3_utils")

# Multipart upload threshold (100MB)
MULTIPART_THRESHOLD = 100 * 1024 * 1024

_s3_client = None


def get_s3_client():
    """
    Get S3 client configured for OpenStack Swift/S3-compatible endpoint.

    Uses S3v4 signature (required for Swift compatibility).
    Client is cached for reuse across calls.

    Returns:
        boto3 S3 client instance

    Raises:
        ValueError: If required S3 configuration is missing
    """
    global _s3_client

    if _s3_client is not None:
        return _s3_client

    # Validate required config
    if not S3["ENDPOINT"]:
        raise ValueError("S3_ENDPOINT environment variable not set")
    if not S3["ACCESS_KEY"]:
        raise ValueError("S3_ACCESS_KEY environment variable not set")
    if not S3["SECRET_KEY"]:
        raise ValueError("S3_SECRET_KEY environment variable not set")

    _s3_client = boto3.client(
        "s3",
        endpoint_url=S3["ENDPOINT"],
        aws_access_key_id=S3["ACCESS_KEY"],
        aws_secret_access_key=S3["SECRET_KEY"],
        config=BotoConfig(
            signature_version="s3v4",
            s3={
                "addressing_style": "path",
                "payload_signing_enabled": False,  # Required for OpenStack Swift
            },
        ),
    )

    logger.debug(f"S3 client initialized for endpoint: {S3['ENDPOINT']}")
    return _s3_client


def upload_archive(local_path: Path, batch_id: str) -> str:
    """
    Upload tar archive to S3.

    Uses multipart upload for files larger than 100MB for reliability
    and better performance on large files.

    Args:
        local_path: Path to local tar file
        batch_id: Unique batch identifier

    Returns:
        S3 key: "archives/{batch_id}.tar"

    Raises:
        FileNotFoundError: If local file doesn't exist
        ClientError: If S3 upload fails
    """
    local_path = Path(local_path)
    if not local_path.exists():
        raise FileNotFoundError(f"Archive not found: {local_path}")

    s3_key = f"{S3['ARCHIVE_PREFIX']}{batch_id}.tar"
    file_size = local_path.stat().st_size
    client = get_s3_client()

    logger.info(
        f"Uploading archive {local_path.name} ({file_size / 1024 / 1024:.1f}MB) "
        f"to s3://{S3['BUCKET']}/{s3_key}"
    )

    if file_size > MULTIPART_THRESHOLD:
        # Use multipart upload for large files
        _multipart_upload(client, local_path, s3_key, file_size)
    else:
        # Simple upload using put_object (better Swift/radosgw compatibility)
        with open(local_path, "rb") as f:
            client.put_object(Bucket=S3["BUCKET"], Key=s3_key, Body=f)

    logger.info(f"Upload complete: {s3_key}")
    return s3_key


def _multipart_upload(client, local_path: Path, s3_key: str, file_size: int):
    """
    Perform multipart upload with progress logging.

    Args:
        client: boto3 S3 client
        local_path: Path to local file
        s3_key: Destination S3 key
        file_size: Total file size in bytes
    """
    from boto3.s3.transfer import TransferConfig

    # Configure multipart: 50MB chunks, 4 concurrent transfers
    config = TransferConfig(
        multipart_threshold=MULTIPART_THRESHOLD,
        multipart_chunksize=50 * 1024 * 1024,
        max_concurrency=4,
        use_threads=True,
    )

    # Progress callback for logging
    uploaded = [0]  # Use list to allow mutation in closure

    def progress_callback(bytes_transferred):
        uploaded[0] += bytes_transferred
        percent = (uploaded[0] / file_size) * 100
        if percent % 25 < 1:  # Log at ~25% intervals
            logger.debug(f"Upload progress: {percent:.0f}%")

    client.upload_file(
        str(local_path),
        S3["BUCKET"],
        s3_key,
        Config=config,
        Callback=progress_callback,
    )


def download_archive(s3_key: str, batch_id: str) -> Path:
    """
    Download archive from S3 to local scratch directory.

    Creates the scratch directory if it doesn't exist.

    Args:
        s3_key: S3 key to download
        batch_id: Batch identifier (determines scratch subdirectory)

    Returns:
        Local path: {SCRATCH_ROOT}/{batch_id}/archive.tar

    Raises:
        ClientError: If S3 download fails (e.g., key not found)
    """
    scratch_dir = LOCAL["SCRATCH_ROOT"] / batch_id
    scratch_dir.mkdir(parents=True, exist_ok=True)

    local_path = scratch_dir / "archive.tar"
    client = get_s3_client()

    logger.info(
        f"Downloading s3://{S3['BUCKET']}/{s3_key} to {local_path}"
    )

    client.download_file(S3["BUCKET"], s3_key, str(local_path))

    file_size = local_path.stat().st_size
    logger.info(
        f"Download complete: {local_path} ({file_size / 1024 / 1024:.1f}MB)"
    )

    return local_path


def upload_opus(local_path: Path, audio_id: int, date_str: str) -> str:
    """
    Upload processed opus file to S3 for long-term storage.

    Args:
        local_path: Path to local opus file
        audio_id: Database ID of audio file
        date_str: Date string in YYYY-MM-DD format

    Returns:
        S3 key: "processed/{date_str}/{audio_id}.opus"

    Raises:
        FileNotFoundError: If local file doesn't exist
        ClientError: If S3 upload fails
    """
    local_path = Path(local_path)
    if not local_path.exists():
        raise FileNotFoundError(f"Opus file not found: {local_path}")

    s3_key = f"{S3['PROCESSED_PREFIX']}{date_str}/{audio_id}.opus"
    client = get_s3_client()

    logger.debug(f"Uploading opus {audio_id} to s3://{S3['BUCKET']}/{s3_key}")

    # Use put_object for Swift/radosgw compatibility
    with open(local_path, "rb") as f:
        client.put_object(Bucket=S3["BUCKET"], Key=s3_key, Body=f)

    return s3_key


def delete_archive(s3_key: str) -> bool:
    """
    Delete archive from S3 after successful batch processing.

    Args:
        s3_key: S3 key of archive to delete

    Returns:
        True if deletion successful or key didn't exist, False on error
    """
    client = get_s3_client()

    try:
        client.delete_object(Bucket=S3["BUCKET"], Key=s3_key)
        logger.info(f"Deleted archive: s3://{S3['BUCKET']}/{s3_key}")
        return True
    except ClientError as e:
        logger.error(f"Failed to delete {s3_key}: {e}")
        return False


def cleanup_scratch(batch_id: str):
    """
    Remove batch scratch directory after processing complete.

    Idempotent: no error if directory doesn't exist.
    Removes the entire {SCRATCH_ROOT}/{batch_id}/ directory tree.

    Args:
        batch_id: Batch identifier
    """
    scratch_dir = LOCAL["SCRATCH_ROOT"] / batch_id

    if not scratch_dir.exists():
        logger.debug(f"Scratch directory already cleaned: {scratch_dir}")
        return

    try:
        shutil.rmtree(scratch_dir)
        logger.info(f"Cleaned up scratch directory: {scratch_dir}")
    except Exception as e:
        logger.warning(f"Failed to cleanup scratch {scratch_dir}: {e}")


def check_s3_connection() -> bool:
    """
    Verify S3 connectivity by checking bucket access.

    Returns:
        True if connection successful, False otherwise
    """
    try:
        client = get_s3_client()
        bucket = S3["BUCKET"]
        endpoint = S3["ENDPOINT"]

        logger.debug(f"Testing S3: endpoint={endpoint}, bucket={bucket}")
        client.head_bucket(Bucket=bucket)
        logger.info(f"S3 connection verified: bucket '{bucket}' accessible")
        return True
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_msg = e.response.get("Error", {}).get("Message", str(e))

        if error_code in ("404", "NoSuchBucket"):
            logger.error(
                f"S3 bucket not found: '{S3['BUCKET']}' at {S3['ENDPOINT']}. "
                f"Check S3_BUCKET env var matches your bucket name."
            )
        elif error_code in ("403", "AccessDenied"):
            logger.error(
                f"S3 access denied to bucket '{S3['BUCKET']}'. "
                f"Check EC2 credentials have bucket access."
            )
        elif error_code == "InvalidAccessKeyId":
            logger.error(
                f"Invalid S3 access key. Regenerate EC2 credentials with: "
                f"openstack ec2 credentials create"
            )
        else:
            logger.error(f"S3 connection failed [{error_code}]: {error_msg}")

        return False
    except Exception as e:
        logger.error(f"S3 connection failed: {e}")
        return False


def get_archive_size(s3_key: str) -> Optional[int]:
    """
    Get the size of an archive in S3.

    Args:
        s3_key: S3 key of the archive

    Returns:
        File size in bytes, or None if not found
    """
    try:
        client = get_s3_client()
        response = client.head_object(Bucket=S3["BUCKET"], Key=s3_key)
        return response["ContentLength"]
    except ClientError:
        return None
