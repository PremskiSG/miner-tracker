"""Shared response parsing/normalization for providers without server-enforced
schemas (both DeepSeek and the Claude path use prompt-embedded schemas — our
metric envelope exceeds structured-outputs' union/optional parameter limits)."""
from __future__ import annotations

import json
import re

from miner_tracker.extraction.schemas import METRIC_DEFS

_EMPTY_METRIC = {"value": None, "currency": None, "unit": None,
                 "page": None, "confidence": "low"}


def parse_json(text: str) -> dict:
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
    if doc_type in ("half_year_report", "fy_report"):
        return {
            "period_end_date": str(data.get("period_end_date") or ""),
            "metrics": _norm_metrics_block(data.get("metrics") or data),
            "notes": data.get("notes"),
        }
    if doc_type in ("interim_report", "quarterly_activities", "annual_mda"):
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
                "project": str(r.get("project") or ""),
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
