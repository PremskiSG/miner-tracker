"""PDF -> text for providers without native PDF input (DeepSeek).

Pages are wrapped in [page N] markers so the model can cite page numbers.
Annual reports are filtered to reserves-relevant pages to stay inside the
context window.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger("miner_tracker.pdftext")

_RESERVE_HINTS = re.compile(
    r"reserve|resource|measured|indicated|inferred|proven|probable|mineral|"
    r"tonnage|g/t|grade", re.IGNORECASE)

MAX_CHARS = 250_000


def pdf_to_text(pdf_path: Path, doc_type: str) -> str:
    import pdfplumber

    pages: list[tuple[int, str]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            try:
                text = page.extract_text(layout=True) or ""
            except Exception:
                text = page.extract_text() or ""
            # layout mode pads lines with trailing spaces and emits swathes of
            # blank lines — strip them (no information loss, big char savings)
            lines = [ln.rstrip() for ln in text.splitlines()]
            text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
            pages.append((i, text))

    if doc_type == "annual_report":
        keep = {i for i, t in pages if _RESERVE_HINTS.search(t)}
        # include neighbours for table continuations
        keep |= {i + 1 for i in keep} | {i - 1 for i in keep}
        filtered = [(i, t) for i, t in pages if i in keep]
        if filtered:
            logger.info("%s: kept %d/%d reserve-relevant pages",
                        pdf_path.name, len(filtered), len(pages))
            pages = filtered

    out = "\n\n".join(f"[page {i}]\n{t}" for i, t in pages if t.strip())
    if len(out) > MAX_CHARS:
        logger.warning("%s: text truncated %d -> %d chars", pdf_path.name,
                       len(out), MAX_CHARS)
        out = out[:MAX_CHARS]
    return out
