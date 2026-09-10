"""Unit tests for P0 Relationship Extraction (owner ↔ share ↔ parcel).

Tests:
1. One owner / one parcel
2. Multiple owners / shared parcel
3. Multiple parcels
4. Ambiguous association
5. Missing fields
"""
import uuid
import pytest
from extraction.domain.ocr.relationship_extractor import OwnerParcelShareBinding, extract_relationships


def _make_extraction(field_name: str, raw_value: str, y: float = 10.0, confidence: float = 0.95) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "page_id": "page-rel-123",
        "field_name": field_name,
        "raw_value": raw_value,
        "canonical_value": raw_value.strip(),
        "unconstrained_value": raw_value.strip(),
        "bbox": {"x": 10.0, "y": y, "w": 100.0, "h": 20.0},
        "engine": "printed_ocr",
        "model_version": "baidu-unlimited-v1",
        "config_version": "v1",
        "token_confidence": confidence,
        "entry_status": "unknown",
    }


# 1. One Owner / One Parcel
def test_relationship_one_owner_one_parcel():
    extractions = [
        _make_extraction("survey_number", "Khasra No 100", y=10.0, confidence=0.98),
        _make_extraction("owner_name", "Owner: Anita Devi", y=40.0, confidence=0.95),
    ]

    bindings = extract_relationships(extractions, page_id="page-rel-123")
    assert len(bindings) == 1

    b = bindings[0]
    assert b["is_ambiguous"] is False
    assert b["parcel"]["raw_value"] == "Khasra No 100"
    assert len(b["owners"]) == 1
    assert b["owners"][0]["raw_value"] == "Owner: Anita Devi"
    assert b["share"] is None
    assert b["confidence"] == round((0.98 + 0.95) / 2, 2)  # 0.96 (round half to even)


# 2. Multiple Owners / Shared Parcel
def test_relationship_multiple_owners_shared_parcel():
    extractions = [
        _make_extraction("survey_number", "Khasra 45", y=10.0, confidence=0.99),
        _make_extraction("owner_name", "Ramesh Chand", y=40.0, confidence=0.96),
        _make_extraction("share_fraction", "Share: 1/2", y=40.0, confidence=0.95),
        _make_extraction("owner_name", "Suresh Kumar", y=70.0, confidence=0.94),
        _make_extraction("share_fraction", "Share: 1/2", y=70.0, confidence=0.95),
    ]

    bindings = extract_relationships(extractions, page_id="page-rel-123")
    assert len(bindings) == 1

    b = bindings[0]
    assert b["is_ambiguous"] is False
    assert b["parcel"]["raw_value"] == "Khasra 45"
    assert len(b["owners"]) == 2
    assert b["owners"][0]["raw_value"] == "Ramesh Chand"
    assert b["owners"][1]["raw_value"] == "Suresh Kumar"
    assert b["share"]["share_numerator"] == 1
    assert b["share"]["share_denominator"] == 2


# 3. Multiple Parcels
def test_relationship_multiple_parcels():
    extractions = [
        # Parcel Block 1 (y: 10 - 50)
        _make_extraction("survey_number", "Khasra 101", y=10.0, confidence=0.98),
        _make_extraction("owner_name", "Sita Ram", y=30.0, confidence=0.96),
        # Parcel Block 2 (y: 100 - 150)
        _make_extraction("survey_number", "Khasra 102", y=100.0, confidence=0.97),
        _make_extraction("owner_name", "Geeta Devi", y=130.0, confidence=0.95),
    ]

    bindings = extract_relationships(extractions, page_id="page-rel-123")
    assert len(bindings) == 2

    # Block 1
    b1 = bindings[0]
    assert b1["parcel"]["raw_value"] == "Khasra 101"
    assert len(b1["owners"]) == 1
    assert b1["owners"][0]["raw_value"] == "Sita Ram"

    # Block 2
    b2 = bindings[1]
    assert b2["parcel"]["raw_value"] == "Khasra 102"
    assert len(b2["owners"]) == 1
    assert b2["owners"][0]["raw_value"] == "Geeta Devi"


# 4. Ambiguous Association
def test_relationship_ambiguous_association():
    extractions = [
        # Owners appearing far above any parcel header without clear alignment
        _make_extraction("owner_name", "Unbound Owner 1", y=10.0, confidence=0.8),
        _make_extraction("owner_name", "Unbound Owner 2", y=30.0, confidence=0.8),
        _make_extraction("survey_number", "Khasra 500", y=200.0, confidence=0.9),
    ]

    bindings = extract_relationships(extractions, page_id="page-rel-123")
    assert len(bindings) >= 1

    # Check for presence of ambiguous flag
    ambiguous_bindings = [b for b in bindings if b["is_ambiguous"]]
    assert len(ambiguous_bindings) > 0


# 5. Missing Fields
def test_relationship_missing_fields():
    # Scenario A: Missing parcel (only owner listed)
    extractions_no_parcel = [
        _make_extraction("owner_name", "Standalone Owner", y=20.0, confidence=0.9),
    ]
    bindings_no_parcel = extract_relationships(extractions_no_parcel, page_id="page-rel-123")
    assert len(bindings_no_parcel) == 1
    assert bindings_no_parcel[0]["parcel"] is None
    assert bindings_no_parcel[0]["owners"][0]["raw_value"] == "Standalone Owner"

    # Scenario B: Missing owner (only parcel listed)
    extractions_no_owner = [
        _make_extraction("survey_number", "Unclaimed Survey 999", y=20.0, confidence=0.9),
    ]
    bindings_no_owner = extract_relationships(extractions_no_owner, page_id="page-rel-123")
    assert len(bindings_no_owner) == 1
    assert bindings_no_owner[0]["parcel"]["raw_value"] == "Unclaimed Survey 999"
    assert bindings_no_owner[0]["owners"] == []
    assert bindings_no_owner[0]["is_ambiguous"] is True
