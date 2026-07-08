"""SQLite schema + write helpers. Reported-currency values are stored as-is;
USD conversion happens at read time via the v_metrics_usd view + fx_rates.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from miner_tracker.paths import db_path

DDL = """
CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY,
  market TEXT NOT NULL,
  ticker TEXT NOT NULL,
  name TEXT NOT NULL,
  reporting_currency TEXT NOT NULL,
  fx_pair TEXT,
  UNIQUE(market, ticker)
);

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  path TEXT NOT NULL UNIQUE,
  sha256 TEXT NOT NULL UNIQUE,
  doc_type TEXT NOT NULL,          -- interim_report | fs_release | annual_report
  published_date TEXT,
  period TEXT,                     -- '2025-Q3' once known
  pages INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending | extracted | failed | skipped
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS quarterly_metrics (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  period TEXT NOT NULL,            -- '2025-Q3' (or '2025-FY' for full-year rows)
  metric TEXT NOT NULL,
  value REAL,
  currency TEXT,                   -- NULL for oz / g/t / % / tonnes
  unit TEXT,
  source_doc_id INTEGER REFERENCES documents(id),
  source_page INTEGER,
  confidence TEXT,                 -- high | medium | low | manual
  is_derived INTEGER NOT NULL DEFAULT 0,
  needs_review INTEGER NOT NULL DEFAULT 0,
  extracted_at TEXT,
  UNIQUE(company_id, period, metric)
);

CREATE TABLE IF NOT EXISTS reserves_statements (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  statement_date TEXT NOT NULL,
  category TEXT NOT NULL,          -- measured | indicated | inferred | pp
  metal TEXT NOT NULL DEFAULT 'silver',
  tonnage REAL,
  grade_gpt REAL,
  source_doc_id INTEGER REFERENCES documents(id),
  confidence TEXT,
  UNIQUE(company_id, statement_date, category, metal)
);

CREATE TABLE IF NOT EXISTS fx_rates (
  pair TEXT NOT NULL,              -- 'SEKUSD' = USD per 1 SEK (multiply to convert)
  period TEXT NOT NULL,            -- '2025-Q3'
  rate REAL NOT NULL,
  PRIMARY KEY (pair, period)
);

CREATE TABLE IF NOT EXISTS npv_scenarios (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  name TEXT NOT NULL,
  assumptions_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(company_id, name)
);

CREATE TABLE IF NOT EXISTS extraction_runs (
  id INTEGER PRIMARY KEY,
  doc_id INTEGER NOT NULL REFERENCES documents(id),
  model TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  input_tokens INTEGER,
  output_tokens INTEGER,
  cost_usd REAL,
  status TEXT,                     -- ok | failed
  error TEXT,
  raw_response_json TEXT
);

CREATE VIEW IF NOT EXISTS v_metrics_usd AS
SELECT m.*,
       c.market, c.ticker, c.name AS company_name,
       CASE WHEN m.currency IS NULL OR m.currency = 'USD' THEN m.value
            ELSE m.value * f.rate END AS value_usd
FROM quarterly_metrics m
JOIN companies c ON c.id = m.company_id
LEFT JOIN fx_rates f ON f.pair = m.currency || 'USD' AND f.period = m.period;
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(DDL)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upsert_company(conn, market: str, ticker: str, name: str,
                   reporting_currency: str, fx_pair: str | None) -> int:
    conn.execute(
        """INSERT INTO companies(market, ticker, name, reporting_currency, fx_pair)
           VALUES(?,?,?,?,?)
           ON CONFLICT(market, ticker) DO UPDATE SET
             name=excluded.name, reporting_currency=excluded.reporting_currency,
             fx_pair=excluded.fx_pair""",
        (market, ticker, name, reporting_currency, fx_pair))
    row = conn.execute("SELECT id FROM companies WHERE market=? AND ticker=?",
                       (market, ticker)).fetchone()
    return row["id"]


def upsert_document(conn, company_id: int, path: str, sha256: str, doc_type: str,
                    published_date: str | None) -> int:
    conn.execute(
        """INSERT INTO documents(company_id, path, sha256, doc_type, published_date)
           VALUES(?,?,?,?,?)
           ON CONFLICT(path) DO NOTHING""",
        (company_id, path, sha256, doc_type, published_date))
    row = conn.execute("SELECT id FROM documents WHERE path=?", (path,)).fetchone()
    return row["id"]


def upsert_metric(conn, company_id: int, period: str, metric: str, value: float | None,
                  currency: str | None, unit: str | None, source_doc_id: int | None,
                  source_page: int | None, confidence: str | None,
                  is_derived: int = 0, needs_review: int = 0) -> bool:
    """Insert/update one metric value. Rows with confidence='manual' are never
    overwritten. Returns True if a row was written."""
    existing = conn.execute(
        "SELECT confidence FROM quarterly_metrics WHERE company_id=? AND period=? AND metric=?",
        (company_id, period, metric)).fetchone()
    if existing and existing["confidence"] == "manual":
        return False
    conn.execute(
        """INSERT INTO quarterly_metrics(company_id, period, metric, value, currency, unit,
             source_doc_id, source_page, confidence, is_derived, needs_review, extracted_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(company_id, period, metric) DO UPDATE SET
             value=excluded.value, currency=excluded.currency, unit=excluded.unit,
             source_doc_id=excluded.source_doc_id, source_page=excluded.source_page,
             confidence=excluded.confidence, is_derived=excluded.is_derived,
             needs_review=excluded.needs_review, extracted_at=excluded.extracted_at""",
        (company_id, period, metric, value, currency, unit, source_doc_id,
         source_page, confidence, is_derived, needs_review, _now()))
    return True


def upsert_reserves(conn, company_id: int, statement_date: str, category: str,
                    tonnage: float | None, grade_gpt: float | None,
                    source_doc_id: int | None, confidence: str | None,
                    metal: str = "silver") -> None:
    conn.execute(
        """INSERT INTO reserves_statements(company_id, statement_date, category, metal,
             tonnage, grade_gpt, source_doc_id, confidence)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(company_id, statement_date, category, metal) DO UPDATE SET
             tonnage=excluded.tonnage, grade_gpt=excluded.grade_gpt,
             source_doc_id=excluded.source_doc_id, confidence=excluded.confidence""",
        (company_id, statement_date, category, metal, tonnage, grade_gpt,
         source_doc_id, confidence))


def set_fx(conn, pair: str, period: str, rate: float) -> None:
    conn.execute(
        """INSERT INTO fx_rates(pair, period, rate) VALUES(?,?,?)
           ON CONFLICT(pair, period) DO UPDATE SET rate=excluded.rate""",
        (pair, period, rate))


def save_scenario(conn, company_id: int, name: str, assumptions: dict) -> None:
    conn.execute(
        """INSERT INTO npv_scenarios(company_id, name, assumptions_json, updated_at)
           VALUES(?,?,?,?)
           ON CONFLICT(company_id, name) DO UPDATE SET
             assumptions_json=excluded.assumptions_json, updated_at=excluded.updated_at""",
        (company_id, name, json.dumps(assumptions), _now()))


def load_scenarios(conn, company_id: int) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT name, assumptions_json FROM npv_scenarios WHERE company_id=? ORDER BY name",
        (company_id,)).fetchall()
    return {r["name"]: json.loads(r["assumptions_json"]) for r in rows}


def log_run(conn, doc_id: int, model: str, started_at: str, finished_at: str,
            input_tokens: int | None, output_tokens: int | None, cost_usd: float | None,
            status: str, error: str | None, raw_response_json: str | None) -> None:
    conn.execute(
        """INSERT INTO extraction_runs(doc_id, model, started_at, finished_at,
             input_tokens, output_tokens, cost_usd, status, error, raw_response_json)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (doc_id, model, started_at, finished_at, input_tokens, output_tokens,
         cost_usd, status, error, raw_response_json))
