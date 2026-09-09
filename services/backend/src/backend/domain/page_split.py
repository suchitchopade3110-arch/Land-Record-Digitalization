"""FR-ING-03 — split a multi-page original into individually addressable
pages. This is structural container work, not OCR/model work: `pypdf`
walks a PDF's own page tree, Pillow walks a multi-frame TIFF's own frame
index — neither library reads or interprets what's drawn on a page, only
the container format around it, which is why this is backend's job and
not Shree's (M3/M4 read the *content* of a page this module has already
made addressable).

`split_stream` is a generator so `backend.workers.ingestion_consumer` can
store one page at a time and never hold all N pages of a large document
in memory at once — the 500-page acceptance test (T2.a) is the reason
this is a generator and not a `list[SplitPage]`.
"""
from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass
from typing import IO

from PIL import Image
from pypdf import PdfReader, PdfWriter


@dataclass(frozen=True)
class SplitPage:
    index: int
    data: bytes
    mime: str  # the per-page artifact's own MIME — same container family as the original


class UnsplittableDocument(ValueError):
    """A recognized MIME with content pypdf/Pillow can't parse as pages —
    distinct from `ingest.UnsupportedMediaType` (rejected before storage)
    because this fails *after* custody already succeeded (FR-ING-02
    immutability holds regardless of what happens next)."""


def split_stream(mime: str, stream: IO[bytes]) -> Iterator[SplitPage]:
    """`stream` must support seeking (a real file or a spooled tempfile —
    `backend.workers.ingestion_consumer` materializes a possibly
    non-seekable object-store stream to one before calling this, since
    pypdf's page tree lives at the end of a PDF and needs random access).
    """
    if mime == "application/pdf":
        yield from _split_pdf(stream)
    elif mime == "image/tiff":
        yield from _split_tiff(stream)
    elif mime in ("image/jpeg", "image/png"):
        yield SplitPage(index=0, data=stream.read(), mime=mime)
    else:
        raise UnsplittableDocument(f"no page-split strategy for mime={mime!r}")


def count_pages(mime: str, stream: IO[bytes]) -> int:
    """FR-ING-08 needs a batch's expected page total independent of
    actually materializing every page's bytes — this reads only the
    container's page count (PDF page-tree length / TIFF frame count),
    then rewinds `stream` so a subsequent `split_stream` call still works."""
    if mime == "application/pdf":
        n = len(PdfReader(stream).pages)
    elif mime == "image/tiff":
        img = Image.open(stream)
        n = img.n_frames
    elif mime in ("image/jpeg", "image/png"):
        n = 1
    else:
        raise UnsplittableDocument(f"no page-count strategy for mime={mime!r}")
    stream.seek(0)
    return n


def _split_pdf(stream: IO[bytes]) -> Iterator[SplitPage]:
    try:
        reader = PdfReader(stream)
        total = len(reader.pages)
    except Exception as e:  # pragma: no cover — pypdf raises several distinct error types
        raise UnsplittableDocument(f"could not parse PDF page tree: {e}") from e

    for i in range(total):
        writer = PdfWriter()
        writer.add_page(reader.pages[i])
        buf = io.BytesIO()
        writer.write(buf)
        yield SplitPage(index=i, data=buf.getvalue(), mime="application/pdf")


def _split_tiff(stream: IO[bytes]) -> Iterator[SplitPage]:
    try:
        img = Image.open(stream)
        total = img.n_frames
    except Exception as e:  # pragma: no cover
        raise UnsplittableDocument(f"could not parse TIFF frame index: {e}") from e

    for i in range(total):
        img.seek(i)
        buf = io.BytesIO()
        # Copy the frame out before saving — Image.save() on a
        # multi-frame-backed Image object can otherwise re-serialize the
        # whole frame sequence depending on the codec.
        frame = img.copy()
        frame.save(buf, format="TIFF")
        yield SplitPage(index=i, data=buf.getvalue(), mime="image/tiff")
