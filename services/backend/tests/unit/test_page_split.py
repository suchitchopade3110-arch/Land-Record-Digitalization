"""FR-ING-03 — page-split unit tests. No DB needed (pure functions over
bytes), so these run under `make test-unit` alongside every other
service's fast unit suite.
"""
import io

import pytest
from PIL import Image
from pypdf import PdfWriter

from backend.domain.page_split import UnsplittableDocument, count_pages, split_stream


def _make_pdf_bytes(num_pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=200, height=300)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_tiff_bytes(num_frames: int) -> bytes:
    frames = [Image.new("L", (20, 20), color=i * 10) for i in range(num_frames)]
    buf = io.BytesIO()
    frames[0].save(buf, format="TIFF", save_all=True, append_images=frames[1:])
    return buf.getvalue()


def test_split_a_500_page_pdf_yields_500_addressable_pages():
    """T2.a's acceptance shape, at the unit level."""
    data = _make_pdf_bytes(500)
    pages = list(split_stream("application/pdf", io.BytesIO(data)))

    assert len(pages) == 500
    assert [p.index for p in pages] == list(range(500))
    assert all(p.mime == "application/pdf" for p in pages)
    assert all(len(p.data) > 0 for p in pages)


def test_count_pages_matches_split_stream_and_rewinds_the_stream():
    data = _make_pdf_bytes(7)
    stream = io.BytesIO(data)

    n = count_pages("application/pdf", stream)
    assert n == 7

    # count_pages must rewind — a subsequent split_stream call on the same
    # stream still works.
    pages = list(split_stream("application/pdf", stream))
    assert len(pages) == 7


def test_split_a_multi_frame_tiff_yields_one_page_per_frame():
    data = _make_tiff_bytes(12)
    pages = list(split_stream("image/tiff", io.BytesIO(data)))

    assert len(pages) == 12
    assert [p.index for p in pages] == list(range(12))
    assert all(p.mime == "image/tiff" for p in pages)


def test_split_a_single_page_jpeg_yields_exactly_one_page():
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color="white").save(buf, format="JPEG")
    data = buf.getvalue()

    pages = list(split_stream("image/jpeg", io.BytesIO(data)))
    assert len(pages) == 1
    assert pages[0].index == 0
    assert pages[0].data == data
    assert pages[0].mime == "image/jpeg"


def test_split_stream_rejects_an_unrecognized_mime():
    with pytest.raises(UnsplittableDocument):
        list(split_stream("application/x-not-a-real-format", io.BytesIO(b"whatever")))
