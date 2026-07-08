"""Walk the filings directory, dedupe into the documents table, run extraction
on pending docs, and write metric/reserve rows with provenance."""
from __future__ import annotations

import calendar
import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from miner_tracker import db
from miner_tracker.config import companies, extraction_settings, filings_dir
from miner_tracker.extraction import extractor
from miner_tracker.extraction.schemas import METRIC_DEFS

logger = logging.getLogger("miner_tracker.pipeline")

_KIND_MAP = {
    "interim-report": "interim_report",
    "half-year-report": "interim_report",
    "financial-statement-release": "fs_release",
    "annual-report": "annual_report",
}

_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_([a-z\-]+)_[0-9a-f]+\.pdf$")

_CATEGORY_MAP = {
    "measured": "measured",
    "indicated": "indicated",
    "measured_indicated": "measured_indicated",
    "inferred": "inferred",
    "proven_probable": "pp",
}

_METAL_MAP = {"ag": "silver", "au": "gold", "pb": "lead", "zn": "zinc"}


def sync(conn) -> int:
    """Register all PDFs on disk into the documents table. Returns #new."""
    new = 0
    for c in companies():
        cid = db.upsert_company(conn, c["market"], c["ticker"], c["name"],
                                c["reporting_currency"], c.get("fx_pair"))
        folder = filings_dir() / f"{c['market']}_{c['ticker']}"
        if not folder.is_dir():
            logger.warning("no filings folder: %s", folder)
            continue
        for pdf in sorted(folder.glob("*.pdf")):
            m = _FILENAME_RE.match(pdf.name)
            if not m:
                logger.warning("unrecognized filename, skipping: %s", pdf.name)
                continue
            date, kind = m.group(1), m.group(2)
            doc_type = _KIND_MAP.get(kind)
            existing = conn.execute("SELECT 1 FROM documents WHERE path=?",
                                    (str(pdf),)).fetchone()
            if existing:
                continue
            sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
            dupe = conn.execute("SELECT path FROM documents WHERE sha256=?",
                                (sha,)).fetchone()
            if dupe:
                logger.info("duplicate content, skipping %s (same as %s)",
                            pdf.name, dupe["path"])
                continue
            doc_id = db.upsert_document(conn, cid, str(pdf), sha,
                                        doc_type or "other", date)
            if doc_type is None:
                conn.execute("UPDATE documents SET status='skipped' WHERE id=?",
                             (doc_id,))
            new += 1
    conn.commit()
    return new


def expected_period(published_date: str, doc_type: str) -> str:
    """Infer the report's fiscal quarter from the publication date."""
    d = datetime.strptime(published_date, "%Y-%m-%d")
    if doc_type == "fs_release":
        # FS releases come out Jan-Mar and cover the prior year's Q4
        year = d.year - 1 if d.month <= 6 else d.year
        return f"{year}-Q4"
    if d.month <= 2:
        return f"{d.year - 1}-Q4"
    quarter = {3: 1, 4: 1, 5: 1, 6: 2, 7: 2, 8: 2, 9: 3, 10: 3, 11: 3, 12: 4}[d.month]
    return f"{d.year}-Q{quarter}"


def _days_in_quarter(period: str) -> int:
    year, q = int(period[:4]), int(period[-1])
    months = {1: (1, 2, 3), 2: (4, 5, 6), 3: (7, 8, 9), 4: (10, 11, 12)}[q]
    return sum(calendar.monthrange(year, m)[1] for m in months)


def _write_metrics(conn, company_id: int, period: str, metrics: dict,
                   reporting_currency: str, doc_id: int, doc_review: bool) -> None:
    """Write one period's metric objects + derived rows."""
    values = {}
    for name, monetary in METRIC_DEFS.items():
        obj = metrics.get(name) or {}
        value = obj.get("value")
        if value is None:
            continue
        if name in ("reported_cost", "capex", "depreciation", "interest_expense"):
            # income statements show expenses as negatives; store magnitudes
            value = abs(float(value))
        currency = obj.get("currency")
        if monetary and not currency:
            currency = reporting_currency
        if not monetary and name != "silver_price_realized":
            # production/grade/% rows never carry a currency
            currency = None
        confidence = obj.get("confidence") or "low"
        needs_review = 1 if (doc_review or confidence == "low") else 0
        db.upsert_metric(conn, company_id, period, name, float(value), currency,
                         obj.get("unit"), doc_id, obj.get("page"), confidence,
                         is_derived=0, needs_review=needs_review)
        values[name] = (float(value), currency, confidence, obj.get("page"))

    # Derived: AISC = (operating cost + capex) / silver oz (the user's Excel
    # back-calc: quarterly cost block sums opex lines + capex, then / production)
    if "reported_cost" in values and values.get("silver_production_oz", (0,))[0]:
        cost, cost_ccy, cost_conf, _ = values["reported_cost"]
        capex = values.get("capex", (0.0,))[0] or 0.0
        oz = values["silver_production_oz"][0]
        conf = "low" if "low" in (cost_conf, values["silver_production_oz"][2]) else "medium"
        db.upsert_metric(conn, company_id, period, "aisc_derived",
                         (cost + capex) / oz, cost_ccy, "/oz", doc_id, None, conf,
                         is_derived=1, needs_review=1 if doc_review else 0)
    # Derived: tonnes/quarter -> tonnes/day
    if values.get("ore_milled_t", (0,))[0] and not period.endswith("FY"):
        t = values["ore_milled_t"][0]
        db.upsert_metric(conn, company_id, period, "tpd_derived",
                         t / _days_in_quarter(period), None, "t/d", doc_id, None,
                         values["ore_milled_t"][2], is_derived=1,
                         needs_review=1 if doc_review else 0)


def _extract_fn(provider: str):
    if provider == "deepseek":
        from miner_tracker.extraction import deepseek
        return deepseek.extract_pdf
    return extractor.extract_pdf


def process_doc(conn, doc, company_cfg: dict, model_override: str | None = None,
                dry_run: bool = False) -> bool:
    """Extract one document. Returns True on success."""
    settings = extraction_settings()
    provider = settings.get("provider", "anthropic")
    primary = model_override or settings.get("model", "claude-haiku-4-5")
    fallback = settings.get("fallback_model", "claude-sonnet-5")
    max_tokens = int(settings.get("max_tokens", 8192))
    max_pages = int(settings.get("max_pages_primary", 100))
    extract_fn = _extract_fn(provider)

    path = Path(doc["path"])
    pdf_bytes = path.read_bytes()
    pages = extractor.count_pdf_pages(pdf_bytes)
    conn.execute("UPDATE documents SET pages=? WHERE id=?", (pages, doc["id"]))

    model = primary
    # the >100-page cap only applies to Anthropic Haiku's native-PDF input
    if provider == "anthropic" and pages > max_pages and fallback:
        logger.info("%s: %d pages > %d, routing to %s", path.name, pages,
                    max_pages, fallback)
        model = fallback

    if dry_run:
        logger.info("[dry-run] would extract %s (%s) with %s/%s",
                    path.name, doc["doc_type"], provider, model)
        return True

    if provider == "anthropic" and fallback and fallback != model:
        attempts = [model, fallback]      # escalate to the stronger model
    else:
        attempts = [model, model]         # plain retry once
    tried = []
    for attempt_model in attempts:
        started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            result = extract_fn(path, doc["doc_type"], company_cfg["name"],
                                doc["published_date"], attempt_model, max_tokens)
        except Exception as e:  # API error or bad JSON -> try fallback once
            finished = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            db.log_run(conn, doc["id"], attempt_model, started, finished, None, None,
                       None, "failed", f"{type(e).__name__}: {e}", None)
            conn.commit()
            logger.warning("%s failed on %s: %s", path.name, attempt_model, e)
            tried.append(attempt_model)
            continue
        finished = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.log_run(conn, doc["id"], attempt_model, started, finished,
                   result.input_tokens, result.output_tokens, result.cost_usd,
                   "ok", None, result.raw_text)
        _store(conn, doc, company_cfg, result.data)
        conn.execute("UPDATE documents SET status='extracted' WHERE id=?", (doc["id"],))
        conn.commit()
        logger.info("%s: extracted with %s ($%.4f, %d+%d tokens)", path.name,
                    attempt_model, result.cost_usd, result.input_tokens,
                    result.output_tokens)
        return True

    conn.execute("UPDATE documents SET status='failed' WHERE id=?", (doc["id"],))
    conn.commit()
    return False


def _store(conn, doc, company_cfg: dict, data: dict) -> None:
    cid = doc["company_id"]
    currency = data.get("reporting_currency") or company_cfg["reporting_currency"]
    doc_review = currency.upper() != company_cfg["reporting_currency"].upper()
    if doc_review:
        logger.warning("%s: reporting currency %s != expected %s -> needs_review",
                       Path(doc["path"]).name, currency,
                       company_cfg["reporting_currency"])

    if doc["doc_type"] == "interim_report":
        p = data["period"]
        period = f"{p['year']}-Q{p['quarter']}"
        exp = expected_period(doc["published_date"], doc["doc_type"])
        if period != exp:
            logger.warning("%s: model period %s != expected %s -> needs_review",
                           Path(doc["path"]).name, period, exp)
            doc_review = True
        _write_metrics(conn, cid, period, data, currency, doc["id"], doc_review)
        conn.execute("UPDATE documents SET period=? WHERE id=?", (period, doc["id"]))

    elif doc["doc_type"] == "fs_release":
        year = data["fiscal_year"]
        exp = expected_period(doc["published_date"], "fs_release")
        if f"{year}-Q4" != exp:
            logger.warning("%s: fiscal year %s != expected %s -> needs_review",
                           Path(doc["path"]).name, year, exp)
            doc_review = True
        _write_metrics(conn, cid, f"{year}-Q4", data["q4"], currency, doc["id"],
                       doc_review)
        _write_metrics(conn, cid, f"{year}-FY", data["full_year"], currency,
                       doc["id"], doc_review)
        conn.execute("UPDATE documents SET period=? WHERE id=?",
                     (f"{year}-Q4", doc["id"]))

    elif doc["doc_type"] == "annual_report":
        for r in data.get("reserves", []):
            category = _CATEGORY_MAP.get(r["category"])
            if category is None or (r["tonnage_t"] is None and r["grade_gpt"] is None):
                continue
            metal = (r.get("metal") or "silver").lower()
            db.upsert_reserves(conn, cid, r["statement_date"], category,
                               r["tonnage_t"], r["grade_gpt"], doc["id"],
                               r.get("confidence"),
                               metal=_METAL_MAP.get(metal, metal))
        conn.execute("UPDATE documents SET period=? WHERE id=?",
                     (str(data["fiscal_year"]), doc["id"]))


def extract_pending(conn, company: str | None = None, doc_path: str | None = None,
                    model: str | None = None, force: bool = False,
                    dry_run: bool = False, limit: int | None = None,
                    doc_type: str | None = None) -> tuple[int, int]:
    """Run extraction over pending (or --force all) documents. Returns (ok, failed)."""
    cfg_by_key = {f"{c['market']}_{c['ticker']}": c for c in companies()}
    q = """SELECT d.*, c.market || '_' || c.ticker AS key FROM documents d
           JOIN companies c ON c.id = d.company_id
           WHERE d.doc_type != 'other'"""
    params: list = []
    if not force:
        q += " AND d.status IN ('pending', 'failed')"
    if doc_type:
        q += " AND d.doc_type = ?"
        params.append(doc_type)
    if company:
        q += " AND c.market || '_' || c.ticker = ?"
        params.append(company)
    if doc_path:
        q += " AND d.path = ?"
        params.append(str(Path(doc_path).resolve()))
    q += " ORDER BY d.published_date"
    docs = conn.execute(q, params).fetchall()
    if limit:
        docs = docs[:limit]

    ok = failed = 0
    for doc in docs:
        cfg = cfg_by_key.get(doc["key"])
        if cfg is None:
            logger.warning("no config for %s, skipping", doc["key"])
            continue
        if process_doc(conn, doc, cfg, model_override=model, dry_run=dry_run):
            ok += 1
        else:
            failed += 1
    return ok, failed
