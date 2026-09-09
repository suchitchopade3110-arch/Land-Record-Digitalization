"""FR-TRI-06 — preprocessing hook. No DB needed."""
import io

from PIL import Image

from backend.domain.preprocessing import map_point_to_original, preprocess_page


def _marker_image(size, marker_xy):
    """A near-white image with one distinctive dark marker BLOCK (not a
    single pixel — a lone pixel is exactly what a median-filter denoise
    is supposed to erase as noise, which would make the marker vanish
    before `preprocess_page` even gets to its deskew/rotate step)."""
    img = Image.new("L", size, color=255)
    x, y = marker_xy
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            img.putpixel((x + dx, y + dy), 0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _find_darkest_pixel(png_bytes: bytes) -> tuple[int, int]:
    img = Image.open(io.BytesIO(png_bytes)).convert("L")
    best, best_val = None, 256
    for y in range(img.height):
        for x in range(img.width):
            v = img.getpixel((x, y))
            if v < best_val:
                best_val, best = v, (x, y)
    return best


def test_preprocess_page_returns_valid_png_bytes_and_a_transform():
    data = _marker_image((120, 80), (60, 40))
    processed_bytes, transform = preprocess_page(data)

    img = Image.open(io.BytesIO(processed_bytes))
    assert img.format == "PNG"
    assert transform.original_size == (120, 80)
    assert transform.processed_size == img.size


def test_the_coordinate_transform_maps_a_processed_point_back_to_the_original_FR_TRI_06():
    """The property that actually matters for FR-TRI-06: a bbox computed
    against the preprocessed image (e.g. by Shree's OCR, downstream) must
    still mean something in the original image's own coordinate space —
    "original" meaning `preprocess_page`'s own input, regardless of
    whatever rotation it decides to apply internally to straighten it.
    Tolerance is a few pixels — binarization can nudge the marker block's
    exact peak by a pixel or two; the transform math itself (verified
    separately below with no denoise/deskew involved) is exact.
    """
    original_size = (150, 100)
    marker = (110, 30)
    data = _marker_image(original_size, marker)

    processed_bytes, transform = preprocess_page(data)
    found = _find_darkest_pixel(processed_bytes)

    mapped_back = map_point_to_original(transform, *found)
    distance = ((mapped_back[0] - marker[0]) ** 2 + (mapped_back[1] - marker[1]) ** 2) ** 0.5
    assert distance < 6.0


def test_map_point_to_original_round_trips_a_known_rotation_exactly():
    """Isolates the transform math itself (no preprocess_page, no
    binarization/deskew-estimation noise) — a plain PIL rotate/expand,
    round-tripped through the same formula `preprocess_page` relies on."""
    original = Image.new("L", (100, 60), color=255)
    original.putpixel((70, 20), 0)

    angle = 12.0
    rotated = original.rotate(angle, expand=True, fillcolor=255)

    best, best_val = None, 256
    for y in range(rotated.height):
        for x in range(rotated.width):
            v = rotated.getpixel((x, y))
            if v < best_val:
                best_val, best = v, (x, y)

    from backend.domain.preprocessing import AffineTransform

    transform = AffineTransform(angle_degrees=angle, original_size=original.size, processed_size=rotated.size)
    mapped_back = map_point_to_original(transform, *best)
    assert abs(mapped_back[0] - 70) < 1.5
    assert abs(mapped_back[1] - 20) < 1.5
