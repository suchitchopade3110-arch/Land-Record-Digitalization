"""FR-ING-01 — the second ingest entrypoint alongside `POST /documents`:
a watched filesystem drop folder (`DROP_FOLDER_PATH`, default
`/data/drop-folder`). Polls for new files rather than using inotify/watchdog
— simpler, dependency-light, and adequate for the volumes this pilot
targets (§07); swapping in an inotify-based watcher later is a driver
change, not a change to `_ingest_one`'s logic.

Batch metadata (FR-ING-05 — district is mandatory) has no HTTP form to
carry it here, so each dropped file `NAME.ext` must have a sidecar
`NAME.ext.meta.json` alongside it (`{"district": "...", "tehsil": "...",
...}`) — same rule as the HTTP endpoint, just a different transport for
the metadata. A file with no sidecar, an invalid one, or an unsupported
MIME is moved to `<DROP_FOLDER_PATH>/rejected/` (never silently dropped or
left to be retried forever) with a `.rejected.json` reason file next to it.
"""
from __future__ import annotations

import json
import mimetypes
import os
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from backend.domain.ingest import (
    MissingMandatoryBatchField,
    UnsupportedMediaType,
    get_or_create_batch,
    ingest_document,
)
from backend.models.base import engine_from_env, session_factory

_META_SUFFIX = ".meta.json"


def _reject(drop_folder: Path, path: Path, reason_code: str, detail: str) -> None:
    rejected_dir = drop_folder / "rejected"
    rejected_dir.mkdir(parents=True, exist_ok=True)
    dest = rejected_dir / path.name
    shutil.move(str(path), str(dest))
    (rejected_dir / f"{path.name}.rejected.json").write_text(
        json.dumps({"reason_code": reason_code, "detail": detail})
    )


def _ingest_one(session: Session, drop_folder: Path, path: Path) -> None:
    meta_path = path.with_name(path.name + _META_SUFFIX)
    if not meta_path.exists():
        _reject(drop_folder, path, "missing_batch_metadata", f"expected sidecar {meta_path.name}")
        return
    try:
        meta = json.loads(meta_path.read_text())
    except json.JSONDecodeError as e:
        _reject(drop_folder, path, "invalid_batch_metadata", str(e))
        return
    finally:
        meta_path.unlink(missing_ok=True)

    mime = meta.get("mime") or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    scanning_date = datetime.fromisoformat(meta["scanning_date"]) if meta.get("scanning_date") else None

    try:
        batch = get_or_create_batch(
            session,
            batch_id=meta.get("batch_id"),
            district=meta.get("district"),
            tehsil=meta.get("tehsil"), village=meta.get("village"), series=meta.get("series"),
            custodian=meta.get("custodian"), scanning_date=scanning_date,
        )
        with path.open("rb") as stream:
            ingest_document(session, batch=batch, stream=stream, mime=mime)
        session.commit()
    except MissingMandatoryBatchField as e:
        session.rollback()
        _reject(drop_folder, path, e.reason_code, str(e))
        return
    except UnsupportedMediaType as e:
        session.rollback()
        _reject(drop_folder, path, e.reason_code, str(e))
        return

    path.unlink(missing_ok=True)


def scan_once(drop_folder: Path | None = None, *, session_maker=None) -> int:
    """Process every file currently sitting directly in the drop folder
    (not in `rejected/`). Returns the count processed (accepted or
    rejected — both are terminal outcomes that remove the file from the
    folder, so this is safe to call again on the next poll tick without
    reprocessing anything already handled).
    """
    drop_folder = drop_folder or Path(os.environ.get("DROP_FOLDER_PATH", "/data/drop-folder"))
    drop_folder.mkdir(parents=True, exist_ok=True)
    session_maker = session_maker or session_factory(engine_from_env())

    candidates = [
        p for p in drop_folder.iterdir()
        if p.is_file() and not p.name.endswith(_META_SUFFIX) and p.parent == drop_folder
    ]
    with session_maker() as session:
        for path in candidates:
            _ingest_one(session, drop_folder, path)
    return len(candidates)


if __name__ == "__main__":  # pragma: no cover
    import time

    poll_seconds = float(os.environ.get("DROP_FOLDER_POLL_SECONDS", "5"))
    while True:
        scan_once()
        time.sleep(poll_seconds)
