"""Writer-style clustering -> writer_cluster_id (FR-TRI-11).
Feeds Shree's per-writer adapter and this service's own calibration stratum.

SOURCE-OF-TRUTH CONTRACTS:
- contracts/schemas/page.schema.json:
  - writer_cluster_id: string (format: uuid) | null
- services/backend/src/backend/models/entities.py:
  - WriterCluster table: id (uuid), style_embedding_centroid, page_count, adapter_ref.
    "PRD §11 Q10: an unnamed style grouping — deliberately no name/officer linkage column exists here."

STRICT ANONYMITY AND SECURITY:
- NEVER map a writer cluster to a person or officer identity.
- NEVER expose personal identity metadata.
- NEVER create a person profile or biographical database.
- writer_cluster_id is strictly an opaque, anonymous UUID identifier.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True)
class WriterClusterResult:
    """Structured result of anonymous writer-style clustering (FR-TRI-11)."""

    writer_cluster_id: str | None
    style_embedding: list[float] | None = None
    model_version: str = "triage_classifier_v1"

    def __post_init__(self) -> None:
        if self.writer_cluster_id is not None:
            if not isinstance(self.writer_cluster_id, str):
                raise ValueError(
                    f"writer_cluster_id must be a UUID string or None, got {type(self.writer_cluster_id)}"
                )
            try:
                uuid.UUID(self.writer_cluster_id)
            except (ValueError, AttributeError) as exc:
                raise ValueError(
                    f"writer_cluster_id must be a valid UUID string per contract, got {self.writer_cluster_id!r}"
                ) from exc


class WriterClusterer(Protocol):
    """Protocol for anonymous writer-style clustering implementations."""

    def cluster(self, page_image: bytes) -> WriterClusterResult:
        """Assign an anonymous writer_cluster_id to the page image."""
        ...


@dataclass(frozen=True)
class DeterministicWriterClusterer:
    """Deterministic test double for anonymous writer clustering.

    COMPATIBLE IMPLEMENTATION POLICY / TEST-ONLY DOUBLE:
    Generates deterministic, anonymous UUIDv5 identifiers from visual/byte features.
    Zero personal identity or officer metadata is used or produced.
    """

    fixed_cluster_id: str | None = None
    cluster_fn: Callable[[bytes], str | None] | None = None
    model_version: str = "triage_classifier_v1"

    def cluster(self, page_image: bytes) -> WriterClusterResult:
        if self.fixed_cluster_id is not None:
            return WriterClusterResult(
                writer_cluster_id=self.fixed_cluster_id,
                model_version=self.model_version,
            )

        if self.cluster_fn is not None:
            cluster_id = self.cluster_fn(page_image)
            return WriterClusterResult(
                writer_cluster_id=cluster_id,
                model_version=self.model_version,
            )

        # Anonymous deterministic UUIDv5 generated from content hash
        digest = hashlib.sha256(page_image if page_image else b"empty_page").hexdigest()
        cluster_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"anonymous-style-cluster:{digest}"))

        return WriterClusterResult(
            writer_cluster_id=cluster_uuid,
            model_version=self.model_version,
        )


def cluster_writer(
    page_image: bytes,
    clusterer: WriterClusterer | None = None,
) -> str | None:
    """Domain entry point for writer-style clustering (FR-TRI-11).

    Returns a canonical UUID string or None.
    """
    active_clusterer = clusterer or DeterministicWriterClusterer()
    res = active_clusterer.cluster(page_image)
    return res.writer_cluster_id
