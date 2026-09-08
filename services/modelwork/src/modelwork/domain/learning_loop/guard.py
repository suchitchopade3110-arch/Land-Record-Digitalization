"""Leakage guard — TODO: FR-LRN-11 (P0). Training job refuses to run if any
source-page digest intersects the frozen regression suite's exclusion list.
The loop that would contaminate the suite runs continuously in production
from day one, so this is not optional.
"""


class LeakageDetected(Exception):
    pass


def assert_no_leakage(training_page_digests: set[str], regression_suite_exclusion_list: set[str]) -> None:
    intersection = training_page_digests & regression_suite_exclusion_list
    if intersection:
        raise LeakageDetected(f"training job refuses to run: {len(intersection)} page(s) overlap the regression suite")
