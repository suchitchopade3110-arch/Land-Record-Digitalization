"""Multi-pass blocking de-dup (M7) — a union of independent passes, not a
lookup. Any single key fails silently exactly when OCR got that field
wrong; that's why it's a union (FR-ENT-02)."""
