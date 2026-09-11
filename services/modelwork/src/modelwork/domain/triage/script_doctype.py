"""Script/language ID, doc-type classification, page-role classification.
Requirements: FR-TRI-02 (script), FR-TRI-03 (doc_type), FR-TRI-04 (page_role).

SOURCE-OF-TRUTH CONTRACTS:
- contracts/schemas/page.schema.json:
  - doc_type enum: ["ror", "jamabandi", "khasra_khatauni", "mutation_register", "deed", "cadastral_map", "fmb_sketch", "unknown", null]
  - page_role enum: ["text", "tabular_register", "map_sheet", "endorsement", "blank", null]
  - script: string | null
  - language: string | null
- contracts/schemas/model_version.schema.json: module "triage_classifier"

NON-NEGOTIABLE SAFETY:
- Conforms strictly to schema enums. Unsupported enum values are rejected.
- Zero personal identity information or officer names are processed or exposed.
- Protocol interfaces allow plugging real trained classifiers when available;
  deterministic test doubles unblock P0 boundary integration without fabricating ML weights.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

# Authoritative enums strictly mirrored from contracts/schemas/page.schema.json
VALID_DOC_TYPES: frozenset[str] = frozenset({
    "ror",
    "jamabandi",
    "khasra_khatauni",
    "mutation_register",
    "deed",
    "cadastral_map",
    "fmb_sketch",
    "unknown",
})

VALID_PAGE_ROLES: frozenset[str] = frozenset({
    "text",
    "tabular_register",
    "map_sheet",
    "endorsement",
    "blank",
})

# Compatible implementation baseline for canonical scripts and languages
KNOWN_SCRIPTS: frozenset[str] = frozenset({
    "devanagari",
    "nastaliq",
    "gurmukhi",
    "bengali",
    "gujarati",
    "kannada",
    "telugu",
    "tamil",
    "malayalam",
    "odia",
    "latin",
    "modi",
    "unknown",
})

KNOWN_LANGUAGES: frozenset[str] = frozenset({
    "hi",
    "ur",
    "pa",
    "bn",
    "gu",
    "kn",
    "te",
    "ta",
    "ml",
    "or",
    "en",
    "mr",
    "unknown",
})


@dataclass(frozen=True)
class ScriptDoctypeResult:
    """Structured result of triage script, language, doc-type, and page-role classification."""

    script: str | None
    language: str | None
    doc_type: str | None
    page_role: str | None
    model_version: str = "triage_classifier_v1"

    def __post_init__(self) -> None:
        if self.doc_type is not None and self.doc_type not in VALID_DOC_TYPES:
            raise ValueError(
                f"Invalid doc_type: {self.doc_type!r}. Must be one of {sorted(VALID_DOC_TYPES)} or None"
            )
        if self.page_role is not None and self.page_role not in VALID_PAGE_ROLES:
            raise ValueError(
                f"Invalid page_role: {self.page_role!r}. Must be one of {sorted(VALID_PAGE_ROLES)} or None"
            )
        if self.script is not None and not isinstance(self.script, str):
            raise ValueError(f"script must be a string or None, got {type(self.script)}")
        if self.language is not None and not isinstance(self.language, str):
            raise ValueError(f"language must be a string or None, got {type(self.language)}")


class ScriptClassifier(Protocol):
    """Protocol for script identification (FR-TRI-02)."""

    def classify_script(self, page_image: bytes) -> str | None:
        ...


class LanguageClassifier(Protocol):
    """Protocol for language identification."""

    def classify_language(self, page_image: bytes) -> str | None:
        ...


class DocTypeClassifier(Protocol):
    """Protocol for document type classification (FR-TRI-03)."""

    def classify_doc_type(self, page_image: bytes) -> str | None:
        ...


class PageRoleClassifier(Protocol):
    """Protocol for page role classification (FR-TRI-04)."""

    def classify_page_role(self, page_image: bytes) -> str | None:
        ...


class TriageClassifier(Protocol):
    """Composite protocol for running all script and document triage classifiers."""

    def classify(self, page_image: bytes) -> ScriptDoctypeResult:
        ...


@dataclass(frozen=True)
class DeterministicScriptDoctypeClassifier:
    """Deterministic test double for script, language, doc-type, and page-role classification.

    COMPATIBLE IMPLEMENTATION POLICY / TEST-ONLY DOUBLE:
    Enables contract and unit testing of downstream routing and schema validation without
    pretending to run production neural OCR/classification backbones.
    """

    default_script: str | None = "devanagari"
    default_language: str | None = "hi"
    default_doc_type: str | None = "jamabandi"
    default_page_role: str | None = "tabular_register"
    model_version: str = "triage_classifier_v1"
    classify_fn: Callable[[bytes], dict[str, Any]] | None = None

    def classify(self, page_image: bytes) -> ScriptDoctypeResult:
        if self.classify_fn is not None:
            overrides = self.classify_fn(page_image)
            return ScriptDoctypeResult(
                script=overrides.get("script", self.default_script),
                language=overrides.get("language", self.default_language),
                doc_type=overrides.get("doc_type", self.default_doc_type),
                page_role=overrides.get("page_role", self.default_page_role),
                model_version=overrides.get("model_version", self.model_version),
            )

        return ScriptDoctypeResult(
            script=self.default_script,
            language=self.default_language,
            doc_type=self.default_doc_type,
            page_role=self.default_page_role,
            model_version=self.model_version,
        )


def classify(
    page_image: bytes,
    classifier: TriageClassifier | None = None,
) -> dict[str, Any]:
    """Domain entry point for script/language/doc-type/page-role classification (FR-TRI-02..04).

    Returns a dict representation matching page.schema.json properties:
    {"script": str | None, "language": str | None, "doc_type": str | None, "page_role": str | None, "model_version": str}
    """
    active_classifier = classifier or DeterministicScriptDoctypeClassifier()
    res = active_classifier.classify(page_image)
    return {
        "script": res.script,
        "language": res.language,
        "doc_type": res.doc_type,
        "page_role": res.page_role,
        "model_version": res.model_version,
    }
