"""Consumes CONFIDENCE_NOVELTY_QUEUE (producer in ["validation", "entity-res"]).

Computes novelty FIRST (FR-CNF-14), gating outside-regime inputs, then executes
per-stratum calibrated confidence and operational posture routing, publishing
DecisionEnvelope onto DECISION_QUEUE for Backend Decision Engine consumption
(FR-CNF-01..04, FR-CNF-15, FR-CFL-01).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from modelwork.domain.calibration.calibrator import (
    CalibratorModel,
    LogisticRegressionCalibrator,
    PerStratumCalibrator,
    ThresholdPolicy,
)
from modelwork.domain.calibration.cold_start import ColdStartState
from modelwork.domain.calibration.decision_flow import process_extraction_for_decision
from modelwork.domain.calibration.novelty import (
    CentroidDistanceNoveltyModel,
    NoveltyDetector,
    NoveltyPolicy,
)

try:
    from observability import traced_consumer
except ImportError:  # pragma: no cover
    def traced_consumer(fn: Callable) -> Callable:
        return fn

try:
    from backend.models.entities import Extraction, Page
except ImportError:  # pragma: no cover
    Extraction = None  # type: ignore[assignment, misc]
    Page = None  # type: ignore[assignment, misc]

logger = logging.getLogger(__name__)

# Canonical producer enums per contracts/asyncapi/confidence-novelty-queue.yaml
VALID_PRODUCERS: frozenset[str] = frozenset({"validation", "entity-res"})

# Default threshold policy derived from 5% target field error (FR-CNF-03)
DEFAULT_THRESHOLD_POLICY = ThresholdPolicy(target_error_rate=0.05)


@traced_consumer
def handle(
    message: dict[str, Any],
    session: Any = None,
    *,
    calibrator: PerStratumCalibrator | CalibratorModel | None = None,
    threshold_policy: ThresholdPolicy | None = None,
    novelty_detector: NoveltyDetector | None = None,
    novelty_policy: NoveltyPolicy | None = None,
    extraction_loader: Callable[[str], Any] | None = None,
    publisher: Callable[..., dict[str, Any]] | None = None,
    cold_start_state: ColdStartState = ColdStartState.STEADY,
    force_audit_sample: bool = False,
    audit_selector: Callable[[str], bool] | None = None,
    publish_to_queue: bool = True,
) -> dict[str, Any]:
    """Execute confidence calibration and novelty evaluation for an extraction validation batch.

    Pipeline:
    1. Validate queue contract producer in {"validation", "entity-res"} and required envelope fields.
    2. Validate pinned provenance in WorkEnvelope (confidence_calibrator and config_version).
    3. Identify referenced extractions from payload.validation_results and optional candidate list.
    4. Hydrate Extraction and Page context through session, extraction_loader, or payload.
    5. Execute novelty-first gating and field-level confidence calibration.
    6. Update Extraction entities with calibrated scores and routing outcome.
    7. Publish DecisionEnvelope to DECISION_QUEUE with producer="confidence-novelty".
    """
    if not isinstance(message, dict) or not message:
        raise ValueError("Message must be a non-empty dictionary")

    producer = message.get("producer")
    if producer not in VALID_PRODUCERS:
        raise ValueError(
            f"Unsupported producer '{producer}' on CONFIDENCE_NOVELTY_QUEUE. Expected one of {sorted(VALID_PRODUCERS)}"
        )

    # Validate required envelope attributes per confidence-novelty-queue.yaml
    required_envelope_keys = ("message_id", "trace_id", "emitted_at", "work_envelope", "payload")
    for key in required_envelope_keys:
        if key not in message or message[key] is None:
            raise ValueError(f"CONFIDENCE_NOVELTY_QUEUE message missing required field '{key}'")

    trace_id = str(message["trace_id"])
    if not trace_id:
        raise ValueError("trace_id must be a non-empty string")

    work_envelope = message["work_envelope"]
    if not isinstance(work_envelope, dict) or not work_envelope:
        raise ValueError("work_envelope must be a non-empty dictionary")

    pinned_models = work_envelope.get("model_versions")
    if not isinstance(pinned_models, dict) or "confidence_calibrator" not in pinned_models:
        raise ValueError("work_envelope missing pinned 'confidence_calibrator' model version")

    pinned_config = work_envelope.get("config_version")
    if not pinned_config or not isinstance(pinned_config, str):
        raise ValueError("work_envelope missing pinned 'config_version'")

    payload = message["payload"]
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dictionary")  # noqa: TRY004

    validation_results = payload.get("validation_results")
    if validation_results is None or not isinstance(validation_results, list):
        raise ValueError("payload missing required 'validation_results' list")

    # Establish model and policy instances
    active_thresh_policy = threshold_policy if threshold_policy is not None else DEFAULT_THRESHOLD_POLICY
    active_calibrator = calibrator if calibrator is not None else LogisticRegressionCalibrator()

    # Determine novelty detector instance strictly respecting pinned model versions
    active_novelty_detector: NoveltyDetector | None = novelty_detector
    if active_novelty_detector is None and "novelty_detector" in pinned_models:
        active_novelty_detector = NoveltyDetector(
            model=CentroidDistanceNoveltyModel(),
            policy=novelty_policy or NoveltyPolicy(),
        )

    # 1. Collect unique extraction IDs referenced across validation results and explicit candidates
    extraction_ids: list[str] = []
    for vr in validation_results:
        if isinstance(vr, dict):
            for fid in vr.get("fields", []):
                if isinstance(fid, str) and fid and fid not in extraction_ids:
                    extraction_ids.append(fid)

    explicit_extractions = payload.get("extractions")
    if isinstance(explicit_extractions, list):
        for item in explicit_extractions:
            eid = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
            if eid and eid not in extraction_ids:
                extraction_ids.append(str(eid))

    if not extraction_ids:
        logger.info("CONFIDENCE_NOVELTY_QUEUE message contains 0 extraction IDs")
        return {
            "status": "ok",
            "trace_id": trace_id,
            "processed_count": 0,
            "decisions": [],
        }

    decisions: list[dict[str, Any]] = []

    # 2. Process each extraction through the decision flow
    for ext_id in extraction_ids:
        ext_obj: Any = None

        # Resolve extraction representation
        if isinstance(explicit_extractions, list):
            for item in explicit_extractions:
                eid = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
                if str(eid) == ext_id:
                    ext_obj = item
                    break

        if ext_obj is None and extraction_loader is not None:
            ext_obj = extraction_loader(ext_id)

        if ext_obj is None and session is not None and hasattr(session, "get"):
            if Extraction is not None:
                ext_obj = session.get(Extraction, ext_id)
            if ext_obj is None:
                try:
                    ext_obj = session.get("Extraction", ext_id)
                except Exception:  # noqa: BLE001
                    ext_obj = None

        if ext_obj is None:
            raise ValueError(f"Extraction with id '{ext_id}' could not be resolved from session or loader")

        # Convert ORM row or dict representation to contract-valid dictionary
        if hasattr(ext_obj, "to_contract_dict") and callable(ext_obj.to_contract_dict):
            ext_dict = ext_obj.to_contract_dict()
        elif isinstance(ext_obj, dict):
            ext_dict = dict(ext_obj)
        else:
            raise ValueError(f"Unable to convert extraction '{ext_id}' to contract dictionary")

        # 3. Hydrate Page metadata if available from database session
        script = ""
        doc_type = ""
        legibility_band = "medium"
        writer_cluster_id = ""

        page_id = ext_dict.get("page_id")
        if session is not None and page_id and hasattr(session, "get"):
            page_obj = None
            if Page is not None:
                try:
                    page_obj = session.get(Page, page_id)
                except Exception:  # noqa: BLE001
                    page_obj = None
            if page_obj is None:
                try:
                    page_obj = session.get("Page", page_id)
                except Exception:  # noqa: BLE001
                    page_obj = None

            if page_obj is not None:
                script = getattr(page_obj, "script", "") or ""
                doc_type = getattr(page_obj, "doc_type", "") or ""
                legibility_band = getattr(page_obj, "legibility_band", "medium") or "medium"
                writer_cluster_id = getattr(page_obj, "writer_cluster_id", "") or ""

        # Fallback to extraction dictionary values if not resolved from Page table
        if not script:
            script = str(ext_dict.get("script") or "")
        if not doc_type:
            doc_type = str(ext_dict.get("doc_type") or "")
        if legibility_band == "medium" and ext_dict.get("legibility_band"):
            legibility_band = str(ext_dict.get("legibility_band"))
        if not writer_cluster_id:
            writer_cluster_id = str(ext_dict.get("writer_cluster_id") or "")

        # 4. Filter validator verdicts relevant to this extraction
        relevant_vrs = [
            vr for vr in validation_results
            if isinstance(vr, dict) and ext_id in vr.get("fields", [])
        ]

        # 5. Execute decision flow: Novelty FIRST -> Stratum Calibration -> Posture Evaluation
        should_publish_internally = publish_to_queue and (publisher is None)

        updated_ext, _calib_res, published_envelope = process_extraction_for_decision(
            extraction=ext_dict,
            work_envelope=work_envelope,
            calibrator=active_calibrator,
            threshold_policy=active_thresh_policy,
            trace_id=trace_id,
            cold_start_state=cold_start_state,
            validation_results=relevant_vrs,
            script=script,
            doc_type=doc_type,
            legibility_band=legibility_band,
            writer_cluster_id=writer_cluster_id,
            force_audit_sample=force_audit_sample,
            audit_selector=audit_selector,
            publish_to_queue=should_publish_internally,
            novelty_detector=active_novelty_detector,
            novelty_policy=novelty_policy,
        )

        # Apply external publisher if injected
        if publish_to_queue and publisher is not None:
            published_envelope = publisher(
                extraction=updated_ext,
                work_envelope=work_envelope,
                trace_id=trace_id,
            )

        # 6. Update database entity if ORM row was loaded
        if ext_obj is not None and not isinstance(ext_obj, dict):
            if hasattr(ext_obj, "calibrated_confidence"):
                ext_obj.calibrated_confidence = updated_ext.get("calibrated_confidence")
            if hasattr(ext_obj, "novelty_score"):
                ext_obj.novelty_score = updated_ext.get("novelty_score")
            if hasattr(ext_obj, "routing_outcome"):
                ext_obj.routing_outcome = updated_ext.get("routing_outcome")
            if hasattr(ext_obj, "model_version"):
                ext_obj.model_version = updated_ext.get("model_version")
            if hasattr(ext_obj, "config_version"):
                ext_obj.config_version = updated_ext.get("config_version")

        decision_item = published_envelope if published_envelope is not None else updated_ext
        decisions.append(decision_item)

    if session is not None and hasattr(session, "flush"):
        try:
            session.flush()
        except Exception:  # noqa: BLE001, S110
            pass

    return {
        "status": "ok",
        "trace_id": trace_id,
        "processed_count": len(decisions),
        "decisions": decisions,
    }
