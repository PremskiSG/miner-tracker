from __future__ import annotations

import argparse
import logging
import sys

from miner_tracker import db


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s %(message)s")
    p = argparse.ArgumentParser(prog="miner_tracker")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sync", help="register filing PDFs into the documents table")

    ex = sub.add_parser("extract", help="extract pending documents via Claude API")
    ex.add_argument("--company", help="MARKET_TICKER, e.g. NORDIC_SOSI1")
    ex.add_argument("--doc", help="path to a single PDF")
    ex.add_argument("--model", help="model override, e.g. claude-sonnet-5")
    ex.add_argument("--force", action="store_true", help="re-extract extracted docs")
    ex.add_argument("--dry-run", action="store_true")
    ex.add_argument("--limit", type=int, help="max documents this run")
    ex.add_argument("--type", dest="doc_type",
                    choices=["interim_report", "fs_release", "annual_report"],
                    help="only documents of this type")

    fx = sub.add_parser("fx", help="refresh quarterly FX rates from yfinance")
    fx.add_argument("--start-year", type=int, default=2021)

    args = p.parse_args(argv)
    conn = db.connect()

    if args.cmd == "sync":
        new = _sync(conn)
        print(f"sync: {new} new document(s) registered")
    elif args.cmd == "extract":
        from miner_tracker.extraction.pipeline import extract_pending, sync
        sync(conn)
        ok, failed = extract_pending(conn, company=args.company, doc_path=args.doc,
                                     model=args.model, force=args.force,
                                     dry_run=args.dry_run, limit=args.limit,
                                     doc_type=args.doc_type)
        total = conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) c FROM extraction_runs").fetchone()["c"]
        print(f"extract: {ok} ok, {failed} failed. Cumulative API cost: ${total:.2f}")
        if failed:
            return 1
    elif args.cmd == "fx":
        from miner_tracker.fx import refresh_all
        refresh_all(conn, start_year=args.start_year)
        print("fx: rates refreshed")
    return 0


def _sync(conn) -> int:
    from miner_tracker.extraction.pipeline import sync
    return sync(conn)


if __name__ == "__main__":
    sys.exit(main())
