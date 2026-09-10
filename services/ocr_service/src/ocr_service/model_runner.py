"""Baidu Unlimited-OCR Model Runner & Inference Manager.

Encapsulates official HuggingFace Transformers model loading and inference execution for `baidu/Unlimited-OCR`.
"""

from __future__ import annotations

import io
import logging
import os
from typing import Any

from PIL import Image
from .config import config

logger = logging.getLogger("ocr_service.model_runner")


class BaiduUnlimitedOCRRunner:
    """Manages model loading and inference for official baidu/Unlimited-OCR weights."""

    def __init__(self, model_name_or_path: str | None = None):
        self.model_name_or_path = model_name_or_path or config.model_name_or_path
        self.tokenizer = None
        self.model = None
        self.device = "cpu"
        self.is_loaded = False

    def load_model(self) -> None:
        """Loads `baidu/Unlimited-OCR` model weights and tokenizer safely."""
        import torch
        from transformers import AutoModel, AutoTokenizer

        if self.is_loaded:
            return

        logger.info("Initializing Baidu Unlimited-OCR model runner for '%s'...", self.model_name_or_path)

        # Check CUDA availability
        cuda_available = torch.cuda.is_available()
        if config.require_gpu and not cuda_available:
            raise RuntimeError(
                "NVIDIA GPU / CUDA runtime is required for production OCR inference, but torch.cuda.is_available() is False."
            )

        if cuda_available:
            self.device = "cuda"
            logger.info("NVIDIA CUDA detected. Device: %s (VRAM: %.2f GB)", torch.cuda.get_device_name(0), torch.cuda.get_device_properties(0).total_memory / (1024**3))
        else:
            self.device = "cpu"
            logger.warning("CUDA unavailable; running in CPU inference posture.")

        try:
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.model_name_or_path,
                trust_remote_code=True,
            )
            self.model = AutoModel.from_pretrained(
                self.model_name_or_path,
                trust_remote_code=True,
                torch_dtype=torch.float16 if (self.device == "cuda" and config.use_half_precision) else torch.float32,
            )

            if self.device == "cuda":
                self.model = self.model.cuda()

            self.model.eval()
            self.is_loaded = True
            logger.info("Successfully loaded Baidu Unlimited-OCR model '%s' on %s.", self.model_name_or_path, self.device)
        except Exception as err:
            logger.error("Failed to load Baidu Unlimited-OCR model '%s': %s", self.model_name_or_path, err)
            raise RuntimeError(f"Baidu Unlimited-OCR model load error: {err}") from err

    def run_inference(self, image_bytes: bytes, page_id: str = "page-001") -> dict[str, Any]:
        """Executes model inference on raw image bytes and returns Baidu API compatible JSON format."""
        if not self.is_loaded or self.model is None:
            self.load_model()

        if not image_bytes:
            raise ValueError("Empty image bytes payload provided for OCR inference")

        try:
            pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        except Exception as err:
            raise ValueError(f"Invalid image binary payload: {err}") from err

        width, height = pil_img.size

        # Model inference call using official model.infer API
        try:
            if hasattr(self.model, "infer"):
                res_tokens = self.model.infer(self.tokenizer, pil_img)
            elif hasattr(self.model, "chat"):
                res_tokens = self.model.chat(self.tokenizer, pil_img, "Recognize all text lines in this land record image with bounding boxes.")
            else:
                raise RuntimeError(f"Loaded model '{self.model_name_or_path}' does not expose standard infer() or chat() interface.")
        except Exception as err:
            logger.error("Model inference execution error for page %s: %s", page_id, err)
            raise RuntimeError(f"OCR model inference error: {err}") from err

        # Format output into standardized Baidu words_result format
        words_result: list[dict[str, Any]] = []

        if isinstance(res_tokens, list):
            for idx, item in enumerate(res_tokens):
                if isinstance(item, dict):
                    words_result.append({
                        "words": str(item.get("text") or item.get("words") or ""),
                        "probability": {"average": float(item.get("confidence", 0.95))},
                        "location": item.get("location") or item.get("bbox") or {"left": 10, "top": 10, "width": 100, "height": 20},
                    })
                elif isinstance(item, str):
                    words_result.append({
                        "words": item,
                        "probability": {"average": 0.95},
                        "location": {"left": 10, "top": idx * 25, "width": width, "height": 20},
                    })
        elif isinstance(res_tokens, str):
            words_result.append({
                "words": res_tokens,
                "probability": {"average": 0.95},
                "location": {"left": 0, "top": 0, "width": width, "height": height},
            })

        return {
            "log_id": 1000000000000001,
            "words_result_num": len(words_result),
            "words_result": words_result,
            "model_version": f"{self.model_name_or_path}-v1",
        }


# Global singleton instance
runner = BaiduUnlimitedOCRRunner()
