"""Run TWO extraction models over one company's documents (no DB writes) and
produce a side-by-side comparison: cost, latency, extraction completeness, and
field-level diffs.

Usage:
  compare_providers.py --company AU_BCN \\
     --a deepseek:deepseek-v4-pro --b deepseek:deepseek-v4-flash

Each --a/--b is "provider:model" where provider is 'anthropic' or 'deepseek'.
Defaults reproduce the original Haiku-vs-DeepSeek-pro run. Raw responses land in
data/compare/{doc}.{label}.json; the report is saved to data/compare/report.txt.
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

_FNS = {"anthropic": extractor.extract_pdf, "deepseek": deepseek.extract_pdf}
OUT = Path(__file__).resolve().parent.parent / "data" / "compare"


def parse_spec(spec: str) -> tuple[str, str, str]:
    provider, model = spec.split(":", 1)
    if provider not in _FNS:
        raise SystemExit(f"unknown provider {provider!r} (anthropic|deepseek)")
    return model, provider, model  # label, provider, model


def n_populated(data: dict, doc_type: str) -> int:
    """Count non-null extracted fields — a proxy for extraction completeness."""
    return sum(1 for v in metric_values(data, doc_type).values() if v is not None)


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
            key = (f"{r['statement_date']}|{(r.get('project') or '').lower()}"
                   f"|{r['category']}|{(r.get('metal') or '').lower()}")
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
    ap.add_argument("--a", default="anthropic:claude-haiku-4-5")
    ap.add_argument("--b", default="deepseek:deepseek-v4-pro")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    la, pa, ma = parse_spec(args.a)
    lb, pb, mb = parse_spec(args.b)
    specs = [(la, _FNS[pa], ma), (lb, _FNS[pb], mb)]

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

    emit(f"# {args.company}: A={la}  vs  B={lb}")
    totals = {la: {"cost": 0.0, "sec": 0.0, "fail": 0, "fields": 0},
              lb: {"cost": 0.0, "sec": 0.0, "fail": 0, "fields": 0}}
    agree = disagree = only_a = only_b = 0

    for doc in docs:
        name = Path(doc["path"]).name
        emit(f"\n=== {name} ({doc['doc_type']})")
        results = {}
        for label, fn, model in specs:
            t0 = time.monotonic()
            try:
                res = fn(Path(doc["path"]), doc["doc_type"], cfg["name"],
                         doc["published_date"], model,
                         metal=cfg.get("metal", "silver"))
                dt = time.monotonic() - t0
                totals[label]["cost"] += res.cost_usd
                totals[label]["sec"] += dt
                pop = n_populated(res.data, doc["doc_type"])
                totals[label]["fields"] += pop
                results[label] = res.data
                (OUT / f"{Path(doc['path']).stem}.{label}.json").write_text(
                    json.dumps(res.data, indent=1))
                emit(f"  {label:20} ok   {dt:5.1f}s  ${res.cost_usd:.4f}  "
                     f"{pop:2} fields  ({res.input_tokens}+{res.output_tokens} tok)")
            except Exception as e:
                dt = time.monotonic() - t0
                totals[label]["fail"] += 1
                totals[label]["sec"] += dt
                emit(f"  {label:20} FAIL {dt:5.1f}s  {type(e).__name__}: {e}")
        if len(results) < 2:
            continue
        va = metric_values(results[la], doc["doc_type"])
        vb = metric_values(results[lb], doc["doc_type"])
        for field in sorted(set(va) | set(vb)):
            a, b = va.get(field), vb.get(field)
            if a is None and b is None:
                continue
            if close(a, b):
                agree += 1
            elif a is None:
                only_b += 1
                emit(f"    ~ {field}: {la}=None  {lb}={b}   <-- ONLY B")
            elif b is None:
                only_a += 1
                emit(f"    ~ {field}: {la}={a}  {lb}=None   <-- ONLY A")
            else:
                disagree += 1
                emit(f"    ! {field}: {la}={a}  {lb}={b}   <-- DISAGREE")

    emit("\n=== TOTALS")
    for label, t in totals.items():
        emit(f"  {label:20} cost ${t['cost']:.3f}  time {t['sec']:.0f}s  "
             f"failures {t['fail']}  fields extracted {t['fields']}")
    emit(f"  {agree} agree, {disagree} disagree, "
         f"{only_a} only-{la}, {only_b} only-{lb}")
    (OUT / f"report_{args.company}.txt").write_text("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
