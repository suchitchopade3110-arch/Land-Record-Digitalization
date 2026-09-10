"""FastAPI REST Service Entrypoint for Baidu Unlimited-OCR Inference Runtime."""

from __future__ import annotations

import base64
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .config import config
from .model_runner import runner

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ocr_service")

app = FastAPI(
    title="Baidu Unlimited-OCR Dedicated Inference Service",
    version="0.1.0",
    description="Dedicated GPU sidecar service running official `baidu/Unlimited-OCR` model.",
)


class OCRRequest(BaseModel):
    image: str = Field(..., description="Base64 encoded raw image string")
    page_id: str = Field(default="page-001", description="Page identifier for provenance tracking")


@app.get("/health")
def health_check() -> dict[str, Any]:
    """Returns runtime health status, GPU availability, and model status."""
    cuda_available = False
    device_name = "N/A"
    try:
        import torch
        cuda_available = torch.cuda.is_available()
        if cuda_available:
            device_name = torch.cuda.get_device_name(0)
    except ImportError:
        pass

    return {
        "status": "healthy",
        "model": config.model_name_or_path,
        "is_model_loaded": runner.is_loaded,
        "cuda_available": cuda_available,
        "device_name": device_name,
        "device_mode": runner.device,
    }


@app.post("/v1/ocr")
def process_ocr(req: OCRRequest) -> dict[str, Any]:
    """Processes base64 encoded image through Baidu Unlimited-OCR model."""
    if not req.image:
        raise HTTPException(status_code=400, detail="Missing required 'image' base64 payload")

    try:
        raw_bytes = base64.b64decode(req.image)
    except Exception as err:
        raise HTTPException(status_code=400, detail=f"Invalid base64 encoding: {err}") from err

    try:
        result = runner.run_inference(image_bytes=raw_bytes, page_id=req.page_id)
        return result
    except RuntimeError as err:
        logger.error("OCR model execution runtime failure: %s", err)
        raise HTTPException(status_code=500, detail=str(err)) from err
    except Exception as err:
        logger.error("Unexpected OCR processing error: %s", err)
        raise HTTPException(status_code=500, detail=f"Internal OCR Service Error: {err}") from err


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ocr_service.app:app", host=config.host, port=config.port, reload=False)
