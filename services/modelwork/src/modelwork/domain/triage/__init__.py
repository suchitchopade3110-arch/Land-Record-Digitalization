"""Triage domain classifiers and scoring engine (FR-TRI-01..04, FR-TRI-10..11, FR-CNF-14).
M2 ML components owned by Model Work, distinct from Backend triage routing infrastructure.
"""
from __future__ import annotations

from modelwork.domain.triage.legibility import (
    CANONICAL_BREACH_REASON,
    ILLEGIBLE_REGION_REASON,
    VALID_LEGIBILITY_BANDS,
    DeterministicLegibilityScorer,
    LegibilityBand,
    LegibilityPolicy,
    LegibilityResult,
    LegibilityScorer,
    score_legibility,
)
from modelwork.domain.triage.novelty_prescore import compute_triage_novelty_prescore
from modelwork.domain.triage.script_doctype import (
    KNOWN_LANGUAGES,
    KNOWN_SCRIPTS,
    VALID_DOC_TYPES,
    VALID_PAGE_ROLES,
    DeterministicScriptDoctypeClassifier,
    DocTypeClassifier,
    LanguageClassifier,
    PageRoleClassifier,
    ScriptClassifier,
    ScriptDoctypeResult,
    TriageClassifier,
    classify,
)
from modelwork.domain.triage.writer_clustering import (
    DeterministicWriterClusterer,
    WriterClusterer,
    WriterClusterResult,
    cluster_writer,
)

__all__ = [
    "CANONICAL_BREACH_REASON",
    "ILLEGIBLE_REGION_REASON",
    "KNOWN_LANGUAGES",
    "KNOWN_SCRIPTS",
    "VALID_DOC_TYPES",
    "VALID_LEGIBILITY_BANDS",
    "VALID_PAGE_ROLES",
    "DeterministicLegibilityScorer",
    "DeterministicScriptDoctypeClassifier",
    "DeterministicWriterClusterer",
    "DocTypeClassifier",
    "LanguageClassifier",
    "LegibilityBand",
    "LegibilityPolicy",
    "LegibilityResult",
    "LegibilityScorer",
    "PageRoleClassifier",
    "ScriptClassifier",
    "ScriptDoctypeResult",
    "TriageClassifier",
    "WriterClusterResult",
    "WriterClusterer",
    "classify",
    "cluster_writer",
    "compute_triage_novelty_prescore",
    "score_legibility",
]
