"""FR-TRI-06 — preprocessing hook: deskew, denoise, binarize, dewarp.
These are image operations, not models — Tharun's triage classifiers are
models; these aren't (per Team-Split's "these are image operations, not
models" framing). Backend owns two things regardless of which operator
library implements them: the retention policy for the preprocessed image
(this module returns bytes; the caller — the triage/preprocessing worker —
decides where they're stored and how they're linked to the original page,
matching every other domain function here that doesn't reach into object
storage unless its whole job is custody) and the coordinate transform back
to original-image space, so a bbox computed against the preprocessed image
(by Shree's OCR, downstream) still means something in terms of the
original scan.

The operators below (Pillow: grayscale + median-filter denoise, a coarse
projection-profile deskew, fixed-threshold binarization) are a
deliberately simple off-the-shelf starting set — good enough to prove the
retention+coordinate-transform contract holds; swapping in a more
sophisticated operator (adaptive thresholding, a real Hough-based deskew)
is a drop-in replacement for the function body, not a change to this
module's two responsibilities above.

Dewarp is not implemented — no dependency-light, off-the-shelf dewarp
exists at this scope. Left as an explicit TODO rather than a silent no-op
under the same name, per this repo's convention (README: "every
still-unimplemented file carries a TODO comment naming the FR it will
satisfy").
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from math import cos, radians, sin

from PIL import Image, ImageFilter, ImageStat


@dataclass(frozen=True)
class AffineTransform:
    """Maps a point in the PREPROCESSED image back to ORIGINAL-image
    space. `angle_degrees` is the exact angle passed to Pillow's
    `Image.rotate()` to produce the preprocessed image (Pillow's own
    counter-clockwise-positive convention); `original_size`/`processed_size`
    are each image's (width, height) — needed because `rotate(...,
    expand=True)` changes canvas size, so the reverse rotation must pivot
    each side around its own center, not a shared one.
    """

    angle_degrees: float
    original_size: tuple[int, int]
    processed_size: tuple[int, int]


def map_point_to_original(transform: AffineTransform, x: float, y: float) -> tuple[float, float]:
    """Undo `preprocess_page`'s rotation: a point in preprocessed-image
    space maps back to the point in the original image it corresponds to.
    Verified empirically against Pillow's actual `rotate(angle,
    expand=True)` behavior (not just derived on paper) — see the module's
    own round-trip test.
    """
    ow, oh = transform.original_size
    pw, ph = transform.processed_size
    theta = radians(transform.angle_degrees)
    cx_p, cy_p = pw / 2, ph / 2
    cx_o, cy_o = ow / 2, oh / 2
    dx, dy = x - cx_p, y - cy_p
    rx = dx * cos(theta) - dy * sin(theta)
    ry = dx * sin(theta) + dy * cos(theta)
    return (rx + cx_o, ry + cy_o)


def _estimate_skew_degrees(gray: Image.Image, *, angle_range: float = 5.0, step: float = 0.5) -> float:
    """A coarse, dependency-light skew estimate: downsample to a small
    thumbnail, try a handful of small rotation angles, and pick the one
    whose row-wise pixel-sum profile has the highest variance — printed
    text lines align into sharp light/dark bands at the correct
    deskew angle, blurring together at the wrong one. Pure Python (no
    numpy) since the thumbnail is tiny; not a substitute for a real
    projection-profile or Hough-based deskew at production accuracy — see
    the module docstring's note on swapping the operator, not the
    contract, when a better one is available.
    """
    thumb = gray.copy()
    thumb.thumbnail((200, 200))
    best_angle, best_variance = 0.0, -1.0
    angle = -angle_range
    while angle <= angle_range + 1e-9:
        rotated = thumb.rotate(angle, fillcolor=255)
        row_sums = [
            ImageStat.Stat(rotated.crop((0, y, rotated.width, y + 1))).sum[0]
            for y in range(rotated.height)
        ]
        mean = sum(row_sums) / len(row_sums)
        variance = sum((v - mean) ** 2 for v in row_sums) / len(row_sums)
        if variance > best_variance:
            best_variance, best_angle = variance, angle
        angle += step
    return best_angle


def preprocess_page(image_bytes: bytes) -> tuple[bytes, AffineTransform]:
    """Deskew + denoise + binarize `image_bytes` (any Pillow-readable
    format). Returns the preprocessed image as PNG bytes and the
    transform mapping preprocessed-space coordinates back to the
    original's. Retention (where the PNG bytes get stored, how they're
    linked to the source `Page` row) is the caller's job.
    """
    original = Image.open(io.BytesIO(image_bytes))
    original_size = original.size
    gray = original.convert("L")
    denoised = gray.filter(ImageFilter.MedianFilter(size=3))

    skew = _estimate_skew_degrees(denoised)
    applied_angle = -skew  # rotate opposite the detected skew to straighten it
    deskewed = denoised.rotate(applied_angle, expand=True, fillcolor=255)

    # Fixed-threshold binarization — reasonable for a printed/typed page;
    # an adaptive threshold is the natural upgrade for handwritten pages
    # (module docstring's "drop-in operator swap").
    binarized = deskewed.point(lambda p: 255 if p > 200 else 0, mode="L")

    buf = io.BytesIO()
    binarized.save(buf, format="PNG")
    transform = AffineTransform(angle_degrees=applied_angle, original_size=original_size, processed_size=binarized.size)
    return buf.getvalue(), transform
