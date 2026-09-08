"""FastAPI app entrypoint. Extraction is primarily a queue worker; the app
exists for /healthz and /docs (local dev convenience per the skeleton doc §2)."""
from fastapi import FastAPI

app = FastAPI(title="extraction", description="Shree — OCR + Map/GIS, M3/M4")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
