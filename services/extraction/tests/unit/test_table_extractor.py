"""Unit tests for P0 Ruled-Table Structure Detection and Cell Extraction (FR-OCR-03).

Tests:
1. Regular table (2x2 grid structure, coordinate mapping)
2. Multiple rows (3+ rows grid)
3. Multiple columns (3+ columns grid)
4. Merged cells (cell spanning across multiple column boundaries)
5. Missing cell text (empty cell preserved with 2D matrix topology)
6. Noisy table lines (clustering noisy coordinate cuts within tolerance)
"""
import pytest
from extraction.domain.ocr.interfaces import BoundingBox, OCRToken
from extraction.domain.ocr.table_extractor import RuledTableExtractor, TableCell, TableStructure, extract_table_cells


def test_regular_table_extraction():
    """1. Regular table with 2 rows and 2 columns."""
    extractor = RuledTableExtractor()

    # Define explicit horizontal and vertical line bounds
    h_lines = [10.0, 50.0, 90.0]  # 2 rows: [10-50], [50-90]
    v_lines = [10.0, 100.0, 200.0]  # 2 cols: [10-100], [100-200]

    tokens = [
        OCRToken(text="Khasra 101", confidence=0.98, bbox=BoundingBox(x=20.0, y=20.0, w=50.0, h=15.0)),
        OCRToken(text="Owner Ramesh", confidence=0.95, bbox=BoundingBox(x=110.0, y=20.0, w=70.0, h=15.0)),
        OCRToken(text="Khasra 102", confidence=0.97, bbox=BoundingBox(x=20.0, y=60.0, w=50.0, h=15.0)),
        OCRToken(text="Owner Suresh", confidence=0.96, bbox=BoundingBox(x=110.0, y=60.0, w=70.0, h=15.0)),
    ]

    table_struct = extractor.extract_table(
        ocr_tokens=tokens,
        horizontal_lines=h_lines,
        vertical_lines=v_lines,
    )

    assert table_struct.num_rows == 2
    assert table_struct.num_cols == 2
    assert len(table_struct.cells) == 4

    grid = table_struct.to_grid()
    assert grid[0][0].text == "Khasra 101"
    assert grid[0][1].text == "Owner Ramesh"
    assert grid[1][0].text == "Khasra 102"
    assert grid[1][1].text == "Owner Suresh"

    # Verify page coordinates preservation
    assert grid[0][0].bbox == BoundingBox(x=10.0, y=10.0, w=90.0, h=40.0)


def test_multiple_rows_and_multiple_columns():
    """2. & 3. Multiple rows (3 rows) and multiple columns (4 columns)."""
    extractor = RuledTableExtractor()

    h_lines = [0.0, 30.0, 60.0, 90.0]  # 3 rows
    v_lines = [0.0, 50.0, 100.0, 150.0, 200.0]  # 4 cols

    tokens = [
        OCRToken(text=f"R{r}C{c}", confidence=0.9, bbox=BoundingBox(x=c * 50.0 + 10.0, y=r * 30.0 + 5.0, w=30.0, h=15.0))
        for r in range(3)
        for c in range(4)
    ]

    table_struct = extractor.extract_table(ocr_tokens=tokens, horizontal_lines=h_lines, vertical_lines=v_lines)

    assert table_struct.num_rows == 3
    assert table_struct.num_cols == 4
    assert len(table_struct.cells) == 12

    grid = table_struct.to_grid()
    for r in range(3):
        for c in range(4):
            assert grid[r][c] is not None
            assert grid[r][c].text == f"R{r}C{c}"
            assert grid[r][c].row_index == r
            assert grid[r][c].col_index == c


def test_merged_cells_detection():
    """4. Merged cell spanning multiple columns."""
    extractor = RuledTableExtractor()

    h_lines = [10.0, 50.0, 90.0]
    v_lines = [10.0, 100.0, 200.0, 300.0]  # 3 columns

    # A header token spanning from x=20 to x=280 across column cuts at x=100 and x=200
    merged_token = OCRToken(
        text="Header Title Spanning Across All Columns",
        confidence=0.99,
        bbox=BoundingBox(x=20.0, y=20.0, w=260.0, h=20.0),
    )

    table_struct = extractor.extract_table(
        ocr_tokens=[merged_token],
        horizontal_lines=h_lines,
        vertical_lines=v_lines,
    )

    # First row cell should have col_span > 1
    cell_0_0 = table_struct.cells[0]
    assert cell_0_0.col_span > 1


def test_missing_cell_text():
    """5. Missing cell text: empty cell preserved in 2D topology."""
    extractor = RuledTableExtractor()

    h_lines = [0.0, 40.0, 80.0]
    v_lines = [0.0, 100.0, 200.0]

    # Only 1 token in R0C0; R0C1, R1C0, R1C1 are empty
    token = OCRToken(text="Only Entry", confidence=0.92, bbox=BoundingBox(x=10.0, y=10.0, w=50.0, h=20.0))

    table_struct = extractor.extract_table(ocr_tokens=[token], horizontal_lines=h_lines, vertical_lines=v_lines)

    grid = table_struct.to_grid()
    assert grid[0][0].text == "Only Entry"
    assert grid[0][1].text == ""  # Missing text preserved
    assert grid[1][0].text == ""
    assert grid[1][1].text == ""

    # Check 2D matrix topology dimensions are strictly preserved
    assert len(grid) == 2
    assert len(grid[0]) == 2


def test_noisy_table_lines():
    """6. Noisy table lines clustered within tolerance."""
    extractor = RuledTableExtractor(line_tolerance_px=10.0)

    # Noisy duplicate lines around y=50 (48.0, 50.0, 52.0) and y=100 (99.0, 101.0)
    noisy_h_lines = [10.0, 48.0, 50.0, 52.0, 99.0, 101.0]
    noisy_v_lines = [10.0, 12.0, 100.0, 103.0]

    table_struct = extractor.extract_table(
        ocr_tokens=[],
        horizontal_lines=noisy_h_lines,
        vertical_lines=noisy_v_lines,
    )

    # Clustered cuts should yield 2 rows and 1 column
    assert table_struct.num_rows == 2
    assert table_struct.num_cols == 1


def test_extract_table_cells_ocr_result_conversion():
    """Test extract_table_cells returns an OCRResult with ordered tokens and provenance."""
    res = extract_table_cells(page_id="page-tbl-123")
    assert res.engine_id == "table_extractor"
    assert res.page_id == "page-tbl-123"
    assert len(res.tokens) > 0
    assert "[R0C0]" in res.tokens[0].text
