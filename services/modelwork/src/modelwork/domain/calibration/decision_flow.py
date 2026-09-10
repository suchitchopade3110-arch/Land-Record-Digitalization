"""Integration boundary between Extraction, field-level Calibrator, and Decision Flow (FR-CNF-01..04, FR-CNF-15).

Translates Extraction contract payloads into CalibratorFeatures, executes
per-stratum or pooled fallback calibration, applies ThresholdPolicy within the
context of ColdStartState operational posture, updates Extraction with
calibrated_confidence and routing_outcome, and publishes to DECISION_QUEUE via DecisionPublisher.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from modelwork.domain.calibration.calibrator import (
    CalibrationRegime,
    CalibrationResult,
    CalibratorFeatures,
    CalibratorModel,
    PerStratumCalibrator,
    ThresholdPolicy,
)
from modelwork.domain.calibration.cold_start import (
    ColdStartState,
    OperationalPosture,
    get_operational_posture,
)
from modelwork.domain.calibration.novelty import (
    NoveltyDetector,
    NoveltyFeatures,
    NoveltyPolicy,
    NoveltyResult,
)
from modelwork.publishers import decision_publisher


@dataclass(frozen=True)
class AuditSamplingPolicy:
    """Configurable audit sampling policy parameters (FR-CNF-07).

    PROVISIONAL POLICY / PLACEHOLDER:
    Captures standard (0.05) and elevated (0.20) audit sampling rates as configuration
    constants for the P0 integration boundary. Full statistical sampling is P1 scope.
    """

    standard_rate: float = 0.05
    elevated_rate: float = 0.20


@dataclass(frozen=True)
class RoutingDecision:
    """Structured decision output of field-level calibration and posture-aware routing."""

    outcome: str
    calibrated_confidence: float
    raw_confidence: float
    stratum: str
    regime: CalibrationRegime
    posture: OperationalPosture
    is_conflict: bool
    feature_attributions: dict[str, float] = field(default_factory=dict)


def extraction_to_calibrator_features(
    extraction: dict[str, Any],
    validation_results: list[dict[str, Any]] | None = None,
    script: str = "",
    doc_type: str = "",
    legibility_band: str = "medium",
    writer_cluster_id: str = "",
    print_or_handwriting: str | None = None,
    layout_certainty: float | None = None,
    normalization_confidence: float | None = None,
) -> CalibratorFeatures:
    """Convert an Extraction representation into CalibratorFeatures.

    Extracts supported signals while strictly separating writer_cluster_id as stratum
    identity (not a pooled model feature) and omitting novelty_score (FR-CNF-14).
    """
    if not isinstance(extraction, dict) or not extraction:
        raise ValueError("Extraction must be a non-empty dictionary")

    extraction_id = extraction.get("id")
    if not extraction_id:
        raise ValueError("Extraction missing required 'id'")

    field_class = extraction.get("field_name") or extraction.get("field_class")
    if not field_class:
        raise ValueError("Extraction missing required 'field_name'")

    raw_conf = extraction.get("token_confidence")
    if raw_conf is None:
        raise ValueError("Extraction missing required 'token_confidence'")
    if not isinstance(raw_conf, (int, float)) or not math.isfinite(raw_conf):
        raise ValueError(f"token_confidence must be a finite real number, got {raw_conf}")
    if not (0.0 <= float(raw_conf) <= 1.0):
        raise ValueError(f"token_confidence must be in [0.0, 1.0], got {raw_conf}")

    # Determine print vs handwriting from extraction engine if not explicitly provided
    if print_or_handwriting is None:
        engine = str(extraction.get("engine", "")).lower()
        if "hwr" in engine:
            p_or_h = "handwritten"
        elif "ocr" in engine or "table" in engine:
            p_or_h = "printed"
        else:
            p_or_h = ""
    else:
        p_or_h = print_or_handwriting

    # Collate validator verdicts for this extraction if validation results are supplied
    validator_outcomes: dict[str, str] = {}
    if validation_results:
        for vr in validation_results:
            fields = vr.get("fields", [])
            if extraction_id in fields:
                val_name = str(vr.get("validator", "unknown"))
                verdict = str(vr.get("verdict", "pass"))
                validator_outcomes[val_name] = verdict

    # Use explicit layout / normalization signals if present on extraction
    layout = layout_certainty if layout_certainty is not None else extraction.get("layout_certainty")
    norm = normalization_confidence if normalization_confidence is not None else extraction.get("normalization_confidence")

    return CalibratorFeatures(
        token_confidence=float(raw_conf),
        layout_certainty=layout,
        normalization_confidence=norm,
        validator_outcomes=validator_outcomes,
        field_class=str(field_class),
        script=str(script),
        doc_type=str(doc_type),
        print_or_handwriting=str(p_or_h),
        legibility_band=str(legibility_band),
        writer_cluster_id=str(writer_cluster_id),
    )


def determine_routing_outcome(
    calibration_result: CalibrationResult,
    threshold_policy: ThresholdPolicy,
    cold_start_state: ColdStartState = ColdStartState.STEADY,
    *,
    is_conflict: bool = False,
    is_outside_regime: bool = False,
    is_audit_sample: bool = False,
) -> RoutingDecision:
    """Evaluate calibrated confidence against threshold policy and cold-start posture.

    Dispatches to exactly one of the 5 canonical routing outcomes in contracts/schemas/extraction.schema.json:
    - outside_calibrated_regime:
      * Destination: SOURCE-OF-TRUTH REQUIREMENT (FR-CNF-14, Architecture §14/§15).
      * Triggering Rule: COMPATIBLE IMPLEMENTATION POLICY. Consumes upstream novelty indicator;
        never auto-accepted at any confidence and generates cluster alert rather than per-field review.
    - conflict:
      * Destination: SOURCE-OF-TRUTH REQUIREMENT (FR-CFL-01, Architecture §15).
      * Triggering Rule: COMPATIBLE IMPLEMENTATION POLICY. Translates field validator failure
        or explicit conflict flag to 'conflict', routing to conflict register pending M10 wiring.
    - auto_accept:
      * Destination & Rule: SOURCE-OF-TRUTH REQUIREMENT (FR-CNF-03, FR-CNF-15).
      * Calibrated confidence meets dynamic threshold AND cold-start posture allows auto-acceptance.
    - audit_sample:
      * Destination: SOURCE-OF-TRUTH REQUIREMENT (FR-CNF-07, Architecture §15).
      * Triggering Rule: COMPATIBLE PROVISIONAL POLICY. Selects verification sample via
        deterministic hook/flag (full statistical sampling engine deferred to dedicated component).
    - review:
      * Destination & Rule: SOURCE-OF-TRUTH REQUIREMENT (FR-CNF-03, FR-CNF-15).
      * Confidence below dynamic threshold OR posture strictly disables auto-acceptance (SHADOW/REGRESSION).
    """
    posture = get_operational_posture(cold_start_state)

    # 1. Upstream novelty / out-of-regime gate (FR-CNF-14)
    if is_outside_regime:
        return RoutingDecision(
            outcome="outside_calibrated_regime",
            calibrated_confidence=calibration_result.calibrated_confidence,
            raw_confidence=calibration_result.raw_confidence,
            stratum=calibration_result.stratum,
            regime=calibration_result.regime,
            posture=posture,
            is_conflict=False,
            feature_attributions=calibration_result.feature_attributions,
        )

    # 2. Validator contradiction / conflict register entry (FR-CFL-01)
    if is_conflict:
        return RoutingDecision(
            outcome="conflict",
            calibrated_confidence=calibration_result.calibrated_confidence,
            raw_confidence=calibration_result.raw_confidence,
            stratum=calibration_result.stratum,
            regime=calibration_result.regime,
            posture=posture,
            is_conflict=True,
            feature_attributions=calibration_result.feature_attributions,
        )

    # 3. Evaluate threshold policy derived from target error rate (FR-CNF-03)
    meets_threshold = threshold_policy.is_auto_acceptable(calibration_result.calibrated_confidence)

    if not meets_threshold:
        outcome = "review"
    else:
        # Confidence satisfies threshold; evaluate operational posture
        if not posture.auto_accept_allowed:
            # In SHADOW or REGRESSION: automated acceptance is strictly disabled
            outcome = "review"
        elif is_audit_sample:
            # Audit sample selected per FR-CNF-07
            outcome = "audit_sample"
        else:
            # Automated acceptance enabled in RAMP (conservative) or STEADY (standard)
            outcome = "auto_accept"

    return RoutingDecision(
        outcome=outcome,
        calibrated_confidence=calibration_result.calibrated_confidence,
        raw_confidence=calibration_result.raw_confidence,
        stratum=calibration_result.stratum,
        regime=calibration_result.regime,
        posture=posture,
        is_conflict=False,
        feature_attributions=calibration_result.feature_attributions,
    )


def process_extraction_for_decision(
    extraction: dict[str, Any],
    work_envelope: dict[str, Any],
    calibrator: PerStratumCalibrator | CalibratorModel,
    threshold_policy: ThresholdPolicy,
    trace_id: str,
    *,
    cold_start_state: ColdStartState = ColdStartState.STEADY,
    validation_results: list[dict[str, Any]] | None = None,
    script: str = "",
    doc_type: str = "",
    legibility_band: str = "medium",
    writer_cluster_id: str = "",
    print_or_handwriting: str | None = None,
    layout_certainty: float | None = None,
    normalization_confidence: float | None = None,
    is_conflict: bool = False,
    is_outside_regime: bool = False,
    force_audit_sample: bool = False,
    audit_selector: Callable[[str], bool] | None = None,
    publish_to_queue: bool = True,
    novelty_detector: NoveltyDetector | None = None,
    novelty_policy: NoveltyPolicy | None = None,
    novelty_result: NoveltyResult | None = None,
    novelty_features: NoveltyFeatures | None = None,
    novelty_cluster_id: str | None = None,
) -> tuple[dict[str, Any], CalibrationResult, dict[str, Any] | None]:
    """Execute the P0 calibration-to-decision pipeline for an Extraction.

    1. Validates pinned provenance in WorkEnvelope (does not query active registry).
    2. Evaluates novelty FIRST (FR-CNF-14), gating outside-regime inputs.
    3. Converts Extraction to CalibratorFeatures.
    4. Executes field-level calibration (per-stratum or pooled fallback).
    5. Evaluates ThresholdPolicy and ColdStartState posture.
    6. Updates Extraction with calibrated_confidence, novelty_score, and routing_outcome.
    7. Publishes to DECISION_QUEUE via DecisionPublisher if publish_to_queue is True.
    """
    if not isinstance(work_envelope, dict) or not work_envelope:
        raise ValueError("work_envelope must be a non-empty dictionary")

    pinned_models = work_envelope.get("model_versions")
    if not isinstance(pinned_models, dict) or "confidence_calibrator" not in pinned_models:
        raise ValueError("work_envelope missing pinned 'confidence_calibrator' model version")

    pinned_config = work_envelope.get("config_version")
    if not pinned_config or not isinstance(pinned_config, str):
        raise ValueError("work_envelope missing pinned 'config_version'")

    if (novelty_detector is not None or novelty_result is not None) and "novelty_detector" not in pinned_models:
        raise ValueError("work_envelope missing pinned 'novelty_detector' model version")

    if not trace_id or not isinstance(trace_id, str):
        raise ValueError("trace_id must be a non-empty string")

    if calibrator is None:
        raise ValueError("Calibrator model is unavailable")

    # Construct features from extraction and context
    features = extraction_to_calibrator_features(
        extraction=extraction,
        validation_results=validation_results,
        script=script,
        doc_type=doc_type,
        legibility_band=legibility_band,
        writer_cluster_id=writer_cluster_id,
        print_or_handwriting=print_or_handwriting,
        layout_certainty=layout_certainty,
        normalization_confidence=normalization_confidence,
    )

    # 1. Evaluate novelty FIRST if detector or result is provided (FR-CNF-14).
    # Novelty evaluation is strictly independent of confidence scores.
    active_novelty_result: NoveltyResult | None = novelty_result
    if active_novelty_result is None and novelty_detector is not None:
        if novelty_features is None:
            novelty_features = NoveltyFeatures(
                embedding=extraction.get("embedding"),
                stratum=features.canonical_stratum(),
                field_class=features.field_class,
                script=features.script,
                doc_type=features.doc_type,
                print_or_handwriting=features.print_or_handwriting,
                legibility_band=features.legibility_band,
                writer_cluster_id=features.writer_cluster_id,
                layout_certainty=features.layout_certainty,
                unconstrained_value=extraction.get("unconstrained_value"),
                raw_value=str(extraction.get("raw_value", "")),
            )
        active_novelty_result = novelty_detector.evaluate_novelty(novelty_features, policy=novelty_policy)

    if active_novelty_result is not None:
        is_outside_regime = is_outside_regime or active_novelty_result.is_outside_regime
        novelty_score: float | None = active_novelty_result.novelty_score
        if novelty_cluster_id is None:
            novelty_cluster_id = active_novelty_result.cluster_id
    else:
        raw_ns = extraction.get("novelty_score")
        novelty_score = float(raw_ns) if raw_ns is not None else None

    # 2. Execute calibration
    if isinstance(calibrator, PerStratumCalibrator):
        calib_res = calibrator.calibrate(features)
    else:
        conf, attributions = calibrator.predict_calibrated_confidence(features)
        calib_res = CalibrationResult(
            calibrated_confidence=conf,
            raw_confidence=features.token_confidence,
            regime=CalibrationRegime.POOLED_FALLBACK,
            stratum=features.canonical_stratum(),
            feature_attributions=attributions,
        )

    # IMPLEMENTATION POLICY: Collate field-level validator failure verdicts as conflict.
    # The 'conflict' outcome is a source-of-truth requirement (FR-CFL-01), while
    # translating raw validation verdicts into is_conflict is a provisional policy boundary.
    has_validator_failure = False
    if validation_results:
        for vr in validation_results:
            if extraction.get("id") in vr.get("fields", []) and str(vr.get("verdict", "")).lower() == "fail":
                has_validator_failure = True
                break

    conflict_flag = is_conflict or has_validator_failure

    # Check audit sample eligibility
    ext_id = str(extraction.get("id", ""))
    selected_for_audit = force_audit_sample or (audit_selector(ext_id) if audit_selector else False)

    # 3. Determine routing outcome
    decision = determine_routing_outcome(
        calibration_result=calib_res,
        threshold_policy=threshold_policy,
        cold_start_state=cold_start_state,
        is_conflict=conflict_flag,
        is_outside_regime=is_outside_regime,
        is_audit_sample=selected_for_audit,
    )

    # Update extraction payload with calibrated score, novelty score, routing outcome, and pinned versions
    # Preserves token_confidence (raw_confidence) without mutation
    updated_extraction = dict(extraction)
    updated_extraction["calibrated_confidence"] = decision.calibrated_confidence
    updated_extraction["novelty_score"] = novelty_score
    updated_extraction["routing_outcome"] = decision.outcome
    updated_extraction["model_version"] = pinned_models["confidence_calibrator"]
    updated_extraction["config_version"] = pinned_config

    published_envelope: dict[str, Any] | None = None
    if publish_to_queue:
        published_envelope = decision_publisher.publish(
            extraction=updated_extraction,
            work_envelope=work_envelope,
            trace_id=trace_id,
            novelty_cluster_id=novelty_cluster_id,
        )

    return updated_extraction, calib_res, published_envelope
