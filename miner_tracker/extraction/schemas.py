"""Strict JSON schemas for structured-output extraction, one per document type.

Every metric is extracted as {value, currency, unit, page, confidence} so the
pipeline can store provenance and route low-confidence values to review.
Structured outputs don't support numeric min/max — sanity checks live in
pipeline.py post-parse.
"""
from __future__ import annotations

CONFIDENCE = {"type": "string", "enum": ["high", "medium", "low"]}


def _metric() -> dict:
    return {
        "type": "object",
        "properties": {
            "value": {"type": ["number", "null"]},
            "currency": {"type": ["string", "null"]},
            "unit": {"type": ["string", "null"]},
            "page": {"type": ["integer", "null"]},
            "confidence": CONFIDENCE,
        },
        "required": ["value", "currency", "unit", "page", "confidence"],
        "additionalProperties": False,
    }


# metric name -> is_monetary (monetary values default to the reporting currency
# when the model leaves currency null; non-monetary ones never carry a currency
# unless the model says so, e.g. realized price in USD inside SEK statements)
METRIC_DEFS: dict[str, bool] = {
    "revenue": True,
    "ebitda": True,
    "ebit": True,
    "net_income": True,
    "equity": True,
    "equity_ratio_pct": False,
    "personnel": False,
    "reported_cost": True,          # production/operating cost used for AISC back-calc
    "capex": True,
    "depreciation": True,
    "interest_expense": True,
    "cash": True,
    "debt": True,
    "silver_price_realized": True,  # per-oz price; often USD even in SEK statements
    "silver_production_oz": False,
    "gold_production_oz": False,
    "zinc_production_t": False,
    "lead_production_t": False,
    "head_grade_gpt": False,
    "ore_milled_t": False,
    "recovery_pct": False,
}


def _metrics_block() -> dict:
    return {name: _metric() for name in METRIC_DEFS}


_PERIOD = {
    "type": "object",
    "properties": {
        "year": {"type": "integer"},
        "quarter": {"type": "integer", "enum": [1, 2, 3, 4]},
    },
    "required": ["year", "quarter"],
    "additionalProperties": False,
}

INTERIM_SCHEMA = {
    "type": "object",
    "properties": {
        "period": _PERIOD,
        "reporting_currency": {"type": "string"},
        **_metrics_block(),
        "notes": {"type": ["string", "null"]},
    },
    "required": ["period", "reporting_currency", *METRIC_DEFS, "notes"],
    "additionalProperties": False,
}

_METRICS_OBJ = {
    "type": "object",
    "properties": _metrics_block(),
    "required": list(METRIC_DEFS),
    "additionalProperties": False,
}

FS_RELEASE_SCHEMA = {
    "type": "object",
    "properties": {
        "fiscal_year": {"type": "integer"},
        "reporting_currency": {"type": "string"},
        "q4": _METRICS_OBJ,
        "full_year": _METRICS_OBJ,
        "notes": {"type": ["string", "null"]},
    },
    "required": ["fiscal_year", "reporting_currency", "q4", "full_year", "notes"],
    "additionalProperties": False,
}

ANNUAL_SCHEMA = {
    "type": "object",
    "properties": {
        "fiscal_year": {"type": "integer"},
        "reserves": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement_date": {"type": "string"},   # YYYY-MM-DD or YYYY-MM
                    "category": {"type": "string",
                                 "enum": ["measured", "indicated", "inferred",
                                          "proven_probable", "measured_indicated"]},
                    "metal": {"type": "string"},
                    "tonnage_t": {"type": ["number", "null"]},
                    "grade_gpt": {"type": ["number", "null"]},
                    "page": {"type": ["integer", "null"]},
                    "confidence": CONFIDENCE,
                },
                "required": ["statement_date", "category", "metal", "tonnage_t",
                             "grade_gpt", "page", "confidence"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": ["string", "null"]},
    },
    "required": ["fiscal_year", "reserves", "notes"],
    "additionalProperties": False,
}

SCHEMAS = {
    "interim_report": INTERIM_SCHEMA,
    "fs_release": FS_RELEASE_SCHEMA,
    "annual_report": ANNUAL_SCHEMA,
}
