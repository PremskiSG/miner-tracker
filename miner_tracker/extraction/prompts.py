from __future__ import annotations

SYSTEM = (
    "You are a precise financial data extractor for mining company reports. "
    "Extract only values explicitly stated in the document. Never estimate or "
    "infer numbers. Use null for anything not stated. Report figures in the "
    "document's reporting currency and state that currency per value. Amounts "
    "reported in thousands or millions (TSEK, kSEK, MSEK, A$M, $M, kt, koz, ...) "
    "must be converted to absolute units (e.g. 110.0 MSEK -> 110000000; "
    "20.1 A$M -> 20100000; 111 kt -> 111000; 5.4 koz -> 5400). The document "
    "may be in English, Finnish, or Swedish — extract regardless of language."
)


def _glossary(metal: str) -> str:
    return (
        f"Glossary hints: head grade may appear as '{metal} grade (g/t)', 'average "
        f"{metal} grade' or 'Au/Ag grade' — use the PRODUCTION head grade for the "
        "period, never a reserves/resources grade and never a 'recovered grade'; "
        "ore tonnage may appear as 'mill feed', 'milled tonnes', 'milled ore', "
        "'ore processed' (-> ore_milled_t) or 'ore mined' (-> ore_mined_t) — "
        "tonnes ONLY: if ore mined is stated in BCM (bank cubic metres), leave "
        "ore_mined_t null; NEVER multiply a value that is already in whole "
        "tonnes by 1000 (only convert when the unit is explicitly kt); "
        "'aisc_reported' is the All-In Sustaining Cost PER OUNCE ($/oz) ONLY if "
        "the report states AISC explicitly — when both a table and prose state "
        "it, prefer the prose sentence about the quarter, and never confuse the "
        "total 'AISC $M' line with per-ounce AISC or with operating costs; "
        "'cash_cost_per_oz' likewise for stated cash costs per ounce; "
        "'reported_cost' means TOTAL operating/cash operating costs for the period "
        "EXCLUDING depreciation (e.g. a 'Total cash operating costs' line, or the "
        "sum of income-statement components such as raw materials, other external "
        "expenses, personnel costs — note the addends in its unit field); do NOT "
        "include depreciation, financial items, taxes, or any AISC line. "
        "Recovery is the mill recovery PERCENTAGE (e.g. 86), never a grade. "
        "'personnel' is the NUMBER of employees (headcount), never an employment "
        "expense amount. 'cash' is cash and cash equivalents only (exclude term "
        "deposits/investments). 'interest_expense' is the finance/interest expense "
        "from the income statement, not a cashflow payment line. Capex is "
        "investments in property, plant and equipment (tangible and intangible) — "
        "prefer an explicitly stated 'capital expenditure' figure over net "
        "investing-cashflow totals. "
        f"Realized {metal} price may be quoted per ounce in USD or the local "
        "currency (A$/oz, US$/oz) — set each value's currency field accordingly. "
        "For each value give the PDF page number where it appears and a "
        "confidence: 'high' (explicitly stated in a table), 'medium' (stated in "
        "prose or required unit interpretation), 'low' (ambiguous)."
    )


def interim_prompt(company: str, published_date: str, metal: str = "silver") -> str:
    return (
        f"This is a quarterly interim report from {company}, a {metal} mining "
        f"company, published {published_date}. Extract the metrics for THE QUARTER "
        "ONLY — not year-to-date, not full year. If the report shows both a "
        "cumulative column (e.g. '1-9/2025' or 'January – September') and a quarter "
        "column (e.g. '7-9/2025' or 'July – September'), use the quarter column. "
        "Also return which fiscal quarter the report covers. " + _glossary(metal)
    )


def quarterly_activities_prompt(company: str, published_date: str,
                                metal: str = "gold") -> str:
    return (
        f"This is an ASX quarterly activities report from {company}, a {metal} "
        f"mining company, published {published_date}. Extract metrics for THE "
        "QUARTER as a whole. CRITICAL: tables in these reports often mix MONTHLY "
        "columns, the QUARTER column, prior-quarter columns and a financial-"
        "year-to-date column side by side — always use the column covering the "
        "full three-month quarter this report is about; never a single month, "
        "never the FYTD/annual column, never a previous quarter. Return the "
        "CALENDAR year and quarter the report covers (e.g. the March 2026 "
        "quarter -> year 2026, quarter 1), not the Australian fiscal quarter. "
        "Amounts are typically in A$ (AUD). " + _glossary(metal)
    )


def fs_release_prompt(company: str, published_date: str, metal: str = "silver") -> str:
    return (
        f"This is a full-year financial statement release from {company}, a "
        f"{metal} mining company, published {published_date}. It mixes "
        "fourth-quarter (Q4, i.e. 10-12/year) columns and full-year (1-12/year) "
        "columns. Extract BOTH separately: the 'q4' object must contain only "
        "quarter figures, the 'full_year' object only full-year figures. Never put "
        "a full-year number in q4. " + _glossary(metal)
    )


def half_year_prompt(company: str, published_date: str, metal: str = "gold") -> str:
    return (
        f"This is a half-year financial report (e.g. ASX Appendix 4D) from "
        f"{company}, a {metal} mining company, published {published_date}. "
        "Extract the metrics for the SIX-MONTH period the report covers and "
        "return that period's end date (period_end_date, YYYY-MM-DD). Use the "
        "current half-year column, not the prior corresponding period. "
        + _glossary(metal)
    )


def fy_prompt(company: str, published_date: str, metal: str = "gold") -> str:
    return (
        f"This is a preliminary final / full-year financial report (e.g. ASX "
        f"Appendix 4E) from {company}, a {metal} mining company, published "
        f"{published_date}. Extract the metrics for the FULL financial year the "
        "report covers and return that year's end date (period_end_date, "
        "YYYY-MM-DD — Australian financial years typically end 30 June). Use the "
        "current-year column, not the prior year. " + _glossary(metal)
    )


def annual_prompt(company: str, published_date: str, metal: str = "silver") -> str:
    return (
        f"This is an annual report from {company}, a {metal} mining company, "
        f"published {published_date}. Extract EVERY ROW of the Mineral Resources "
        "and Ore Reserves statement(s). Emit ONE array entry per "
        "(project, category, statement_date): \n"
        "- 'project': the individual pit/deposit/mine name as written (e.g. "
        "'Iguana Pit', 'Geko Pit'). If the table gives ONLY a single "
        "company-wide/consolidated total with no per-deposit breakdown, use an "
        "empty string \"\" for project.\n"
        "- 'category': Resources use 'measured', 'indicated', 'inferred' (or "
        "'measured_indicated' if only the combined M+I is given); Ore Reserves "
        "use 'proved' and 'probable' (or 'proven_probable' if only the combined "
        "figure is given). Keep Proved and Probable as SEPARATE rows when the "
        "table lists them separately. Do NOT invent a company total — only emit "
        "totals that are printed as such.\n"
        f"- 'tonnage_t' in tonnes (convert Mt/million tonnes -> tonnes, e.g. "
        "0.181 million tonnes -> 181000), 'grade_gpt' the "
        f"{metal} grade in g/t.\n"
        "- 'statement_date' the effective date of that column (YYYY-MM-DD, or "
        "YYYY-MM). These tables often show TWO date columns side by side (e.g. "
        "30 June 2025 and 30 June 2024) — emit a separate row for EACH date that "
        "has values, tagging each with its own statement_date.\n"
        "Give the PDF page number and a confidence high/medium/low for each row."
    )


PROMPTS = {
    "interim_report": interim_prompt,
    "quarterly_activities": quarterly_activities_prompt,
    "fs_release": fs_release_prompt,
    "half_year_report": half_year_prompt,
    "fy_report": fy_prompt,
    "annual_report": annual_prompt,
}
