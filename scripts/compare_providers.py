"""Run BOTH extraction providers over one company's documents (no DB writes)
and produce a side-by-side comparison: cost, latency, and field-level diffs.

Usage: .venv/bin/python scripts/compare_providers.py [--company AU_BCN] [--limit N]

Raw responses land in data/compare/{doc}.{provider}.json; the diff report is
printed and saved to data/compare/report.txt.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from miner_tracker import db
from miner_tracker.config import companies
from miner_tracker.extraction import deepseek, extractor
from miner_tracker.extraction.schemas import METRIC_DEFS

PROVIDERS = {
    "haiku": (extractor.extract_pdf, "claude-haiku-4-5"),
    "deepseek": (deepseek.extract_pdf, "deepseek-v4-pro"),
}
OUT = Path(__file__).resolve().parent.parent / "data" / "compare"


def metric_values(data: dict, doc_type: str) -> dict[str, float | None]:
    """Flatten a result into {field: value} for diffing."""
    out: dict[str, float | None] = {}
    if doc_type in ("interim_report", "quarterly_activities"):
        out["period"] = f"{data['period']['year']}-Q{data['period']['quarter']}"
        block = data
    elif doc_type in ("half_year_report", "fy_report"):
        out["period"] = data.get("period_end_date")
        block = data.get("metrics", {})
    elif doc_type == "fs_release":
        for scope in ("q4", "full_year"):
            for name in METRIC_DEFS:
                v = (data.get(scope, {}).get(name) or {}).get("value")
                out[f"{scope}.{name}"] = v
        return out
    elif doc_type == "annual_report":
        for r in data.get("reserves", []):
            key = f"{r['statement_date']}|{r['category']}|{(r.get('metal') or '').lower()}"
            out[f"{key}|tonnage"] = r.get("tonnage_t")
            out[f"{key}|grade"] = r.get("grade_gpt")
        return out
    else:
        return out
    for name in METRIC_DEFS:
        out[name] = (block.get(name) or {}).get("value")
    return out


def close(a, b, rel=0.005) -> bool:
    if a is None or b is None:
        return a == b
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    if a == b:
        return True
    denom = max(abs(a), abs(b))
    return denom > 0 and abs(a - b) / denom <= rel


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", default="AU_BCN")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    market, ticker = args.company.split("_", 1)
    cfg = next(c for c in companies()
               if c["market"] == market and c["ticker"] == ticker)
    conn = db.connect()
    docs = conn.execute(
        """SELECT d.* FROM documents d JOIN companies c ON c.id=d.company_id
           WHERE c.market=? AND c.ticker=? AND d.doc_type != 'other'
           ORDER BY d.published_date""", (market, ticker)).fetchall()
    if args.limit:
        docs = docs[:args.limit]
    OUT.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []

    def emit(s: str = "") -> None:
        print(s, flush=True)
        lines.append(s)

    totals = {p: {"cost": 0.0, "sec": 0.0, "fail": 0} for p in PROVIDERS}
    agree = disagree = only_one = 0

    for doc in docs:
        name = Path(doc["path"]).name
        emit(f"\n=== {name} ({doc['doc_type']})")
        results = {}
        for pname, (fn, model) in PROVIDERS.items():
            t0 = time.monotonic()
            try:
                res = fn(Path(doc["path"]), doc["doc_type"], cfg["name"],
                         doc["published_date"], model,
                         metal=cfg.get("metal", "silver"))
                dt = time.monotonic() - t0
                totals[pname]["cost"] += res.cost_usd
                totals[pname]["sec"] += dt
                results[pname] = res.data
                (OUT / f"{Path(doc['path']).stem}.{pname}.json").write_text(
                    json.dumps(res.data, indent=1))
                emit(f"  {pname:9} ok   {dt:5.1f}s  ${res.cost_usd:.4f}  "
                     f"({res.input_tokens}+{res.output_tokens} tok)")
            except Exception as e:
                dt = time.monotonic() - t0
                totals[pname]["fail"] += 1
                totals[pname]["sec"] += dt
                emit(f"  {pname:9} FAIL {dt:5.1f}s  {type(e).__name__}: {e}")
        if len(results) < 2:
            continue
        va = metric_values(results["haiku"], doc["doc_type"])
        vb = metric_values(results["deepseek"], doc["doc_type"])
        for field in sorted(set(va) | set(vb)):
            a, b = va.get(field), vb.get(field)
            if a is None and b is None:
                continue
            if close(a, b):
                agree += 1
            elif a is None or b is None:
                only_one += 1
                emit(f"    ~ {field}: haiku={a}  deepseek={b}   <-- ONLY ONE")
            else:
                disagree += 1
                emit(f"    ! {field}: haiku={a}  deepseek={b}   <-- DISAGREE")

    emit("\n=== TOTALS")
    for pname, t in totals.items():
        emit(f"  {pname:9} cost ${t['cost']:.3f}  time {t['sec']:.0f}s  "
             f"failures {t['fail']}")
    emit(f"  fields: {agree} agree, {disagree} disagree, {only_one} only-one-found")
    (OUT / "report.txt").write_text("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
