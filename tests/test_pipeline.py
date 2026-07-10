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


def test_sedar_kind_mapping_and_filenames():
    cases = {
        "2026-05-28_interim-md-a_da-en-2eed.pdf": "interim_report",
        "2025-04-30_management-s-discussion-analysis-md-a_glish-0801.pdf": "annual_mda",
        # the paired financial-statements PDFs carry the balance sheet
        "2026-05-28_interim-financial-statements_rt-en-917c.pdf": "balance_sheet",
        "2026-04-30_financial-statements_ts-en-712a.pdf": "balance_sheet",
    }
    for fname, expected in cases.items():
        m = pipeline._FILENAME_RE.match(fname)
        assert m, fname
        assert pipeline.doc_type_for_kind(m.group(2)) == expected, fname
    # regex still parses the older hex-suffixed names correctly
    m = pipeline._FILENAME_RE.match("2025-07-31_interim-report_01b25cd0dd.pdf")
    assert m.group(2) == "interim-report" and m.group(3) == "01b25cd0dd"
    m = pipeline._FILENAME_RE.match(
        "2026-02-27_appendix-4d-and-2025-half-year-financial-report_03066761.pdf")
    assert m.group(2) == "appendix-4d-and-2025-half-year-financial-report"


def test_annual_mda_stores_q4_metrics_and_reserves(conn):
    cid = db.upsert_company(conn, "CA", "SOMA", "Soma Gold", "CAD", "CADUSD",
                            metal="gold")
    doc_id = db.upsert_document(conn, cid, "/x/2026-05-01_mda_da.pdf", "shama",
                                "annual_mda", "2026-05-01")
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    data = _empty_metrics()
    data["period"] = {"year": 2025, "quarter": 4}
    data["reporting_currency"] = "CAD"
    data["ore_milled_t"] = _metric(19895, unit="t")
    data["reserves"] = [
        {"statement_date": "2022-12-31", "project": "Cordero", "category": "indicated",
         "metal": "gold", "tonnage_t": 355000, "grade_gpt": 6.9, "page": 5,
         "confidence": "high"},
        {"statement_date": "2022-12-31", "project": "Nechi", "category": "inferred",
         "metal": "gold", "tonnage_t": 405000, "grade_gpt": 6.5, "page": 5,
         "confidence": "high"},
    ]
    data["notes"] = None
    pipeline._store(conn, doc, {"name": "Soma", "reporting_currency": "CAD",
                                "metal": "gold"}, data)
    # Q4 quarterly metric landed
    row = conn.execute("SELECT value FROM quarterly_metrics "
                       "WHERE period='2025-Q4' AND metric='ore_milled_t'").fetchone()
    assert row["value"] == 19895
    # AND the reserves rows landed with project + effective date
    res = conn.execute("SELECT project, category, tonnage FROM reserves_statements "
                       "ORDER BY category").fetchall()
    assert [(r["project"], r["category"]) for r in res] == \
        [("Cordero", "indicated"), ("Nechi", "inferred")]
    assert res[0]["tonnage"] == 355000


def test_annual_mda_period_is_prior_q4():
    # SEDAR annual MD&A filed Apr/May covers the prior calendar year's Q4
    assert pipeline.expected_period("2026-05-01", "annual_mda") == "2025-Q4"
    assert pipeline.expected_period("2025-04-30", "annual_mda") == "2024-Q4"
    # interim MD&A cadence (period end ~2 months before filing)
    assert pipeline.expected_period("2026-05-28", "interim_report") == "2026-Q1"
    assert pipeline.expected_period("2025-08-28", "interim_report") == "2025-Q2"
    assert pipeline.expected_period("2025-11-28", "interim_report") == "2025-Q3"


def test_lse_filename_and_kind_mapping():
    cases = {
        "2025-05-12_1st-quarter-results_8871715.html": "interim_report",
        "2025-01-27_q4-production-results_8706391.html": "interim_report",
        "2025-10-27_3rd-quarter-results_9194910.html": "interim_report",
        "2025-09-25_half-year-financial-report_0924972747.html": "half_year_report",
        # ESEF .zip annual + hyphen-leading suffix -> unmapped (skipped)
        "2026-04-29_annual-financial-report_-000144283.zip": None,
        "2026-01-22_miscellaneous_38b83c104d.html": None,
    }
    for fname, expected in cases.items():
        m = pipeline._FILENAME_RE.match(fname)
        assert m, fname
        assert pipeline.doc_type_for_kind(m.group(2)) == expected, fname


def test_html_to_text_strips_tags_and_tables():
    from miner_tracker.extraction.pdftext import html_to_text
    raw = ("<html><head><style>x{}</style></head><body>"
           "<p>Gold sold 11,532 oz</p>"
           "<table><tr><td>Revenue</td><td>US$56.3m</td></tr></table>"
           "revenue &amp; profit</body></html>")
    txt = html_to_text(raw)
    assert "Gold sold 11,532 oz" in txt
    assert "Revenue | US$56.3m" in txt
    assert "revenue & profit" in txt
    assert "<" not in txt and "style" not in txt


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


def test_store_per_project_reserves_and_aggregate(conn):
    from miner_tracker import queries
    cid = db.upsert_company(conn, "AU", "BCN", "Beacon", "AUD", "AUDUSD", metal="gold")
    doc_id = db.upsert_document(conn, cid, "/x/2025-09-05_ar_ab.pdf", "sha6x",
                                "annual_report", "2025-09-05")
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    data = {"fiscal_year": 2025, "notes": None, "reserves": [
        {"statement_date": "2025-06-30", "project": "Iguana Pit", "category": "proved",
         "metal": "gold", "tonnage_t": 181000, "grade_gpt": 1.6, "page": 66, "confidence": "high"},
        {"statement_date": "2025-06-30", "project": "Iguana Pit", "category": "probable",
         "metal": "gold", "tonnage_t": 3549000, "grade_gpt": 1.1, "page": 66, "confidence": "high"},
        {"statement_date": "2025-06-30", "project": "Geko Pit", "category": "proved",
         "metal": "gold", "tonnage_t": 980000, "grade_gpt": 1.1, "page": 66, "confidence": "high"},
    ]}
    pipeline._store(conn, doc, {"name": "Beacon", "reporting_currency": "AUD",
                                "metal": "gold"}, data)
    rows = conn.execute("SELECT project, category FROM reserves_statements "
                        "ORDER BY project, category").fetchall()
    assert [(r["project"], r["category"]) for r in rows] == [
        ("Geko Pit", "proved"), ("Iguana Pit", "probable"), ("Iguana Pit", "proved")]

    agg = queries.reserves_aggregate(queries.reserves_frame(conn, cid))
    proved = agg[agg["category"] == "proved"].iloc[0]
    assert proved["tonnage"] == 181000 + 980000               # summed across pits
    assert proved["grade_gpt"] == pytest.approx(               # tonnage-weighted
        (181000 * 1.6 + 980000 * 1.1) / (181000 + 980000))

    # re-extracting the same doc with different projects replaces, not appends
    data["reserves"] = [data["reserves"][0]]
    pipeline._store(conn, doc, {"name": "Beacon", "reporting_currency": "AUD",
                                "metal": "gold"}, data)
    assert conn.execute("SELECT COUNT(*) n FROM reserves_statements").fetchone()["n"] == 1


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


def test_quarter_label_for_date():
    assert pipeline.quarter_label_for_date("2026-03-31") == "2026-Q1"
    assert pipeline.quarter_label_for_date("2025-12-31") == "2025-Q4"
    assert pipeline.quarter_label_for_date("2025-06-30") == "2025-Q2"
    assert pipeline.quarter_label_for_date("garbage") is None


def test_store_balance_sheet(conn):
    cid = db.upsert_company(conn, "CA", "SOMA", "Soma Gold", "CAD", "CADUSD",
                            metal="gold")
    doc_id = db.upsert_document(conn, cid, "/x/2026-05-28_interim-financial-statements_z.pdf",
                                "shabs", "balance_sheet", "2026-05-28")
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    data = {
        "period_end_date": "2026-03-31",
        "cash": _metric(3_783_295, "CAD"),
        "total_debt": _metric(-18_800_000, "CAD"),   # negatives -> magnitude
        "shares_outstanding": _metric(118_453_241, unit="shares"),
        "notes": None,
    }
    pipeline._store(conn, doc, {"name": "Soma", "reporting_currency": "CAD",
                                "metal": "gold"}, data)
    rows = {r["metric"]: r for r in conn.execute(
        "SELECT metric, value, currency FROM quarterly_metrics WHERE period='2026-Q1'")}
    assert rows["cash"]["value"] == 3_783_295 and rows["cash"]["currency"] == "CAD"
    assert rows["debt"]["value"] == 18_800_000        # abs()
    assert rows["shares_outstanding"]["value"] == 118_453_241
    assert rows["shares_outstanding"]["currency"] is None


def test_net_debt_usd_same_period_and_fx(conn):
    from miner_tracker import queries
    cid = db.upsert_company(conn, "CA", "SOMA", "Soma", "CAD", "CADUSD", metal="gold")
    db.set_fx(conn, "CADUSD", "2026-Q1", 0.73)
    # older quarter with debt only should NOT be chosen (needs both)
    db.upsert_metric(conn, cid, "2024-Q4", "debt", 35_000_000, "CAD", None, None, 1, "high")
    db.upsert_metric(conn, cid, "2026-Q1", "debt", 18_800_000, "CAD", None, None, 1, "high")
    db.upsert_metric(conn, cid, "2026-Q1", "cash", 3_800_000, "CAD", None, None, 1, "high")
    nd = queries.net_debt_usd(conn, cid)
    assert nd["period"] == "2026-Q1"                  # latest with BOTH
    assert nd["net_debt_usd"] == pytest.approx((18_800_000 - 3_800_000) * 0.73)


def test_net_debt_usd_usd_reporter_no_fx(conn):
    from miner_tracker import queries
    cid = db.upsert_company(conn, "CA", "THX", "Thor", "USD", None, metal="gold")
    db.upsert_metric(conn, cid, "2026-Q1", "debt", 0, "USD", None, None, 1, "high")
    db.upsert_metric(conn, cid, "2026-Q1", "cash", 159_000_000, "USD", None, None, 1, "high")
    nd = queries.net_debt_usd(conn, cid)
    assert nd["net_debt_usd"] == -159_000_000 and nd["fx"] == 1.0
