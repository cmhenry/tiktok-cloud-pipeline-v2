"""
Audio Processing Pipeline - GPU Worker

Handles transcription (WhisperX) and classification (CoPE-A with Gemma-2-9B)
for audio files. Runs on GPU VMs with L4 24GB VRAM.
"""

import json
import re
import traceback
from typing import Optional

import torch
import whisperx
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel

from config import REDIS, PROCESSING, ensure_paths_exist
from utils import setup_logger, get_redis_client
from db import (
    get_db_pool,
    insert_transcript,
    insert_classification,
    update_audio_status,
)

logger = setup_logger("gpu_worker")


class GPUWorker:
    """
    GPU worker for transcription and classification.

    Loads WhisperX large-v2 for transcription and Gemma-2-9B with CoPE-A LoRA
    for content classification. Both models use optimizations to fit in 24GB VRAM.
    """

    def __init__(self):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.whisper_model = None
        self.cope_model = None
        self.cope_tokenizer = None

        if self.device == "cpu":
            logger.warning("CUDA not available - running on CPU (slow)")

    def initialize_models(self):
        """Load models at startup. Must be called before processing."""
        logger.info("Initializing GPU worker models...")

        # Initialize WhisperX
        logger.info(f"Loading WhisperX {PROCESSING['WHISPERX_MODEL']}...")
        self.whisper_model = whisperx.load_model(
            PROCESSING["WHISPERX_MODEL"],
            self.device,
            compute_type="float16" if self.device == "cuda" else "int8"
        )

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
        Classify transcript for harmful content using CoPE-A.

        Args:
            transcript: Transcribed text to classify

        Returns:
            Dict with keys: flagged (bool), score (float), category (str or None)
        """
        if not transcript or not transcript.strip():
            return {"flagged": False, "score": 0.0, "category": None}

        # CoPE-A prompt format
        prompt = f"""Analyze this transcript for harmful content including hate speech, violence, harassment, self-harm, illegal activities, or exploitation.

Transcript: "{transcript}"

Respond with JSON only: {{"flagged": true/false, "score": 0.0-1.0, "category": "category or null"}}"""

        inputs = self.cope_tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=2048,
        ).to(self.cope_model.device)

        with torch.no_grad():
            outputs = self.cope_model.generate(
                **inputs,
                max_new_tokens=100,
                do_sample=False,
                pad_token_id=self.cope_tokenizer.pad_token_id,
            )

        # Decode only the new tokens (skip the input)
        response = self.cope_tokenizer.decode(
            outputs[0][inputs["input_ids"].shape[1]:],
            skip_special_tokens=True
        )

        return self._parse_classification_response(response)

    def _parse_classification_response(self, response: str) -> dict:
        """
        Parse CoPE-A JSON response, handling malformed output gracefully.

        Args:
            response: Raw model output

        Returns:
            Parsed classification dict
        """
        default_result = {"flagged": False, "score": 0.0, "category": None}

        try:
            # Try to find JSON in response
            json_match = re.search(r'\{[^}]+\}', response)
            if not json_match:
                logger.warning(f"No JSON found in response: {response[:100]}")
                return default_result

            json_str = json_match.group()
            data = json.loads(json_str)

            # Validate and normalize fields
            flagged = bool(data.get("flagged", False))
            score = float(data.get("score", 0.0))
            score = min(1.0, max(0.0, score))  # Clamp to 0-1
            category = data.get("category")

            # Handle various null representations
            if category in (None, "null", "None", ""):
                category = None

            return {
                "flagged": flagged,
                "score": score,
                "category": category,
            }

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON: {e}, response: {response[:100]}")
            return default_result
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid values in response: {e}")
            return default_result

    def process_item(self, item: dict) -> bool:
        """
        Process a single audio file: transcribe, classify, and update DB.

        Args:
            item: Dict with keys: audio_id, opus_path, original_filename

        Returns:
            True if processed successfully, False otherwise
        """
        audio_id = item["audio_id"]
        opus_path = item["opus_path"]

        try:
            # Transcribe
            logger.debug(f"Transcribing {audio_id}: {opus_path}")
            transcript = self.transcribe(opus_path)

            insert_transcript(
                audio_id,
                transcript["text"],
                transcript["language"],
                transcript["confidence"],
            )

            # Classify
            logger.debug(f"Classifying {audio_id}")
            classification = self.classify(transcript["text"])

            insert_classification(
                audio_id,
                classification["flagged"],
                classification["score"],
                classification["category"],
            )

            # Update status
            status = "flagged" if classification["flagged"] else "transcribed"
            update_audio_status(audio_id, status)

            logger.debug(
                f"Processed {audio_id}: status={status}, "
                f"score={classification['score']:.2f}"
            )
            return True

        except Exception as e:
            logger.error(
                f"Failed processing {audio_id}: {e}\n{traceback.format_exc()}"
            )
            try:
                update_audio_status(audio_id, "failed")
            except Exception:
                pass
            return False

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
        redis_client = get_redis_client()
        logger.info("GPU worker started, waiting for audio files...")

        while True:
            batch = []

            # Collect batch up to BATCH_SIZE
            # Use blocking pop for first item, then non-blocking for rest
            while len(batch) < PROCESSING["BATCH_SIZE"]:
                if not batch:
                    # Block waiting for first item
                    result = redis_client.brpop(
                        REDIS["QUEUES"]["TRANSCRIBE"],
                        timeout=30
                    )
                else:
                    # Non-blocking for subsequent items (timeout=0 doesn't block)
                    result = redis_client.brpop(
                        REDIS["QUEUES"]["TRANSCRIBE"],
                        timeout=1
                    )

                if result is None:
                    break  # Timeout, process what we have

                _, msg = result
                try:
                    item = json.loads(msg)
                    batch.append(item)
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid message in queue: {e}")

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
    ensure_paths_exist()

    # Initialize database pool
    get_db_pool()

    worker = GPUWorker()
    worker.initialize_models()
    worker.run()


if __name__ == "__main__":
    main()
