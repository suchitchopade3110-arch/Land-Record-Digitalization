"""FastAPI app entrypoint."""
from fastapi import FastAPI

app = FastAPI(title="validation", description="Shruthi — Validation + Entity Resolution, M6/M7")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
