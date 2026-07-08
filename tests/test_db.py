import sqlite3

import pytest

from miner_tracker import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()


def test_company_upsert_idempotent(conn):
    a = db.upsert_company(conn, "NORDIC", "SOSI1", "Sotkamo Silver", "SEK", "SEKUSD")
    b = db.upsert_company(conn, "NORDIC", "SOSI1", "Sotkamo Silver AB", "SEK", "SEKUSD")
    assert a == b
    row = conn.execute("SELECT name FROM companies WHERE id=?", (a,)).fetchone()
    assert row["name"] == "Sotkamo Silver AB"


def test_document_upsert(conn):
    cid = db.upsert_company(conn, "NORDIC", "SOSI1", "Sotkamo", "SEK", "SEKUSD")
    d1 = db.upsert_document(conn, cid, "/x/a.pdf", "abc", "interim_report", "2025-07-31")
    d2 = db.upsert_document(conn, cid, "/x/a.pdf", "abc", "interim_report", "2025-07-31")
    assert d1 == d2


def test_metric_upsert_and_manual_protection(conn):
    cid = db.upsert_company(conn, "NORDIC", "SOSI1", "Sotkamo", "SEK", "SEKUSD")
    assert db.upsert_metric(conn, cid, "2025-Q3", "revenue", 110_000_000, "SEK", None,
                            None, 5, "high")
    # normal re-extraction overwrites
    assert db.upsert_metric(conn, cid, "2025-Q3", "revenue", 111_000_000, "SEK", None,
                            None, 5, "high")
    # manual value is protected
    db.upsert_metric(conn, cid, "2025-Q3", "revenue", 112_000_000, "SEK", None,
                     None, None, "manual")
    assert not db.upsert_metric(conn, cid, "2025-Q3", "revenue", 999.0, "SEK", None,
                                None, 5, "high")
    row = conn.execute(
        "SELECT value, confidence FROM quarterly_metrics WHERE metric='revenue'").fetchone()
    assert row["value"] == 112_000_000
    assert row["confidence"] == "manual"


def test_usd_view_converts_by_metric_currency(conn):
    cid = db.upsert_company(conn, "NORDIC", "SOSI1", "Sotkamo", "SEK", "SEKUSD")
    db.set_fx(conn, "SEKUSD", "2025-Q3", 0.104)
    db.upsert_metric(conn, cid, "2025-Q3", "revenue", 110_000_000, "SEK", None, None, 5, "high")
    db.upsert_metric(conn, cid, "2025-Q3", "silver_price_realized", 41.0, "USD", "/oz", None, 5, "high")
    db.upsert_metric(conn, cid, "2025-Q3", "silver_production_oz", 255_000, None, "oz", None, 5, "high")
    rows = {r["metric"]: r["value_usd"] for r in
            conn.execute("SELECT metric, value_usd FROM v_metrics_usd").fetchall()}
    assert rows["revenue"] == pytest.approx(110_000_000 * 0.104)
    assert rows["silver_price_realized"] == 41.0          # already USD
    assert rows["silver_production_oz"] == 255_000        # unit-less passthrough
    # missing FX rate -> value_usd is NULL, not wrong
    db.upsert_metric(conn, cid, "2024-Q1", "revenue", 84_500_000, "SEK", None, None, 5, "high")
    row = conn.execute(
        "SELECT value_usd FROM v_metrics_usd WHERE period='2024-Q1'").fetchone()
    assert row["value_usd"] is None


def test_scenario_roundtrip(conn):
    cid = db.upsert_company(conn, "NORDIC", "SOSI1", "Sotkamo", "SEK", "SEKUSD")
    db.save_scenario(conn, cid, "spot", {"globals": {"payability": 1.18}, "years": []})
    db.save_scenario(conn, cid, "spot", {"globals": {"payability": 1.20}, "years": []})
    scen = db.load_scenarios(conn, cid)
    assert list(scen) == ["spot"]
    assert scen["spot"]["globals"]["payability"] == 1.20
