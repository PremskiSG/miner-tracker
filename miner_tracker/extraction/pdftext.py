"""Document -> text for providers without native document input (DeepSeek).

Handles PDF (pdfplumber), HTML/RNS announcements (LSE/AIM), and ESEF .zip
packages (LSE annual reports — a single large iXBRL .xhtml inside). Annual
reports are filtered to reserves-relevant regions to stay inside the context
window.
"""
from __future__ import annotations

import html as _html
import logging
import re
import zipfile
from pathlib import Path

logger = logging.getLogger("miner_tracker.pdftext")

_RESERVE_HINTS = re.compile(
    r"reserve|resource|measured|indicated|inferred|proven|probable|mineral|"
    r"tonnage|g/t|grade", re.IGNORECASE)

MAX_CHARS = 250_000


def doc_to_text(path: Path, doc_type: str) -> str:
    """Extract plain text from a filing regardless of format (.pdf/.html/.zip)."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        return pdf_to_text(path, doc_type)
    if ext in (".html", ".htm", ".xhtml"):
        return _finalize(html_to_text(path.read_text(encoding="utf-8", errors="ignore")),
                         path, doc_type)
    if ext == ".zip":
        return _finalize(_zip_to_text(path), path, doc_type)
    raise ValueError(f"unsupported filing format: {path.name}")


def html_to_text(raw: str) -> str:
    """Strip HTML/iXBRL to readable text, preserving table/row breaks."""
    raw = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?is)</td>", " | ", raw)
    raw = re.sub(r"(?is)</(p|div|tr|h[1-6]|li|table|th)>", "\n", raw)
    txt = _html.unescape(re.sub(r"(?s)<[^>]+>", "", raw))
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n[ \t]*\n+", "\n", txt)
    return txt.strip()


def _zip_to_text(path: Path) -> str:
    """ESEF package: extract the largest embedded XHTML/HTML (the iXBRL report)."""
    with zipfile.ZipFile(path) as z:
        docs = [n for n in z.namelist()
                if n.lower().endswith((".xhtml", ".html", ".htm"))]
        if not docs:
            raise ValueError(f"no XHTML in ESEF package {path.name}")
        main = max(docs, key=lambda n: z.getinfo(n).file_size)
        return html_to_text(z.read(main).decode("utf-8", errors="ignore"))


def _finalize(text: str, path: Path, doc_type: str) -> str:
    """Reserve-filter (annuals) + char cap, shared by html/zip paths."""
    if doc_type == "annual_report" and len(text) > MAX_CHARS:
        # keep only reserve-relevant windows so a huge annual fits the budget
        keep = []
        for m in _RESERVE_HINTS.finditer(text):
            keep.append((max(0, m.start() - 400), m.end() + 400))
        if keep:
            merged, cs, ce = [], *keep[0]
            for s, e in keep[1:]:
                if s <= ce:
                    ce = max(ce, e)
                else:
                    merged.append((cs, ce)); cs, ce = s, e
            merged.append((cs, ce))
            text = "\n...\n".join(text[s:e] for s, e in merged)
            logger.info("%s: reserve-filtered to %d chars", path.name, len(text))
    if len(text) > MAX_CHARS:
        logger.warning("%s: text truncated %d -> %d chars", path.name,
                       len(text), MAX_CHARS)
        text = text[:MAX_CHARS]
    return text


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
