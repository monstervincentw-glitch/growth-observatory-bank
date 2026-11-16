"""Owner-only portfolio exit and funding monitor with append-only state changes.

No simulated trade here changes actual positions. Events are deliberately about
the user's position and its entry financial baseline, not research-only ticker lists.
"""
from __future__ import annotations
from datetime import datetime, timezone, date, timedelta
import hashlib
import json
from core import compute_account, portfolio_value, exit_review, funding_decision


def _as_date(value):
    return date.fromisoformat(str(value)[:10])


def data_too_old(asof, today=None):
    """Conservative trading-calendar approximation, excluding weekends.

    On holidays this may over-block; it never silently permits a stale
    snapshot for multiple business days.
    """
    today=today or datetime.now(timezone.utc).date()
    cutoff=_as_date(asof)
    if cutoff>today:
        return True
    days=0
    cursor=cutoff+timedelta(days=1)
    while cursor<=today:
        days+=cursor.weekday()<5
        if days>1:
            return True
        cursor+=timedelta(days=1)
    return False


def review_holding(holding, strategy, macro):
    """For long-hold and custom portfolios avoid inventing a sell rule."""
    if strategy in ("A", "B"):
        return exit_review(holding, macro)
    s=holding["stock"]
    unknown=[]
    risks=[]
    if not s.get("integrity") or s.get("sourceError"):
        unknown.append("行情來源尚未核實")
    if s.get("financial", {}).get("financial_status")!="ok":
        unknown.append("最新財報資訊不足")
    if funding_decision(macro) == "red":
        risks.append("全球資金溫度紅燈，僅作長抱風險觀察")
    elif funding_decision(macro) != "non_red":
        unknown.append("無法判定：全球資金溫度未知，不能當成未觸發")
    if strategy=="背離長抱":
        if not (s.get("evaluation") or {}).get("divergence"):
            risks.append("目前未具備最新背離觀察條件；不等於原策略賣出訊號")
    else:
        unknown.append("自訂策略尚未設定賣出規則")
    return {"status":"無法判定" if unknown else "長抱風險觀察",
            "reasons":risks,"unknown":unknown,
            "symbol":s["symbol"],"asof":s.get("priceDate"),
            "financial_period":s.get("financial",{}).get("latest_period"),
            "observed_at":s.get("financialObservedAt")}


def portfolio_reviews(portfolios, transactions, snapshot, batch, today=None):
    """Generate current per-position review rows; fail closed on missing data."""
    events=[]
    for p in portfolios:
        market=p["market"]
        if market not in ("US", "TW"):
            continue
        block=snapshot["markets"][market]
        macro=block["macro"]
        asof=macro["marketAsOf"]
        stale=data_too_old(asof,today=today)
        account=compute_account(transactions,p["id"])
        valued=portfolio_value(account,block)
        found=set()
        for h in valued["holdings"]:
            found.add(h["symbol"])
            if stale:
                review={"status":"無法判定","reasons":[],
                        "unknown":["無法判定：市場研究批次已過期，暫停最新買賣判斷"],
                        "asof":asof,"financial_period":None,"observed_at":None}
            else:
                review=review_holding(h,p["strategy"],macro)
            events.append(_event_row(p,h["symbol"],review,asof,batch))
        for symbol in valued["missing_prices"]:
            if symbol not in found:
                events.append(_event_row(
                    p,symbol,{"status":"無法判定","reasons":[],
                    "unknown":["無法判定：這檔持股沒有同日可驗證行情或已停止交易"],
                    "financial_period":None,"observed_at":None},
                    asof,batch))
    return events


def _event_row(portfolio,symbol,review,asof,batch):
    return {"portfolio_id":portfolio["id"],"market":portfolio["market"],
            "symbol":symbol,"strategy":portfolio["strategy"],
            "signal_date":asof,"batch":batch,"status":review["status"],
            "reasons":"｜".join(review.get("reasons") or []),
            "unknown":"｜".join(review.get("unknown") or []),
            "financial_period":review.get("financial_period") or "",
            "observed_at":review.get("observed_at") or ""}


def changed_events(current, history):
    """Save only transitions, not daily repeats; monitor versions are audited."""
    last={}
    for r in history:
        last[(r["portfolio_id"],r["market"],r["symbol"])]=r
    changed=[]
    for row in current:
        key=(row["portfolio_id"],row["market"],row["symbol"])
        old=last.get(key)
        # No need to spam the same trigger every batch; persist first observation
        # and changed risk/unknown detail, including resolved transitions.
        state=(row["status"],row["reasons"],row["unknown"])
        prior=(old.get("status"),old.get("reasons"),old.get("unknown")) if old else None
        if state!=prior:
            changed.append(row)
    return changed


def _parts(text):
    return [part for part in str(text or "").split("｜") if part]


def condition_transitions(current, history):
    """Append-only open and resolved rows for each exit condition.

    history rows are previous transition records, oldest first. An open row is
    the first trigger. A later resolved row records the release time without
    editing the original trigger.
    """
    open_rows = {}
    for row in history or []:
        key = (str(row.get("portfolio_id")), str(row.get("symbol")), str(row.get("condition")))
        if row.get("state") == "open":
            open_rows[key] = row
        elif row.get("state") == "resolved":
            open_rows.pop(key, None)
    active = {}
    for row in current:
        conditions = _parts(row.get("reasons")) + _parts(row.get("unknown"))
        for condition in conditions:
            active[(str(row["portfolio_id"]), str(row["symbol"]), condition)] = row
    changes = []
    for key, row in active.items():
        if key in open_rows:
            continue
        changes.append({
            "portfolio_id": key[0], "market": row.get("market", ""), "symbol": key[1],
            "condition": key[2], "state": "open",
            "first_triggered_at": "", "resolved_at": "",
            "signal_date": row.get("signal_date", ""), "batch": row.get("batch", ""),
            "detail": row.get("status", ""),
        })
    for key, opened in open_rows.items():
        if key in active:
            continue
        changes.append({
            "portfolio_id": key[0], "market": opened.get("market", ""), "symbol": key[1],
            "condition": key[2], "state": "resolved",
            "first_triggered_at": opened.get("first_triggered_at") or opened.get("recorded_at") or "",
            "resolved_at": "",
            "signal_date": "", "batch": "",
            "detail": "條件已解除",
        })
    return changes
