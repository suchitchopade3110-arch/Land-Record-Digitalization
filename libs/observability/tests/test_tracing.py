# TODO: expand as tracing.py grows — this only smoke-tests the trace_id format.
from observability import trace_id_for


def test_trace_id_format():
    assert trace_id_for("doc-1", "page-1") == "doc-1:page-1"
