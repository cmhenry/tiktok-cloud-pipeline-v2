"""
Audio Processing Pipeline - GPU Worker

Handles transcription (WhisperX) and classification (CoPE-A with Gemma-2-9B)
for audio files. Runs on GPU VMs with L4 24GB VRAM.

S3 Flow:
1. Pop JSON job from queue:transcribe (batch_id, opus_path, original_filename)
2. Transcribe + classify audio
3. Insert to database (get audio_id)
4. Upload opus to S3 processed/{date}/{audio_id}.opus
5. Update DB with S3 path
6. Track batch progress with Redis atomic counter
7. Cleanup scratch when batch completes
"""

import json
import re
import traceback
from datetime import datetime
from pathlib import Path

import torch
import whisperx
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel

from .config import REDIS, PROCESSING, LOCAL
from .utils import setup_logger, get_redis_client
from .s3_utils import upload_opus, cleanup_scratch
from .db import (
    get_db_pool,
    insert_audio_file,
    insert_transcript,
    insert_classification,
    update_audio_status,
    update_audio_s3_path,
    update_audio_metadata,
)

logger = setup_logger("gpu_worker")


class GPUWorker:
    """
    GPU worker for transcription and classification.

    Loads WhisperX large-v2 for transcription and Gemma-2-9B with CoPE-A LoRA
    for content classification. Both models use optimizations to fit in 24GB VRAM.

    Handles S3 upload of processed files and batch completion tracking.
    """

    def __init__(self, redis_client=None):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.whisper_model = None
        self.cope_model = None
        self.cope_tokenizer = None
        self.policy_template = None
        self.redis_client = redis_client

        if self.device == "cpu":
            logger.warning("CUDA not available - running on CPU (slow)")

    def initialize_models(self):
        """Load models at startup. Must be called before processing."""
        logger.info("Initializing GPU worker models...")

        # Load CoPE-A policy template
        policy_path = PROCESSING["COPE_POLICY"]
        logger.info(f"Loading CoPE-A policy from {policy_path}")
        with open(policy_path, "r") as f:
            self.policy_template = f.read()

        # Initialize WhisperX
        # PyTorch 2.6+ defaults torch.load to weights_only=True, but pyannote's
        # VAD checkpoint contains omegaconf types not in the safe allowlist.
        # Temporarily patch torch.load to use weights_only=False for this load only.
        _original_torch_load = torch.load
        torch.load = lambda *args, **kwargs: _original_torch_load(
            *args, **{**kwargs, "weights_only": False}
        )
        try:
            logger.info(f"Loading WhisperX {PROCESSING['WHISPERX_MODEL']}...")
            self.whisper_model = whisperx.load_model(
                PROCESSING["WHISPERX_MODEL"],
                self.device,
                compute_type="float16" if self.device == "cuda" else "int8"
            )
        finally:
            torch.load = _original_torch_load

        # Log VRAM after WhisperX
        if self.device == "cuda":
            allocated = torch.cuda.memory_allocated() / 1024**3
            logger.info(f"WhisperX loaded. VRAM used: {allocated:.1f}GB")

        # Initialize Gemma + CoPE-A LoRA with 8-bit quantization
        logger.info(f"Loading {PROCESSING['COPE_MODEL']} with CoPE-A LoRA...")

        bnb_config = BitsAndBytesConfig(
            load_in_8bit=True,
            bnb_8bit_compute_dtype=torch.float16,
        )

        base_model = AutoModelForCausalLM.from_pretrained(
            PROCESSING["COPE_MODEL"],
            quantization_config=bnb_config,
            device_map="auto",
            torch_dtype=torch.float16,
        )

        # Load LoRA adapter
        adapter_path = str(PROCESSING["COPE_ADAPTER"])
        self.cope_model = PeftModel.from_pretrained(
            base_model,
            adapter_path,
        )
        self.cope_model.eval()

        self.cope_tokenizer = AutoTokenizer.from_pretrained(
            PROCESSING["COPE_MODEL"],
            padding_side="left",
        )

        # Ensure pad token is set
        if self.cope_tokenizer.pad_token is None:
            self.cope_tokenizer.pad_token = self.cope_tokenizer.eos_token

        # Log total VRAM usage
        if self.device == "cuda":
            allocated = torch.cuda.memory_allocated() / 1024**3
            reserved = torch.cuda.memory_reserved() / 1024**3
            logger.info(
                f"All models loaded. VRAM: {allocated:.1f}GB allocated, "
                f"{reserved:.1f}GB reserved"
            )

    def transcribe(self, audio_path: str) -> dict:
        """
        Transcribe a single audio file using WhisperX.

        Args:
            audio_path: Path to the audio file (opus format)

        Returns:
            Dict with keys: text, language, confidence
        """
        audio = whisperx.load_audio(audio_path)
        result = self.whisper_model.transcribe(audio, batch_size=16)

        # Extract text from segments
        if result.get("segments"):
            text = " ".join(seg["text"].strip() for seg in result["segments"])
            # Calculate average confidence if available
            confidences = [
                seg.get("avg_logprob", 0.0)
                for seg in result["segments"]
                if "avg_logprob" in seg
            ]
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
            # Convert log probability to a 0-1 score (approximate)
            confidence = min(1.0, max(0.0, 1.0 + avg_confidence / 5.0))
        else:
            text = ""
            confidence = 0.0

        return {
            "text": text,
            "language": result.get("language", "unknown"),
            "confidence": confidence,
        }

    def classify(self, transcript: str) -> dict:
        """
        Classify transcript for harmful content using CoPE-A with policy file.

        Uses the policy template loaded from tiktok_policy.txt which defines
        the classification criteria for reportable content (harassment, hate
        speech, violence, sexual exploitation).

        Args:
            transcript: Transcribed text to classify

        Returns:
            Dict with keys: flagged (bool), score (float), category (str or None)
        """
        if not transcript or not transcript.strip():
            return {"flagged": False, "score": 0.0, "category": None}

        # Format prompt using policy template with content placeholder
        prompt = self.policy_template.format(content_text=transcript)

        inputs = self.cope_tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=8192,  # CoPE-A supports 8K tokens
        ).to(self.cope_model.device)

        with torch.no_grad():
            outputs = self.cope_model.generate(
                **inputs,
                max_new_tokens=2,  # Binary output: "0" or "1"
                do_sample=False,
                pad_token_id=self.cope_tokenizer.pad_token_id,
            )

        # Decode only the new tokens (skip the input)
        response = self.cope_tokenizer.decode(
            outputs[0][inputs["input_ids"].shape[1]:],
            skip_special_tokens=True
        ).strip()

        return self._parse_classification_response(response)

    def _parse_classification_response(self, response: str) -> dict:
        """
        Parse CoPE-A binary response ("0" or "1").

        CoPE-A outputs:
        - "0": Content does not match any policy labels (safe)
        - "1": Content matches one or more policy labels (flagged)

        Args:
            response: Raw model output (should be "0" or "1")

        Returns:
            Parsed classification dict with flagged, score, and category
        """
        # Extract first digit from response
        digit_match = re.search(r'[01]', response)

        if not digit_match:
            logger.warning(f"Unexpected CoPE-A response: {response[:50]}")
            return {"flagged": False, "score": 0.0, "category": None}

        flagged = digit_match.group() == "1"

        return {
            "flagged": flagged,
            "score": 1.0 if flagged else 0.0,
            "category": "reportable_content" if flagged else None,
        }

    def process_item(self, item: dict) -> bool:
        """
        Process a single audio file: transcribe, classify, upload to S3, track batch.

        New S3 Flow:
        1. Transcribe + classify (unchanged)
        2. Insert to database (NEW - get audio_id)
        3. Upload opus to S3 (NEW)
        4. Update DB with S3 path (NEW)
        5. Track batch progress (NEW)

        Args:
            item: Dict with keys: batch_id, opus_path, original_filename

        Returns:
            True if processed successfully, False otherwise
        """
        batch_id = item["batch_id"]
        opus_path = Path(item["opus_path"])
        original_filename = item["original_filename"]

        audio_id = None  # Track for error handling

        try:
            # 1. Transcribe
            logger.debug(f"Transcribing: {opus_path.name}")
            transcript = self.transcribe(str(opus_path))

            # 2. Classify
            logger.debug(f"Classifying: {opus_path.name}")
            classification = self.classify(transcript["text"])

            # 3. Insert audio file record to database (NEW - get audio_id)
            file_size = opus_path.stat().st_size if opus_path.exists() else 0
            audio_id = insert_audio_file(
                original_filename=original_filename,
                opus_path=str(opus_path),  # Local scratch path (temporary)
                archive_source=batch_id,
                duration_seconds=None,  # Could extract from WhisperX if needed
                file_size_bytes=file_size,
            )

            # 3a. Store parquet metadata if present in job payload
            parquet_metadata = item.get("parquet_metadata", {})
            if parquet_metadata:
                update_audio_metadata(audio_id, parquet_metadata)

            # 4. Insert transcript
            insert_transcript(
                audio_id,
                transcript["text"],
                transcript["language"],
                transcript["confidence"],
            )

            # 5. Insert classification
            insert_classification(
                audio_id,
                classification["flagged"],
                classification["score"],
                classification["category"],
            )

            # 6. Update status
            status = "flagged" if classification["flagged"] else "transcribed"
            update_audio_status(audio_id, status)

            # 7. Upload opus to S3 (NEW)
            date_str = datetime.utcnow().strftime("%Y-%m-%d")
            s3_opus_key = upload_opus(opus_path, audio_id, date_str)

            # 8. Update DB with S3 path (NEW)
            update_audio_s3_path(audio_id, s3_opus_key)

            logger.debug(
                f"Processed {audio_id}: status={status}, "
                f"score={classification['score']:.2f}, s3={s3_opus_key}"
            )

            return True

        except Exception as e:
            logger.error(
                f"Failed processing {opus_path}: {e}\n{traceback.format_exc()}"
            )
            # Mark as failed in DB if we have an audio_id
            if audio_id is not None:
                try:
                    update_audio_status(audio_id, "failed")
                except Exception:
                    pass
            return False

        finally:
            # Always track batch progress, even on failure.
            # Without this, a single failed file permanently stalls the batch.
            self.track_batch_progress(batch_id)

    def track_batch_progress(self, batch_id: str):
        """
        Increment batch counter and trigger cleanup if complete.

        Uses Redis atomic INCR so only one worker will see processed == total.

        Args:
            batch_id: Batch identifier
        """
        if self.redis_client is None:
            logger.warning(f"No Redis client - skipping batch tracking for {batch_id}")
            return

        # Atomic increment - returns new value
        processed = self.redis_client.incr(f"batch:{batch_id}:processed")

        # Get total (set by unpack worker)
        total_raw = self.redis_client.get(f"batch:{batch_id}:total")
        if total_raw is None:
            logger.warning(f"Batch {batch_id}: no total key found, skipping completion check")
            return

        total = int(total_raw)

        logger.debug(f"Batch {batch_id}: {processed}/{total} processed")

        # Check if this worker completed the batch
        # Due to atomic INCR, only ONE worker will see processed == total
        if processed >= total:
            self.complete_batch(batch_id)

    def complete_batch(self, batch_id: str):
        """
        Called when all items in batch are processed.

        Cleans up scratch directory and Redis keys.

        Args:
            batch_id: Batch identifier
        """
        logger.info(f"Batch {batch_id} complete, cleaning up")

        # Clean up scratch directory (idempotent)
        cleanup_scratch(batch_id)

        # Expire Redis keys after 60s instead of immediate deletion.
        # This gives monitoring/test scripts time to observe completion.
        if self.redis_client:
            self.redis_client.expire(f"batch:{batch_id}:total", 60)
            self.redis_client.expire(f"batch:{batch_id}:processed", 60)
            self.redis_client.expire(f"batch:{batch_id}:s3_key", 60)

        logger.info(f"Batch {batch_id}: scratch cleaned, Redis keys expire in 60s")

    def process_batch(self, items: list[dict]) -> tuple[int, int]:
        """
        Process a batch of audio files.

        Args:
            items: List of items to process

        Returns:
            Tuple of (success_count, failure_count)
        """
        success = 0
        failed = 0

        for item in items:
            if self.process_item(item):
                success += 1
            else:
                failed += 1

        return success, failed

    def run(self):
        """Main loop - collect batches from Redis queue and process."""
        # Initialize redis client if not provided
        if self.redis_client is None:
            self.redis_client = get_redis_client()

        logger.info("GPU worker started (S3 mode), waiting for audio files...")

        while True:
            batch = []

            # Collect batch up to BATCH_SIZE
            # Use blocking pop for first item, then non-blocking for rest
            while len(batch) < PROCESSING["BATCH_SIZE"]:
                if not batch:
                    # Block waiting for first item
                    result = self.redis_client.brpop(
                        REDIS["QUEUES"]["TRANSCRIBE"],
                        timeout=30
                    )
                else:
                    # Non-blocking for subsequent items (timeout=0 doesn't block)
                    result = self.redis_client.brpop(
                        REDIS["QUEUES"]["TRANSCRIBE"],
                        timeout=1
                    )

                if result is None:
                    break  # Timeout, process what we have

                _, msg = result
                try:
                    item = json.loads(msg)
                    # Validate required fields
                    if "batch_id" not in item or "opus_path" not in item:
                        logger.error(f"Invalid job format, missing batch_id or opus_path: {msg[:100]}")
                        continue
                    batch.append(item)
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON in queue: {e}")

            if batch:
                logger.info(f"Processing batch of {len(batch)} files")
                success, failed = self.process_batch(batch)
                logger.info(
                    f"Batch complete: {success} succeeded, {failed} failed"
                )

                # Log VRAM periodically
                if self.device == "cuda":
                    allocated = torch.cuda.memory_allocated() / 1024**3
                    logger.debug(f"VRAM in use: {allocated:.1f}GB")


def main():
    """Entry point for GPU worker."""
    # Ensure scratch directory exists
    LOCAL["SCRATCH_ROOT"].mkdir(parents=True, exist_ok=True)

    # Initialize database pool
    get_db_pool()

    # Initialize Redis client
    redis_client = get_redis_client()

    # Create and run worker
    worker = GPUWorker(redis_client=redis_client)
    worker.initialize_models()
    worker.run()


if __name__ == "__main__":
    main()
