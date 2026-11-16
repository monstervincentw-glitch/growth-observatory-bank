"""Pure portfolio accounting and forward-looking research decisions.

No order execution, no invented prices, and no historical backfill.
Inputs are verified current research snapshots and owner-recorded transactions.
"""
from __future__ import annotations
from collections import defaultdict
from decimal import Decimal, InvalidOperation

ZERO = Decimal("0")
ENTRY_FRACTION = Decimal("0.10")
FINANCIAL_AGE_LIMIT = 180


def money(value):
    if value in (None, ""):
        return ZERO
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid numeric value") from exc
    if not result.is_finite():
        raise ValueError("Non-finite financial input")
    return result


def _positive(value, label):
    result = money(value)
    if result <= ZERO:
        raise ValueError(f"{label} must be positive")
    return result


def compute_account(transactions, portfolio_id):
    """Rebuild account from an append-only ledger; sells consume FIFO lots.

    Raises on overselling or insufficient cash. P/L is after recorded fees.
    """
    cash, realized = ZERO, ZERO
    lots = defaultdict(list)
    ordered = sorted(
        (r for r in transactions if str(r.get("portfolio_id")) == str(portfolio_id)),
        key=lambda r: (str(r.get("trade_date") or str(r.get("at", ""))[:10]), str(r.get("at", "")), str(r.get("id", ""))),
    )
    for tx in ordered:
        action = str(tx["action"]).upper()
        symbol = str(tx.get("symbol", "")).upper().strip()
        fee = money(tx.get("fee"))
        if fee < 0:
            raise ValueError("Negative transaction fee")
        if action == "DEPOSIT":
            cash += _positive(tx.get("cash_amount"), "deposit")
        elif action == "WITHDRAW":
            cash -= _positive(tx.get("cash_amount"), "withdrawal")
        elif action == "BUY":
            qty, price = _positive(tx.get("quantity"), "quantity"), _positive(tx.get("price"), "price")
            if not symbol:
                raise ValueError("Missing ticker")
            cost = qty * price + fee
            cash -= cost
            lots[symbol].append({
                "quantity": qty, "unit_cost": cost / qty, "role": tx.get("role") or "core",
                "at": tx["at"], "entry_growth": tx.get("entry_growth", ""),
                "entry_margin": tx.get("entry_margin", ""),
                "entry_filing_period": tx.get("entry_filing_period", ""),
            })
        elif action == "SELL":
            qty, price = _positive(tx.get("quantity"), "quantity"), _positive(tx.get("price"), "price")
            if not symbol:
                raise ValueError("Missing ticker")
            remaining = qty
            for lot in lots[symbol]:
                consumed = min(lot["quantity"], remaining)
                if consumed:
                    cost = consumed * lot["unit_cost"]
                    realized += consumed * price - cost - fee * consumed / qty
                    lot["quantity"] -= consumed
                    remaining -= consumed
                if remaining == ZERO:
                    break
            if remaining > ZERO:
                raise ValueError("Cannot sell more than owned")
            cash += qty * price - fee
        else:
            raise ValueError(f"Unknown action {action}")
        if cash < Decimal("-0.000001"):
            raise ValueError("Account cash cannot become negative")
    open_lots = {symbol: [l for l in rows if l["quantity"] > 0]
                 for symbol, rows in lots.items()}
    open_lots = {s: rows for s, rows in open_lots.items() if rows}
    return {"cash": cash, "realized": realized, "lots": open_lots,
            "positions": {s: sum((l["quantity"] for l in rows), ZERO)
                          for s, rows in open_lots.items()}}


def portfolio_value(account, market_block):
    """Never quote or recommend trades when any held stock lacks valid price."""
    stocks = {s["symbol"]: s for s in market_block["stocks"]}
    market_value, items, missing = ZERO, [], []
    for symbol, lots in account["lots"].items():
        s = stocks.get(symbol)
        if (not s or not s.get("integrity") or s.get("sourceError")
                or s.get("marketEvent") or not s.get("close") or
                s.get("priceDate") != market_block["macro"]["marketAsOf"]):
            missing.append(symbol)
            continue
        price = money(s["close"])
        qty = sum((lot["quantity"] for lot in lots), ZERO)
        cost = sum((lot["quantity"] * lot["unit_cost"] for lot in lots), ZERO)
        value = price * qty
        market_value += value
        items.append({"symbol": symbol, "quantity": qty, "price": price,
                      "value": value, "cost": cost, "pnl": value - cost,
                      "lots": lots, "stock": s})
    return {"cash": account["cash"], "market_value": market_value,
            "nav": account["cash"] + market_value if not missing else None,
            "holdings": items, "missing_prices": missing,
            "realized": account["realized"]}


def entry_eligible(stock):
    e = stock.get("evaluation") or {}
    return bool(stock.get("integrity") and not stock.get("sourceError")
                and not stock.get("marketEvent") and stock.get("researchRank")
                and e.get("fundamental") and e.get("tradable") and e.get("fresh")
                and stock.get("financial", {}).get("financial_status") == "ok")


def buy_plan(account, valued, market_block, variant="A"):
    """Evaluate A core slots first; B extras only if core exactly full.

    Suggestions use close prices and do not mean a completed transaction.
    """
    if variant not in ("A", "B"):
        raise ValueError("Only main strategy A/B supported")
    macro = market_block["macro"]
    if valued["nav"] is None:
        return {"status": "估值不完整，暫停建議", "candidates": [], "mode": None}
    if macro.get("decision") != "non_red":
        return {"status": "資金紅燈或狀態待確認，不新增", "candidates": [], "mode": None}
    holdings = account["lots"]
    core = {s for s, rows in holdings.items() if any(l["role"] == "core" for l in rows)}
    if len(core) > 10:
        return {"status": "核心持股超過 10 檔，需人工確認", "candidates": [], "mode": None}
    ranked = sorted((s for s in market_block["stocks"] if entry_eligible(s)
                     and s["symbol"] not in holdings),
                    key=lambda s: (s["researchRank"]["rank"], s["symbol"]))
    threshold = None
    if len(core) < 10:
        mode, limit = "core", 10 - len(core)
    elif variant == "A":
        return {"status": "A 核心已滿 10 檔，保留現金", "candidates": [],
                "mode": "none", "core_count": len(core)}
    else:
        core_stocks = {s["symbol"]: s for s in market_block["stocks"] if s["symbol"] in core}
        if len(core_stocks) != len(core) or any(
            not entry_eligible(s) for s in core_stocks.values()
        ):
            return {"status": "核心持股有評分或資料缺口，無法核實 B 加碼門檻",
                    "candidates": [], "mode": None}
        threshold = max(money(s["researchRank"]["score"]) for s in core_stocks.values())
        ranked = [s for s in ranked if money(s["researchRank"]["score"]) > threshold]
        mode, limit = "extra", len(ranked)
    target = valued["nav"] * ENTRY_FRACTION
    if target <= 0 or valued["cash"] < target:
        return {"status": "可用現金不足以建立完整 10% 目標部位",
                "candidates": [], "mode": mode, "core_count": len(core),
                "target": target, "threshold": threshold}
    available = int(valued["cash"] // target)
    proposed = ranked[:min(limit, available)]
    return {"status": "符合候選檢查；實際交易前需確認開盤及成本" if proposed else
            "本次無可新增股票", "mode": mode, "core_count": len(core),
            "threshold": threshold, "target": target,
            "candidates": [{"symbol": s["symbol"], "name": s.get("name", ""),
                            "score": s["researchRank"]["score"],
                            "price_date": s["priceDate"], "target": target,
                            "role": mode} for s in proposed]}


def exit_review(holding, macro):
    """Evaluate each held lot against latest financials and its entry baseline."""
    stock = holding["stock"]
    f = stock.get("financial") or {}
    reasons = []
    unknown = []
    if not stock.get("integrity") or stock.get("sourceError"):
        unknown.append("行情來源未確認")
    if macro.get("decision") == "red":
        reasons.append("全球資金溫度已達紅燈門檻")
    elif macro.get("decision") != "non_red":
        unknown.append("全球資金溫度未知")
    if f.get("financial_status") != "ok":
        unknown.append("最新財報必要欄位不完整")
    else:
        for reason in (stock.get("evaluation") or {}).get("fundamentalReasons", []):
            reasons.append(str(reason))
        for lot in holding["lots"]:
            growth, margin = f.get("revenue_growth"), f.get("operating_margin")
            if lot.get("entry_growth") in (None, "") or lot.get("entry_margin") in (None, ""):
                unknown.append("這筆持股缺少進場時財報基準")
                continue
            if growth is not None and money(growth) <= money(lot["entry_growth"]) - Decimal(".10"):
                reasons.append("營收成長較進場時下降至少 10 個百分點")
            if margin is not None and money(margin) <= money(lot["entry_margin"]) - Decimal(".05"):
                reasons.append("營益率較進場時下降至少 5 個百分點")
    if reasons:
        status = "觸發出場條件"
    elif unknown:
        status = "資料待確認"
    else:
        status = "未觸發出場條件"
    return {"status": status, "reasons": list(dict.fromkeys(reasons)),
            "unknown": list(dict.fromkeys(unknown)), "symbol": stock["symbol"],
            "asof": stock.get("priceDate"), "financial_period": f.get("latest_period"),
            "observed_at": stock.get("financialObservedAt")}
