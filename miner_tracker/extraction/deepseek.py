"""DeepSeek extraction via DeepSeek's Anthropic-compatible endpoint (the same
pattern as the user's segment-inflection/llm_utils.py). No native PDF input,
so the PDF is converted to text first; no structured-outputs enforcement, so
the JSON is requested in the prompt and normalized post-parse."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import anthropic

from miner_tracker.extraction.extractor import ExtractionResult
from miner_tracker.extraction.pdftext import pdf_to_text
from miner_tracker.extraction.prompts import PROMPTS, SYSTEM
from miner_tracker.extraction.schemas import METRIC_DEFS, SCHEMAS
from miner_tracker.secrets import get_secret

logger = logging.getLogger("miner_tracker.deepseek")

DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"

# USD per MTok (input, output) — from the user's llm_utils registry
PRICES = {
    "deepseek-v4-pro": (0.435, 0.87),
    "deepseek-v4-flash": (0.14, 0.28),
}

_EMPTY_METRIC = {"value": None, "currency": None, "unit": None,
                 "page": None, "confidence": "low"}


def _client() -> anthropic.Anthropic:
    key = get_secret("deepseek", "api_key", env_var="DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError(
            "No DeepSeek key: set DEEPSEEK_API_KEY or add to secrets.yaml:\n"
            "deepseek:\n  api_key: \"...\"")
    base = get_secret("deepseek", "base_url", env_var="DEEPSEEK_BASE_URL") \
        or DEFAULT_BASE_URL
    return anthropic.Anthropic(api_key=key, base_url=base)


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in response")
    return json.loads(text[start:end + 1])


def _norm_metric(obj) -> dict:
    if obj is None:
        return dict(_EMPTY_METRIC)
    if isinstance(obj, (int, float)):
        return {**_EMPTY_METRIC, "value": float(obj), "confidence": "medium"}
    if isinstance(obj, dict):
        out = dict(_EMPTY_METRIC)
        for k in out:
            if obj.get(k) is not None:
                out[k] = obj[k]
        if not isinstance(out["value"], (int, float, type(None))):
            try:
                out["value"] = float(str(out["value"]).replace(",", ""))
            except ValueError:
                out["value"] = None
        if out["confidence"] not in ("high", "medium", "low"):
            out["confidence"] = "low"
        if out["page"] is not None:
            try:
                out["page"] = int(out["page"])
            except (TypeError, ValueError):
                out["page"] = None
        return out
    return dict(_EMPTY_METRIC)


def _norm_metrics_block(block) -> dict:
    block = block if isinstance(block, dict) else {}
    return {name: _norm_metric(block.get(name)) for name in METRIC_DEFS}


def normalize(data: dict, doc_type: str) -> dict:
    """Coerce a loosely-followed response into the strict schema shape."""
    if doc_type == "interim_report":
        period = data.get("period") or {}
        out = _norm_metrics_block(data)
        out["period"] = {"year": int(period.get("year", 0)),
                         "quarter": int(period.get("quarter", 0))}
        out["reporting_currency"] = data.get("reporting_currency") or ""
        out["notes"] = data.get("notes")
        return out
    if doc_type == "fs_release":
        return {
            "fiscal_year": int(data.get("fiscal_year", 0)),
            "reporting_currency": data.get("reporting_currency") or "",
            "q4": _norm_metrics_block(data.get("q4")),
            "full_year": _norm_metrics_block(data.get("full_year")),
            "notes": data.get("notes"),
        }
    if doc_type == "annual_report":
        reserves = []
        for r in data.get("reserves") or []:
            if not isinstance(r, dict):
                continue
            reserves.append({
                "statement_date": str(r.get("statement_date") or ""),
                "category": r.get("category") or "",
                "metal": r.get("metal") or "silver",
                "tonnage_t": r.get("tonnage_t"),
                "grade_gpt": r.get("grade_gpt"),
                "page": r.get("page"),
                "confidence": r.get("confidence")
                if r.get("confidence") in ("high", "medium", "low") else "low",
            })
        return {"fiscal_year": int(data.get("fiscal_year", 0)),
                "reserves": reserves, "notes": data.get("notes")}
    raise ValueError(f"unknown doc_type {doc_type}")


def extract_pdf(pdf_path: Path, doc_type: str, company_name: str,
                published_date: str, model: str, max_tokens: int = 8192) -> ExtractionResult:
    text = pdf_to_text(pdf_path, doc_type)
    if not text.strip():
        raise ValueError("PDF produced no extractable text")
    schema = SCHEMAS[doc_type]
    prompt = (
        PROMPTS[doc_type](company_name, published_date)
        + "\n\nRespond with ONLY a JSON object (no prose, no markdown fences) that "
        "conforms exactly to this JSON schema:\n"
        + json.dumps(schema)
        + "\n\nDocument text (page markers indicate PDF page numbers):\n\n"
        + text
    )
    client = _client()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM,
        thinking={"type": "disabled"},   # required by DeepSeek's endpoint
        messages=[{"role": "user", "content": prompt}],
    )
    raw = next((b.text for b in response.content if b.type == "text"), "")
    data = normalize(_parse_json(raw), doc_type)
    usage = response.usage
    inp, out = PRICES.get(model, (0.0, 0.0))
    return ExtractionResult(
        data=data, model=model,
        input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
        cost_usd=usage.input_tokens / 1e6 * inp + usage.output_tokens / 1e6 * out,
        raw_text=raw,
    )
