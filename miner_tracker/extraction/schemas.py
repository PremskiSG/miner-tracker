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
    "gold_price_realized": True,    # per-oz; may be A$/oz or US$/oz — state currency
    "silver_production_oz": False,
    "gold_production_oz": False,
    "gold_sold_oz": False,
    "zinc_production_t": False,
    "lead_production_t": False,
    "head_grade_gpt": False,
    "ore_mined_t": False,
    "ore_milled_t": False,
    "recovery_pct": False,
    "aisc_reported": True,          # per-oz, only when the report states AISC itself
    "cash_cost_per_oz": True,
}
# 'cash', 'debt', 'shares_outstanding' also live as stored metric names (the
# balance_sheet pass writes them via BALANCE_SHEET_SCHEMA, not this block).


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
                    "project": {"type": "string"},           # pit/deposit; "" if company-total
                    "category": {"type": "string",
                                 "enum": ["measured", "indicated", "inferred",
                                          "proven_probable", "measured_indicated",
                                          "proved", "probable"]},
                    "metal": {"type": "string"},
                    "tonnage_t": {"type": ["number", "null"]},
                    "grade_gpt": {"type": ["number", "null"]},
                    "page": {"type": ["integer", "null"]},
                    "confidence": CONFIDENCE,
                },
                "required": ["statement_date", "project", "category", "metal",
                             "tonnage_t", "grade_gpt", "page", "confidence"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": ["string", "null"]},
    },
    "required": ["fiscal_year", "reserves", "notes"],
    "additionalProperties": False,
}

# ASX-style: operational quarterly (same envelope as an interim), and
# half-year / preliminary-final financial reports identified by period end date.
FIN_PERIOD_SCHEMA = {
    "type": "object",
    "properties": {
        "period_end_date": {"type": "string"},   # YYYY-MM-DD
        "metrics": _METRICS_OBJ,
        "notes": {"type": ["string", "null"]},
    },
    "required": ["period_end_date", "metrics", "notes"],
    "additionalProperties": False,
}

# Balance-sheet pass (SEDAR financial-statements PDFs): just the three items
# needed for net debt + market cap that the MD&A doesn't carry.
BALANCE_SHEET_SCHEMA = {
    "type": "object",
    "properties": {
        "period_end_date": {"type": "string"},   # YYYY-MM-DD
        "cash": _metric(),
        "total_debt": _metric(),
        "shares_outstanding": _metric(),
        "notes": {"type": ["string", "null"]},
    },
    "required": ["period_end_date", "cash", "total_debt", "shares_outstanding",
                 "notes"],
    "additionalProperties": False,
}

# SEDAR annual MD&A: the Q4 quarter column AND the embedded mineral
# reserves/resources statement (Canadian MD&As restate it each year).
ANNUAL_MDA_SCHEMA = {
    "type": "object",
    "properties": {
        "period": _PERIOD,
        "reporting_currency": {"type": "string"},
        **_metrics_block(),
        "reserves": ANNUAL_SCHEMA["properties"]["reserves"],
        "notes": {"type": ["string", "null"]},
    },
    "required": ["period", "reporting_currency", *METRIC_DEFS, "reserves", "notes"],
    "additionalProperties": False,
}

SCHEMAS = {
    "interim_report": INTERIM_SCHEMA,
    "quarterly_activities": INTERIM_SCHEMA,
    "annual_mda": ANNUAL_MDA_SCHEMA,       # Q4 quarter column + reserves statement
    "balance_sheet": BALANCE_SHEET_SCHEMA,  # SEDAR FS: cash / total debt / shares
    "fs_release": FS_RELEASE_SCHEMA,
    "half_year_report": FIN_PERIOD_SCHEMA,
    "fy_report": FIN_PERIOD_SCHEMA,
    "annual_report": ANNUAL_SCHEMA,
}


def _deunion(node):
    """Anthropic structured outputs allow at most 16 union-typed parameters.
    Convert every nullable union ("type": [X, "null"]) into an OPTIONAL
    non-null field: absence then means what null meant. Returns
    (new_node, is_nullable)."""
    if isinstance(node, list):
        return [_deunion(x)[0] for x in node], False
    if not isinstance(node, dict):
        return node, False
    out = dict(node)
    nullable = False
    t = out.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        nullable = len(non_null) < len(t)
        out["type"] = non_null[0] if len(non_null) == 1 else non_null
    if "properties" in out:
        props = {}
        optional = set()
        for k, v in out["properties"].items():
            newv, is_nullable = _deunion(v)
            props[k] = newv
            if is_nullable:
                optional.add(k)
        out["properties"] = props
        out["required"] = [k for k in out.get("required", []) if k not in optional]
    if "items" in out:
        out["items"], _ = _deunion(out["items"])
    return out, nullable


def anthropic_schema(doc_type: str) -> dict:
    """Union-free variant for Claude structured outputs (omitted == null)."""
    schema, _ = _deunion(SCHEMAS[doc_type])
    return schema
