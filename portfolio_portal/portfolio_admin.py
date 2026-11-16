"""Portfolio metadata rules for the private site.

These functions never read or write holdings. Callers apply the returned field
changes to the portfolios sheet and append an audit row. The trade ledger,
NAV history, and entry baselines stay untouched.
"""
from __future__ import annotations
from datetime import date, timedelta

STRATEGIES = ("A", "B", "背離長抱", "自訂")
MARKETS = ("US", "TW")


def clean_name(name):
    text = str(name or "").strip()
    if not text or len(text) > 80:
        raise ValueError("請輸入有效的組合名稱")
    return text


def clean_note(note):
    return str(note or "").strip()[:300]


def currency_for(market):
    if market == "US":
        return "USD"
    if market == "TW":
        return "TWD"
    raise ValueError("策略或市場不支援")


def validate_new_portfolio(*, name, market, strategy, currency, note=""):
    if market not in MARKETS or strategy not in STRATEGIES:
        raise ValueError("策略或市場不支援")
    if currency != currency_for(market):
        raise ValueError("組合幣別不符合交易市場")
    return {
        "name": clean_name(name),
        "market": market,
        "strategy": strategy,
        "currency": currency,
        "note": clean_note(note),
        "archived": "",
    }


def plan_profile_update(existing, *, name, note):
    new_name = clean_name(name)
    new_note = clean_note(note)
    changes = []
    if new_name != str(existing.get("name") or ""):
        changes.append(("name", str(existing.get("name") or ""), new_name))
    if new_note != str(existing.get("note") or ""):
        changes.append(("note", str(existing.get("note") or ""), new_note))
    return changes


def plan_strategy_change(existing, new_strategy, *, confirmed):
    if not confirmed:
        raise ValueError("策略變更需要明確確認")
    if new_strategy not in STRATEGIES:
        raise ValueError("策略或市場不支援")
    old = str(existing.get("strategy") or "")
    if new_strategy == old:
        return None
    return ("strategy", old, new_strategy)


def plan_archive(existing, *, confirmed):
    if not confirmed:
        raise ValueError("封存需要確認")
    if str(existing.get("archived") or "").lower() == "true":
        raise ValueError("這個組合已經封存")
    return ("archived", str(existing.get("archived") or ""), "true")


def plan_restore(existing):
    if str(existing.get("archived") or "").lower() != "true":
        raise ValueError("這個組合沒有封存")
    return ("archived", "true", "")


def permanent_delete_refused():
    raise ValueError("只允許封存，不永久刪除投資組合、交易、淨值或建倉基準")


def weekday_gaps(iso_dates):
    """Weekdays between the first and last saved NAV that have no row.

    The missing days are labels only. Nothing here invents a NAV.
    """
    days = []
    for raw in iso_dates or []:
        text = str(raw or "")[:10]
        if len(text) != 10:
            continue
        days.append(date.fromisoformat(text))
    if len(days) < 2:
        return []
    have = set(days)
    cursor = min(days) + timedelta(days=1)
    end = max(days)
    missing = []
    while cursor < end:
        if cursor.weekday() < 5 and cursor not in have:
            missing.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return missing


def condition_timeline(rows, portfolio_id, symbol):
    """Collapse append-only exit rows into first-trigger and resolve times."""
    state = {}
    for row in rows or []:
        if str(row.get("portfolio_id")) != str(portfolio_id):
            continue
        if str(row.get("symbol")) != str(symbol):
            continue
        cond = str(row.get("condition") or "")
        if not cond:
            continue
        slot = state.setdefault(cond, {"condition": cond, "first": "", "resolved": "", "open": False})
        if row.get("state") == "open":
            slot["first"] = row.get("first_triggered_at") or row.get("recorded_at") or slot["first"]
            slot["resolved"] = ""
            slot["open"] = True
        elif row.get("state") == "resolved":
            slot["first"] = row.get("first_triggered_at") or slot["first"]
            slot["resolved"] = row.get("resolved_at") or row.get("recorded_at") or ""
            slot["open"] = False
    return list(state.values())
