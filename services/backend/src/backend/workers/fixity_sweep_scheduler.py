"""FR-ING-07 — runs `backend.domain.fixity.run_sweep` on a fixed interval
(`FIXITY_SWEEP_INTERVAL_SECONDS`, default one day). A separate process
from the request-serving API and the queue consumers, matching this
repo's convention that scheduled/batch work is its own worker, not a
side effect of a request handler.
"""
from __future__ import annotations

import os
import time

from backend.domain.fixity import run_sweep
from backend.models.base import engine_from_env, session_factory


def run_once() -> int:
    Session = session_factory(engine_from_env())
    with Session() as session:
        alerts = run_sweep(session)
        session.commit()
    return len(alerts)


if __name__ == "__main__":  # pragma: no cover
    interval = float(os.environ.get("FIXITY_SWEEP_INTERVAL_SECONDS", str(24 * 60 * 60)))
    while True:
        run_once()
        time.sleep(interval)
