"""Configuration for dedicated Baidu Unlimited-OCR inference runtime."""

import os
from pydantic import BaseModel


class OCRServiceConfig(BaseModel):
    model_name_or_path: str = os.getenv("BAIDU_OCR_MODEL_PATH", "baidu/Unlimited-OCR")
    host: str = os.getenv("OCR_HOST", "0.0.0.0")
    port: int = int(os.getenv("OCR_PORT", "8080"))
    device: str = os.getenv("OCR_DEVICE", "cuda" if os.getenv("CUDA_VISIBLE_DEVICES") else "auto")
    use_half_precision: bool = os.getenv("OCR_HALF_PRECISION", "true").lower() == "true"
    require_gpu: bool = os.getenv("OCR_REQUIRE_GPU", "false").lower() == "true"


config = OCRServiceConfig()
