"""Scheduled owner-only observation check from validated bank research.

Run with GOOGLE_SERVICE_ACCOUNT_JSON and PRIVATE_PORTFOLIO_SHEET_ID as
repository Actions secrets. No actual holdings or credentials are printed.
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone

from research import latest_research
from monitor import portfolio_reviews, data_too_old
from core import compute_account, portfolio_value
from entry_basis import apply_official_baselines, sync_entry_baselines
from storage import PortfolioSheets, sheets_client


def run():
    credentials = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    private_id = os.environ.get("PRIVATE_PORTFOLIO_SHEET_ID")
    if not credentials or not private_id:
        raise RuntimeError("Private Google Sheet or owner service-account credentials not configured")
    client = sheets_client(json.loads(credentials))
    store = PortfolioSheets(client, private_id)
    entry, source, notes = latest_research(client)
    snapshot = entry["snapshot"]
    portfolios = store.portfolios()
    transactions = store.transactions()
    baseline_result = "skipped"
    try:
        added = sync_entry_baselines(store, transactions, portfolios, snapshot)
        baseline_result = "recorded" if added else "unchanged"
    except Exception as exc:
        baseline_result = exc.__class__.__name__
    try:
        transactions = apply_official_baselines(transactions, store.entry_baselines())
    except Exception as exc:
        baseline_result = exc.__class__.__name__
    current = portfolio_reviews(portfolios, transactions, snapshot, entry["batch"])
    changed = store.append_monitor_results(current)
    transitions = store.append_exit_transitions(current)
    nav_new_count=0
    # Save verified account NAV separately from owner-created positions/trades.
    # Use only a full, confirmed same-session quote for every held security.
    for p in portfolios:
        market=p["market"]
        block=snapshot["markets"][market]
        asof=block["macro"]["marketAsOf"]
        if data_too_old(asof):
            continue
        account=compute_account(transactions,p["id"])
        valued=portfolio_value(account,block)
        if valued["nav"] is None:
            continue
        if store.record_nav_snapshot(
            portfolio_id=p["id"], session_date=asof,
            nav=valued["nav"],cash=valued["cash"],
            equity_value=valued["market_value"]
        ):
            nav_new_count+=1
    # Output aggregate status only. Never expose user positions, costs or trades in CI logs.
    print(json.dumps({
        "result":"complete", "source":source, "batch":entry["batch"],
        "market_dates":{m:snapshot["markets"][m]["macro"]["marketAsOf"] for m in ("US","TW")},
        "portfolio_count":len(portfolios), "monitored_position_count":len(current),
        "new_event_count":len(changed), "exit_transitions":len(transitions),
        "entry_baselines":baseline_result, "nav_snapshots_created":nav_new_count,
        "observation_warnings":len(notes),
        "at":datetime.now(timezone.utc).isoformat()
    },ensure_ascii=False))


if __name__=="__main__":
    run()
