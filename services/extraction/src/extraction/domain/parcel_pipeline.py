"""M4 Map/GIS Pipeline Execution.

End-to-end execution path:
map_payload + work_envelope
-> Georeferencing (affine transform + RMSE)
-> Parcel boundary vectorization (GeoJSON polygons)
-> Survey label binding (spatial point-in-polygon)
-> Exact decimal area computation
-> ULPIN eligibility threshold check
-> ParcelGeometry contract output
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from .georeference import Georeferencer
from .vectorize import bind_survey_labels, compute_area, vectorize_parcels

logger = logging.getLogger(__name__)

# FR-MAP-05 ULPIN eligibility strict RMSE threshold (in meters)
ULPIN_RMSE_THRESHOLD = 1.5


def process_map_page(
    map_payload: dict[str, Any],
    work_envelope: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Executes M4 Map/GIS vertical slice for a single map page.

    Returns strictly contract-compliant ParcelGeometry dict items.
    """
    if not isinstance(map_payload, dict):
        logger.warning("Invalid map_payload: must be a dict")
        return []

    page_id = map_payload.get("page_id") or map_payload.get("id") or "00000000-0000-0000-0000-000000000000"
    labels = map_payload.get("labels") or map_payload.get("survey_labels") or []

    # 1. Georeference map page
    georeferencer = Georeferencer()
    georef_res = georeferencer.georeference_map(map_payload)

    # 2. Vectorize parcel boundaries
    parcels = vectorize_parcels(map_payload, georef_result=georef_res)
    if not parcels:
        logger.info("0 parcel polygons vectorized for page %s", page_id)
        return []

    # 3. Bind survey number labels
    bound_parcels = bind_survey_labels(parcels, labels, georef_result=georef_res)

    # 4. Determine ULPIN eligibility
    is_ulpin_eligible = georef_res.rmse <= ULPIN_RMSE_THRESHOLD

    parcel_geometries: list[dict[str, Any]] = []

    for item in bound_parcels:
        polygon = item.get("polygon")
        if not item.get("is_valid", True) or not polygon:
            logger.info("Skipping invalid/broken parcel boundary item %s", item.get("boundary_id"))
            continue

        area_str = compute_area(polygon, crs=georef_res.crs)

        pg_dict = {
            "id": str(uuid.uuid4()),
            "page_id": page_id,
            "polygon": polygon,
            "crs": georef_res.crs,
            "area_computed": area_str,
            "control_points": georef_res.control_points_count,
            "georef_rmse": georef_res.rmse,
            "transform_type": georef_res.transform_type,
            "ulpin_eligible": is_ulpin_eligible,
            "bound_survey_no": item.get("bound_survey_no"),
            "conflation_lineage_ref": map_payload.get("conflation_lineage_ref"),
        }
        parcel_geometries.append(pg_dict)

    return parcel_geometries
