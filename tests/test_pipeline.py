"""Pipeline unit tests — no network; extraction results are stubbed."""
import pytest

from miner_tracker import db
from miner_tracker.extraction import pipeline


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()


def _metric(value, currency=None, unit=None, page=3, confidence="high"):
    return {"value": value, "currency": currency, "unit": unit,
            "page": page, "confidence": confidence}


def _empty_metrics():
    from miner_tracker.extraction.schemas import METRIC_DEFS
    return {name: _metric(None) for name in METRIC_DEFS}


def test_expected_period():
    assert pipeline.expected_period("2025-07-31", "interim_report") == "2025-Q2"
    assert pipeline.expected_period("2025-04-29", "interim_report") == "2025-Q1"
    assert pipeline.expected_period("2025-10-23", "interim_report") == "2025-Q3"
    assert pipeline.expected_period("2026-02-20", "fs_release") == "2025-Q4"
    assert pipeline.expected_period("2022-01-15", "interim_report") == "2021-Q4"
    # ASX quarterly activities follow the same publication cadence
    assert pipeline.expected_period("2026-04-30", "quarterly_activities") == "2026-Q1"
    assert pipeline.expected_period("2026-01-30", "quarterly_activities") == "2025-Q4"


def test_asx_kind_mapping_and_filenames():
    cases = {
        "2026-04-30_quarterly-activities-report_03088979.pdf": "quarterly_activities",
        "2026-02-27_appendix-4d-and-2025-half-year-financial-report_03066761.pdf":
            "half_year_report",
        "2025-08-29_appendix-4e-preliminary-final-report_02988645.pdf": "fy_report",
        "2025-09-05_annual-report-to-shareholders_02991898.pdf": "annual_report",
        "2025-07-31_interim-report_01b25cd0dd.pdf": "interim_report",
    }
    for fname, expected in cases.items():
        m = pipeline._FILENAME_RE.match(fname)
        assert m, fname
        assert pipeline.doc_type_for_kind(m.group(2)) == expected, fname


def test_fin_period_label():
    assert pipeline.fin_period_label("half_year_report", "2025-12-31") == "2025-H2"
    assert pipeline.fin_period_label("half_year_report", "2024-06-30") == "2024-H1"
    assert pipeline.fin_period_label("fy_report", "2025-06-30") == "FY2025-06"
    assert pipeline.fin_period_label("fy_report", "garbage") is None


def test_store_quarterly_activities_gold(conn):
    cid = db.upsert_company(conn, "AU", "BCN", "Beacon Minerals", "AUD", "AUDUSD",
                            metal="gold")
    doc_id = db.upsert_document(conn, cid, "/x/2026-04-30_qar_ab.pdf", "sha4x",
                                "quarterly_activities", "2026-04-30")
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    data = _empty_metrics()
    data["period"] = {"year": 2026, "quarter": 1}
    data["reporting_currency"] = "AUD"
    data["gold_production_oz"] = _metric(5419, unit="oz")
    data["aisc_reported"] = _metric(4484, "AUD", "/oz")
    data["ore_milled_t"] = _metric(221155, unit="t")
    data["head_grade_gpt"] = _metric(0.86, unit="g/t")
    data["notes"] = None
    pipeline._store(conn, doc, {"name": "Beacon", "reporting_currency": "AUD",
                                "metal": "gold"}, data)
    rows = {r["metric"]: r for r in conn.execute(
        "SELECT * FROM quarterly_metrics WHERE period='2026-Q1'").fetchall()}
    assert rows["aisc_reported"]["value"] == 4484
    assert rows["aisc_reported"]["currency"] == "AUD"
    assert rows["gold_production_oz"]["currency"] is None
    assert rows["tpd_derived"]["value"] == pytest.approx(221155 / 90)  # Q1 2026


def test_store_half_year_report(conn):
    cid = db.upsert_company(conn, "AU", "BCN", "Beacon", "AUD", "AUDUSD", metal="gold")
    doc_id = db.upsert_document(conn, cid, "/x/2026-02-27_a4d_ab.pdf", "sha5x",
                                "half_year_report", "2026-02-27")
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    metrics = _empty_metrics()
    metrics["revenue"] = _metric(53_470_000, "AUD")
    data = {"period_end_date": "2025-12-31", "metrics": metrics, "notes": None}
    pipeline._store(conn, doc, {"name": "Beacon", "reporting_currency": "AUD",
                                "metal": "gold"}, data)
    row = conn.execute("SELECT period, value FROM quarterly_metrics "
                       "WHERE metric='revenue'").fetchone()
    assert row["period"] == "2025-H2"
    assert row["value"] == 53_470_000
    # half-year rows must not produce quarterly derived tpd or leak into -Q periods
    assert conn.execute("SELECT COUNT(*) n FROM quarterly_metrics "
                        "WHERE period LIKE '%-Q%'").fetchone()["n"] == 0


def _fake_doc(conn):
    cid = db.upsert_company(conn, "NORDIC", "SOSI1", "Sotkamo Silver", "SEK", "SEKUSD")
    doc_id = db.upsert_document(conn, cid, "/x/2025-07-31_interim-report_ab.pdf",
                                "sha1x", "interim_report", "2025-07-31")
    return conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()


def test_store_interim_writes_metrics_and_derived(conn):
    doc = _fake_doc(conn)
    data = _empty_metrics()
    data["period"] = {"year": 2025, "quarter": 2}
    data["reporting_currency"] = "SEK"
    data["revenue"] = _metric(110_000_000, "SEK")
    data["reported_cost"] = _metric(53_200_000, "SEK")
    data["capex"] = _metric(16_000_000, "SEK")
    data["silver_production_oz"] = _metric(255_000, unit="oz")
    data["silver_price_realized"] = _metric(39.4, "USD", "/oz")
    data["ore_milled_t"] = _metric(110_000, unit="t")
    data["notes"] = None

    cfg = {"name": "Sotkamo Silver", "reporting_currency": "SEK"}
    pipeline._store(conn, doc, cfg, data)

    rows = {r["metric"]: r for r in conn.execute(
        "SELECT * FROM quarterly_metrics WHERE period='2025-Q2'").fetchall()}
    assert rows["revenue"]["currency"] == "SEK"
    assert rows["silver_production_oz"]["currency"] is None
    assert rows["silver_price_realized"]["currency"] == "USD"
    # derived AISC = (opex + capex) / oz, matching the user's Excel back-calc
    assert rows["aisc_derived"]["value"] == pytest.approx(
        (53_200_000 + 16_000_000) / 255_000)
    assert rows["aisc_derived"]["currency"] == "SEK"
    assert rows["aisc_derived"]["is_derived"] == 1
    # derived tpd: Q2 has 91 days
    assert rows["tpd_derived"]["value"] == pytest.approx(110_000 / 91)
    # nothing flagged: period matches publication date, currency matches
    assert all(r["needs_review"] == 0 for r in rows.values())


def test_store_flags_period_mismatch(conn):
    doc = _fake_doc(conn)
    data = _empty_metrics()
    data["period"] = {"year": 2025, "quarter": 3}  # wrong: published 2025-07 => Q2
    data["reporting_currency"] = "SEK"
    data["revenue"] = _metric(1.0, "SEK")
    data["notes"] = None
    pipeline._store(conn, doc, {"name": "S", "reporting_currency": "SEK"}, data)
    row = conn.execute("SELECT * FROM quarterly_metrics").fetchone()
    assert row["needs_review"] == 1


def test_store_fs_release_separates_q4_and_fy(conn):
    cid = db.upsert_company(conn, "NORDIC", "SOSI1", "Sotkamo Silver", "SEK", "SEKUSD")
    doc_id = db.upsert_document(conn, cid, "/x/2026-02-20_fsr_ab.pdf", "sha2x",
                                "fs_release", "2026-02-20")
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    q4 = _empty_metrics()
    q4["revenue"] = _metric(186_000_000, "SEK")
    fy = _empty_metrics()
    fy["revenue"] = _metric(500_000_000, "SEK")
    data = {"fiscal_year": 2025, "reporting_currency": "SEK", "q4": q4,
            "full_year": fy, "notes": None}
    pipeline._store(conn, doc, {"name": "S", "reporting_currency": "SEK"}, data)
    rows = {r["period"]: r["value"] for r in conn.execute(
        "SELECT period, value FROM quarterly_metrics WHERE metric='revenue'")}
    assert rows == {"2025-Q4": 186_000_000, "2025-FY": 500_000_000}


def test_store_annual_reserves(conn):
    cid = db.upsert_company(conn, "NORDIC", "SOSI1", "Sotkamo Silver", "SEK", "SEKUSD")
    doc_id = db.upsert_document(conn, cid, "/x/2026-03-31_ar_ab.pdf", "sha3x",
                                "annual_report", "2026-03-31")
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    data = {"fiscal_year": 2025, "notes": None, "reserves": [
        {"statement_date": "2026-01-01", "category": "measured", "metal": "Silver",
         "tonnage_t": 7_335_000, "grade_gpt": 58, "page": 40, "confidence": "high"},
        {"statement_date": "2026-01-01", "category": "proven_probable",
         "metal": "silver", "tonnage_t": 1_598_000, "grade_gpt": 84.7, "page": 40,
         "confidence": "high"},
    ]}
    pipeline._store(conn, doc, {"name": "S", "reporting_currency": "SEK"}, data)
    rows = conn.execute("SELECT * FROM reserves_statements ORDER BY category").fetchall()
    assert [r["category"] for r in rows] == ["measured", "pp"]
    assert rows[1]["grade_gpt"] == 84.7
    assert all(r["metal"] == "silver" for r in rows)
