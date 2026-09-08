"""FastAPI app entrypoint."""
from fastapi import FastAPI

from modelwork.api import model_registry

app = FastAPI(title="modelwork", description="Tharun B L — triage classifiers, confidence/novelty, learning loop")

app.include_router(model_registry.router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
