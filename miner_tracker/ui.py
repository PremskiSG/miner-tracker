"""Shared Streamlit helpers."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from miner_tracker import db, queries


@st.cache_data(ttl=30)
def companies_df() -> pd.DataFrame:
    conn = db.connect()
    try:
        return queries.companies_frame(conn)
    finally:
        conn.close()


def select_company() -> dict | None:
    df = companies_df()
    if df.empty:
        st.warning("No companies in the database yet — run "
                   "`python -m miner_tracker sync` first.")
        return None
    labels = df.apply(lambda r: f"{r['name']} ({r['market']}:{r['ticker']})", axis=1)
    idx = st.sidebar.selectbox("Company", range(len(df)),
                               format_func=lambda i: labels.iloc[i])
    return df.iloc[idx].to_dict()


@st.cache_data(ttl=30)
def metrics_long(company_id: int) -> pd.DataFrame:
    conn = db.connect()
    try:
        return queries.metrics_long(conn, company_id)
    finally:
        conn.close()


@st.cache_data(ttl=30)
def reserves(company_id: int) -> pd.DataFrame:
    conn = db.connect()
    try:
        return queries.reserves_frame(conn, company_id)
    finally:
        conn.close()


def period_sort_key(s: pd.Series) -> pd.Series:
    return s.str.replace("-Q", ".").astype(float)
