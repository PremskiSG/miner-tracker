from __future__ import annotations

SYSTEM = (
    "You are a precise financial data extractor for mining company reports. "
    "Extract only values explicitly stated in the document. Never estimate or "
    "infer numbers. Use null for anything not stated. Report figures in the "
    "document's reporting currency and state that currency per value. Amounts "
    "reported in thousands or millions (TSEK, kSEK, MSEK, TEUR, ...) must be "
    "converted to absolute units (e.g. 110.0 MSEK -> 110000000). The document "
    "may be in English, Finnish, or Swedish — extract regardless of language."
)

_GLOSSARY = (
    "Glossary hints: head grade may appear as 'Ag grade (g/t)', 'silver grade' "
    "or 'average silver grade' — use the PRODUCTION head grade for the quarter, "
    "never a reserves/resources grade; ore tonnage may appear as 'mill feed', "
    "'milled tonnes', 'milled ore', 'processed ore' or 'tonnes of ore' (convert "
    "kt to tonnes); 'ebit' is operating profit; 'equity_ratio_pct' is the "
    "equity ratio %; 'personnel' is headcount at the end of the period; the "
    "realized silver price is often quoted in USD/oz even when the financial "
    "statements are in SEK — set that value's currency to USD in that case; "
    "'reported_cost' means TOTAL operating costs for the period EXCLUDING "
    "depreciation: if the income statement lists components (e.g. raw materials "
    "and consumables, other external expenses/costs, personnel costs), sum those "
    "components and note the addends in its unit field; do NOT include "
    "depreciation, financial items or taxes. Recovery is the mill silver "
    "recovery percentage. Capex is investments in property, plant and "
    "equipment (tangible and intangible) for the period. For each value give the PDF page number where it "
    "appears and a confidence: 'high' (explicitly stated in a table), 'medium' "
    "(stated in prose or required unit interpretation), 'low' (ambiguous)."
)


def interim_prompt(company: str, published_date: str) -> str:
    return (
        f"This is a quarterly interim report from {company}, a silver/gold mining "
        f"company, published {published_date}. Extract the metrics for THE QUARTER "
        "ONLY — not year-to-date, not full year. If the report shows both a "
        "cumulative column (e.g. '1-9/2025' or 'January – September') and a quarter "
        "column (e.g. '7-9/2025' or 'July – September'), use the quarter column. "
        "Also return which fiscal quarter the report covers. "
        + _GLOSSARY
    )


def fs_release_prompt(company: str, published_date: str) -> str:
    return (
        f"This is a full-year financial statement release from {company}, a "
        f"silver/gold mining company, published {published_date}. It mixes "
        "fourth-quarter (Q4, i.e. 10-12/year) columns and full-year (1-12/year) "
        "columns. Extract BOTH separately: the 'q4' object must contain only "
        "quarter figures, the 'full_year' object only full-year figures. Never put "
        "a full-year number in q4. " + _GLOSSARY
    )


def annual_prompt(company: str, published_date: str) -> str:
    return (
        f"This is an annual report from {company}, a silver/gold mining company, "
        f"published {published_date}. Extract the mineral reserves and resources "
        "statement(s): for each category (measured, indicated, inferred, proven & "
        "probable — use 'proven_probable'; if measured and indicated are only given "
        "combined, use 'measured_indicated') give the tonnage in tonnes, the silver "
        "grade in g/t, the metal, and the effective date of the statement "
        "(YYYY-MM-DD, or YYYY-MM if no day is given). If several statement dates "
        "appear, extract each. For each value give the PDF page number and a "
        "confidence high/medium/low."
    )


PROMPTS = {
    "interim_report": interim_prompt,
    "fs_release": fs_release_prompt,
    "annual_report": annual_prompt,
}
