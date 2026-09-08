"""FastAPI app entrypoint. Mounts every router this service owns and wires
the observability lib. TODO: add a router here (and a matching row in
gateway/route_registry.yaml) for every new endpoint — CI fails the build on
an undocumented path.
"""
from fastapi import FastAPI

from backend.api import closed_sets, config_service, conflicts, dashboard, documents, review_tasks

app = FastAPI(title="backend", description="Suchit — API, Storage, Queue, M1/M2-routing/M9-M10-M12-M15")

app.include_router(documents.router)
app.include_router(config_service.router)
app.include_router(closed_sets.router)
app.include_router(review_tasks.router)
app.include_router(conflicts.router)
app.include_router(dashboard.router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
