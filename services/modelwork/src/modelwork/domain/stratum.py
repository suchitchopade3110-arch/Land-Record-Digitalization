"""Canonical stratum definition (API-Contracts §6). Owner: Tharun for
calibration; Shruthi uses the identical definition for FR-ANL-11 pairwise
validator agreement reporting. One definition, referenced by both — do not
let two versions drift.

stratum = field_class x script x print_or_handwriting x legibility_band x writer_cluster_id
"""


def stratum_key(field_class: str, script: str, print_or_handwriting: str, legibility_band: str, writer_cluster_id: str) -> str:
    return f"{field_class}|{script}|{print_or_handwriting}|{legibility_band}|{writer_cluster_id}"
