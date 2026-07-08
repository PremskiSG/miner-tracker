"""Review low-confidence / flagged extracted values and override manually."""
from pathlib import Path

import streamlit as st

from miner_tracker import db, queries, ui

st.set_page_config(page_title="Extraction Review", layout="wide")
st.title("Extraction Review")

company = ui.select_company()
if company is None:
    st.stop()

conn = db.connect()
rows = queries.review_rows(conn, company["id"])

if rows.empty:
    st.success("Nothing needs review 🎉")
else:
    st.write(f"{len(rows)} value(s) flagged (needs_review or low/medium confidence).")
    st.dataframe(rows.drop(columns=["source_pdf"]), use_container_width=True,
                 hide_index=True)

    st.subheader("Override a value")
    options = rows.apply(
        lambda r: f"{r['period']} · {r['metric']} = {r['value']} ({r['confidence']})",
        axis=1)
    sel = st.selectbox("Row", range(len(rows)), format_func=lambda i: options.iloc[i])
    row = rows.iloc[sel]

    col1, col2 = st.columns([1, 1])
    with col1:
        new_value = st.number_input("Corrected value", value=float(row["value"] or 0.0),
                                    format="%.4f")
        confirm = st.checkbox("Confirm as correct (no change needed)")
        if st.button("Apply", type="primary"):
            if confirm:
                conn.execute("""UPDATE quarterly_metrics SET needs_review=0,
                                confidence='high' WHERE id=?""", (int(row["id"]),))
            else:
                conn.execute("""UPDATE quarterly_metrics SET value=?, needs_review=0,
                                confidence='manual' WHERE id=?""",
                             (new_value, int(row["id"])))
            conn.commit()
            st.cache_data.clear()
            st.rerun()
    with col2:
        pdf = row["source_pdf"]
        if pdf:
            st.write(f"Source: `{Path(pdf).name}`"
                     + (f", page {int(row['source_page'])}" if row["source_page"] else ""))
            if Path(pdf).exists():
                st.download_button("Download source PDF", Path(pdf).read_bytes(),
                                   file_name=Path(pdf).name, mime="application/pdf")
            else:
                st.caption("PDF stored on the extraction machine — not available "
                           "on this deployment.")

st.divider()
st.subheader("Extraction runs")
runs = queries.extraction_runs_frame(conn)
if not runs.empty:
    runs["path"] = runs["path"].map(lambda p: Path(p).name)
    st.dataframe(runs.drop(columns=["error"]).head(50), use_container_width=True,
                 hide_index=True)
    total = runs["cost_usd"].fillna(0).sum()
    st.caption(f"Total API spend: ${total:.2f}")
    failed = runs[runs["status"] == "failed"]
    if not failed.empty:
        with st.expander(f"{len(failed)} failed run(s)"):
            st.dataframe(failed[["path", "model", "error"]], hide_index=True)
conn.close()
