#!/usr/bin/env python3
"""`make seed` — loads one fake batch/document/page/extraction/review-task
so the API and dashboard have something to show in a fresh local
environment. Not test data (see tests/invariant, tests/e2e for that) —
this is for a human clicking through `GET /review-tasks` on a machine that
just ran `make up && make migrate` and has nothing in it yet.
"""
from __future__ import annotations

from backend.domain.decision import route
from backend.domain.triage import route_page
from backend.models.base import engine_from_env, session_factory
from backend.models.entities import Batch, Extraction, Page, SourceDocument


def main() -> None:
    engine = engine_from_env()
    Session = session_factory(engine)

    with Session() as session:
        batch = Batch(district="sitapur", tehsil="sitapur-sadar", village="rampur", series="jamabandi-1431f")
        session.add(batch)
        session.flush()

        doc = SourceDocument(
            batch_id=batch.id,
            sha256="0" * 64,
            storage_uri="sha256/00/00/" + "0" * 64,
            mime="image/tiff",
            page_count=1,
        )
        session.add(doc)
        session.flush()

        page = Page(document_id=doc.id, index=0, doc_type="jamabandi", page_role="text", legibility_band="good")
        session.add(page)
        session.flush()

        envelope, queued_to = route_page(
            session, document_id=doc.id, page_id=page.id, doc_type="jamabandi", page_role="text",
            config_version="seed-v1",
        )

        extraction = Extraction(
            page_id=page.id,
            field_name="owner_name",
            raw_value="राम प्रसाद",
            canonical_value="Ram Prasad",
            engine="printed_ocr",
            model_version=envelope.model_versions["printed_ocr"],
            config_version="seed-v1",
            token_confidence=0.6,
            calibrated_confidence=0.6,
            routing_outcome="review",  # low confidence — lands in the review queue, visible via GET /review-tasks
        )
        session.add(extraction)
        session.flush()

        result = route(session, extraction.id)
        session.commit()

        print(f"Seeded batch={batch.id} document={doc.id} page={page.id}")
        print(f"Pinned WorkEnvelope {envelope.envelope_id}, queued to {queued_to}")
        print(f"Extraction {extraction.id} routed: {result}")
        print("GET /review-tasks should now show one open task.")


if __name__ == "__main__":
    main()
