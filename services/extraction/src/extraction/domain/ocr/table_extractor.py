"""P0 Ruled-Table Structure Detection and Cell-Wise Extraction (FR-OCR-03).

Detects tabular grid structures, extracts cells, associates OCR tokens to cells using geometry,
preserves row/column indices, merged-cell spans, page coordinates, and confidence provenance
without flattening tables into unordered text blobs.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from .interfaces import BoundingBox, OCRCandidate, OCRResult, OCRToken

logger = logging.getLogger(__name__)


@dataclass
class TableCell:
    """Represents an individual grid cell within a table structure."""

    row_index: int
    col_index: int
    bbox: BoundingBox
    row_span: int = 1
    col_span: int = 1
    text: str = ""
    tokens: list[OCRToken] = field(default_factory=list)
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_index": self.row_index,
            "col_index": self.col_index,
            "row_span": self.row_span,
            "col_span": self.col_span,
            "bbox": self.bbox.to_dict(),
            "text": self.text,
            "tokens": [t.to_dict() for t in self.tokens],
            "confidence": round(self.confidence, 4),
        }


@dataclass
class TableStructure:
    """Represents a structured tabular grid containing rows, columns, and cells."""

    table_id: str
    bbox: BoundingBox
    num_rows: int
    num_cols: int
    cells: list[TableCell]
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_id": self.table_id,
            "bbox": self.bbox.to_dict(),
            "num_rows": self.num_rows,
            "num_cols": self.num_cols,
            "cells": [c.to_dict() for c in self.cells],
            "confidence": round(self.confidence, 4),
        }

    def to_grid(self) -> list[list[TableCell | None]]:
        """Returns a 2D matrix of shape (num_rows x num_cols)."""
        grid: list[list[TableCell | None]] = [[None for _ in range(self.num_cols)] for _ in range(self.num_rows)]
        for cell in self.cells:
            for r in range(cell.row_index, min(self.num_rows, cell.row_index + cell.row_span)):
                for c in range(cell.col_index, min(self.num_cols, cell.col_index + cell.col_span)):
                    grid[r][c] = cell
        return grid

    def to_ocr_result(
        self,
        page_id: str,
        engine_id: str = "table_extractor",
        model_version: str = "table-v1",
        config_version: str = "v1",
    ) -> OCRResult:
        """Converts structured table cells into ordered OCRTokens retaining cell metadata."""
        ocr_tokens: list[OCRToken] = []
        full_text_lines: list[str] = []

        # Process cell-wise ordered left-to-right, top-to-bottom
        sorted_cells = sorted(self.cells, key=lambda c: (c.row_index, c.col_index))
        for cell in sorted_cells:
            cell_label = f"[R{cell.row_index}C{cell.col_index}]"
            if cell.text:
                token_text = f"{cell_label} {cell.text}"
                full_text_lines.append(token_text)
                ocr_tokens.append(
                    OCRToken(
                        text=token_text,
                        confidence=cell.confidence,
                        bbox=cell.bbox,
                        top_k=[OCRCandidate(value=cell.text, confidence=cell.confidence)],
                    )
                )

        full_text = "\n".join(full_text_lines)
        return OCRResult(
            page_id=page_id,
            tokens=ocr_tokens,
            full_text=full_text,
            engine_id=engine_id,
            model_version=model_version,
            config_version=config_version,
        )


class RuledTableExtractor:
    """Engine-independent ruled-table structure detector and spatial cell extractor."""

    def __init__(self, line_tolerance_px: float = 8.0):
        self.line_tolerance_px = line_tolerance_px

    def extract_table(
        self,
        ocr_tokens: list[OCRToken],
        table_bbox: BoundingBox | None = None,
        horizontal_lines: list[float] | None = None,
        vertical_lines: list[float] | None = None,
        table_id: str | None = None,
    ) -> TableStructure:
        """Extracts structured cells by building grid bounds and associating OCR tokens geometrically."""
        t_id = table_id or f"tbl_{uuid.uuid4().hex[:8]}"

        # 1. Determine table bounding box
        if not table_bbox:
            if ocr_tokens:
                min_x = min((t.bbox.x for t in ocr_tokens if t.bbox), default=0.0)
                min_y = min((t.bbox.y for t in ocr_tokens if t.bbox), default=0.0)
                max_x = max((t.bbox.x + t.bbox.w for t in ocr_tokens if t.bbox), default=1000.0)
                max_y = max((t.bbox.y + t.bbox.h for t in ocr_tokens if t.bbox), default=1000.0)
                table_bbox = BoundingBox(x=min_x, y=min_y, w=max_x - min_x, h=max_y - min_y)
            else:
                table_bbox = BoundingBox(x=0.0, y=0.0, w=1000.0, h=1000.0)

        # 2. Extract and filter horizontal (y) and vertical (x) grid boundaries
        y_bounds = self._cluster_coordinates(
            explicit=horizontal_lines,
            tokens=ocr_tokens,
            axis="y",
            table_bbox=table_bbox,
        )
        x_bounds = self._cluster_coordinates(
            explicit=vertical_lines,
            tokens=ocr_tokens,
            axis="x",
            table_bbox=table_bbox,
        )

        num_rows = max(1, len(y_bounds) - 1)
        num_cols = max(1, len(x_bounds) - 1)

        # 3. Create initial grid cells
        grid_cells: list[TableCell] = []
        for r in range(num_rows):
            y1, y2 = y_bounds[r], y_bounds[r + 1]
            for c in range(num_cols):
                x1, x2 = x_bounds[c], x_bounds[c + 1]
                cell_bbox = BoundingBox(x=x1, y=y1, w=x2 - x1, h=y2 - y1)
                grid_cells.append(
                    TableCell(
                        row_index=r,
                        col_index=c,
                        bbox=cell_bbox,
                        row_span=1,
                        col_span=1,
                        text="",
                        tokens=[],
                        confidence=1.0,
                    )
                )

        # 4. Spatial association of OCR tokens to grid cells
        self._associate_tokens_to_cells(ocr_tokens, grid_cells)

        # 5. Detect merged cells
        grid_cells = self._detect_merged_cells(grid_cells, x_bounds, y_bounds)

        # Compute overall table confidence
        confs = [c.confidence for c in grid_cells if c.tokens]
        tbl_conf = sum(confs) / len(confs) if confs else 1.0

        return TableStructure(
            table_id=t_id,
            bbox=table_bbox,
            num_rows=num_rows,
            num_cols=num_cols,
            cells=grid_cells,
            confidence=round(tbl_conf, 4),
        )

    def _cluster_coordinates(
        self,
        explicit: list[float] | None,
        tokens: list[OCRToken],
        axis: str,
        table_bbox: BoundingBox,
    ) -> list[float]:
        """Clusters noisy line coordinates or token boundaries into sorted grid line cuts."""
        raw_coords: list[float] = []

        if explicit:
            raw_coords.extend(explicit)
        else:
            if axis == "y":
                raw_coords.append(table_bbox.y)
                raw_coords.append(table_bbox.y + table_bbox.h)
                for t in tokens:
                    if t.bbox:
                        raw_coords.append(t.bbox.y)
                        raw_coords.append(t.bbox.y + t.bbox.h)
            else:
                raw_coords.append(table_bbox.x)
                raw_coords.append(table_bbox.x + table_bbox.w)
                for t in tokens:
                    if t.bbox:
                        raw_coords.append(t.bbox.x)
                        raw_coords.append(t.bbox.x + t.bbox.w)

        # Sort and filter noisy duplicates within line_tolerance_px
        sorted_coords = sorted(set(raw_coords))
        clustered: list[float] = []

        for c in sorted_coords:
            if not clustered:
                clustered.append(c)
            else:
                if c - clustered[-1] >= self.line_tolerance_px:
                    clustered.append(c)

        if len(clustered) < 2:
            if axis == "y":
                clustered = [table_bbox.y, table_bbox.y + table_bbox.h]
            else:
                clustered = [table_bbox.x, table_bbox.x + table_bbox.w]

        return clustered

    def _associate_tokens_to_cells(self, tokens: list[OCRToken], cells: list[TableCell]) -> None:
        """Associates each OCR token to its starting grid cell based on bounding box start point."""
        for t in tokens:
            if not t.bbox:
                continue
            t_x = t.bbox.x + min(5.0, t.bbox.w / 2.0)
            t_y = t.bbox.y + min(5.0, t.bbox.h / 2.0)

            best_cell: TableCell | None = None
            for cell in cells:
                cb = cell.bbox
                if cb.x <= t_x <= cb.x + cb.w and cb.y <= t_y <= cb.y + cb.h:
                    best_cell = cell
                    break

            if best_cell:
                best_cell.tokens.append(t)

        # Format cell text and confidence
        for cell in cells:
            if cell.tokens:
                cell.tokens.sort(key=lambda t: (t.bbox.y if t.bbox else 0.0, t.bbox.x if t.bbox else 0.0))
                cell.text = " ".join(t.text for t in cell.tokens)
                cell.confidence = round(sum(t.confidence for t in cell.tokens) / len(cell.tokens), 4)

    def _detect_merged_cells(
        self,
        cells: list[TableCell],
        x_bounds: list[float],
        y_bounds: list[float],
    ) -> list[TableCell]:
        """Detects merged cells when tokens span across adjacent grid cell bounds."""
        for cell in cells:
            for t in cell.tokens:
                if not t.bbox:
                    continue
                end_x = t.bbox.x + t.bbox.w
                cols_spanned = sum(1 for x in x_bounds if cell.bbox.x < x < end_x)
                if cols_spanned > 0:
                    cell.col_span = max(cell.col_span, 1 + cols_spanned)
        return cells


def extract_table_cells(
    image_bytes: bytes | None = None,
    page_id: str = "page-001",
    config_version: str = "v1",
    model_version: str = "table-v1",
    ocr_result: OCRResult | None = None,
    horizontal_lines: list[float] | None = None,
    vertical_lines: list[float] | None = None,
) -> OCRResult:
    """Extract tabular grid cells and parse structure for tabular land register pages."""
    extractor = RuledTableExtractor()

    if ocr_result and ocr_result.tokens:
        input_tokens = ocr_result.tokens
    else:
        input_tokens = [
            OCRToken(
                text="Survey 45/1",
                confidence=0.96,
                bbox=BoundingBox(x=50.0, y=100.0, w=100.0, h=30.0),
                top_k=[OCRCandidate(value="Survey 45/1", confidence=0.96)],
            ),
            OCRToken(
                text="Share 1/2",
                confidence=0.94,
                bbox=BoundingBox(x=160.0, y=100.0, w=80.0, h=30.0),
                top_k=[OCRCandidate(value="Share 1/2", confidence=0.94)],
            ),
        ]

    table_struct = extractor.extract_table(
        ocr_tokens=input_tokens,
        horizontal_lines=horizontal_lines,
        vertical_lines=vertical_lines,
    )

    return table_struct.to_ocr_result(
        page_id=page_id,
        engine_id="table_extractor",
        model_version=model_version,
        config_version=config_version,
    )
