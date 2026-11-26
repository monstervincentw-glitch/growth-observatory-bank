"""Pure portfolio accounting and forward-looking research decisions.

No order execution, no invented prices, and no historical backfill.
Inputs are verified current research snapshots and owner-recorded transactions.

Position limits and the 10% sleeve come from performance/engine/equity_ab.py
together with performance/primary_engine/*/config.json. Exit thresholds match
engine/rules.ts (the publisher) and the primary event engines. Do not tune them.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

ZERO = Decimal("0")
ONE = Decimal("1")
# Frozen official thresholds. Not styling knobs.
MAX_CORE = 10
ENTRY_FRACTION = Decimal("0.10")
MIN_ENTRY_FRACTION = Decimal("0.02")
EXIT_GROWTH_DROP = Decimal("0.10")
EXIT_MARGIN_DROP = Decimal("0.05")
MAX_FINANCIAL_AGE_DAYS = 180
UNCONFIRMED_REASONS = {
    "財報欄位不足，暫停判斷",
    "財報距季末超過180天",
}


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


def owner_allowed(email, verified, allowed):
    """Only an explicitly listed, verified Google account may pass."""
    if verified is not True and str(verified).strip().lower() not in {"true", "1"}:
        return False
    normalized = str(email or "").strip().casefold()
    allowed_set = {part.strip().casefold() for part in str(allowed or "").split(",") if part.strip()}
    return bool(normalized and normalized in allowed_set)


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
                "at": tx["at"], "ledger_id": tx.get("id", ""),
                "entry_growth": tx.get("entry_growth", ""),
                "entry_margin": tx.get("entry_margin", ""),
                "entry_filing_period": tx.get("entry_filing_period", ""),
                "baseline_filed": tx.get("baseline_filed", ""),
                "baseline_source": tx.get("baseline_source", ""),
                "baseline_version": tx.get("baseline_version", ""),
                "baseline_verification": tx.get("baseline_verification", ""),
                "baseline_reason": tx.get("baseline_reason", ""),
                "baseline_note": tx.get("baseline_note", ""),
                "ledger_entry_growth": tx.get("ledger_entry_growth", ""),
                "ledger_entry_margin": tx.get("ledger_entry_margin", ""),
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


def _flows(transactions, portfolio_id, start_exclusive, end_inclusive):
    """External cash only. Buys and sells stay inside the portfolio."""
    total = ZERO
    dated = []
    for tx in transactions:
        if str(tx.get("portfolio_id")) != str(portfolio_id):
            continue
        action = str(tx.get("action", "")).upper()
        if action not in ("DEPOSIT", "WITHDRAW"):
            continue
        day = str(tx.get("trade_date") or "")[:10]
        if not day:
            continue
        if start_exclusive is not None and day <= start_exclusive:
            continue
        if day > end_inclusive:
            continue
        amount = _positive(tx.get("cash_amount"), action.lower())
        signed = amount if action == "DEPOSIT" else -amount
        total += signed
        dated.append((day, signed))
    return total, dated


def contributions_through(transactions, portfolio_id, through_date):
    total, _ = _flows(transactions, portfolio_id, None, through_date)
    return total


def modified_dietz(transactions, portfolio_id, nav_rows):
    """Chain verified closes with Modified Dietz. This is not standard TWR.

    Standard TWR needs a valuation at each cash-flow instant. These closes do
    not provide that, so the result stays an estimate: opening capital is the
    external cash already in the account, and later flows are day-weighted.
    Missing dates are never inserted.
    """
    points = sorted(
        (r for r in nav_rows if r.get("date") and str(r.get("nav") or "") != ""),
        key=lambda r: r["date"],
    )
    seen = set()
    series = []
    linked_estimate = None
    prev = None
    for row in points:
        if row["date"] in seen:
            raise ValueError("同一組合同一天不能有兩筆淨值")
        seen.add(row["date"])
        nav = money(row["nav"])
        if prev is None:
            base = contributions_through(transactions, portfolio_id, row["date"])
            if base <= ZERO:
                return None
            ret = nav / base - ONE
            linked_estimate = ret
            flow = base
        else:
            start = date.fromisoformat(prev["date"])
            end = date.fromisoformat(row["date"])
            span = (end - start).days
            if span <= 0:
                raise ValueError("淨值日期必須往後")
            flow, dated = _flows(transactions, portfolio_id, prev["date"], row["date"])
            weighted = ZERO
            for day, amount in dated:
                remaining = (end - date.fromisoformat(day)).days
                weighted += (Decimal(remaining) / Decimal(span)) * amount
            prev_nav = money(prev["nav"])
            denom = prev_nav + weighted
            if prev_nav <= ZERO or denom <= ZERO:
                return None
            ret = (nav - prev_nav - flow) / denom
            linked_estimate = (ONE + linked_estimate) * (ONE + ret) - ONE
        series.append({
            "date": row["date"], "nav": nav, "external_flow": flow,
            "subperiod_return": ret, "modified_dietz": linked_estimate,
        })
        prev = row
    return series


def nav_extension(transactions, portfolio_id, existing_rows, session_date, nav, cash, equity):
    """Fields for one new verified close. Returns None if that date already exists.

    Does not mutate existing rows and does not create a row for a missing day.
    """
    session_date = str(session_date)
    if any(str(r.get("date")) == session_date for r in existing_rows):
        return None
    nav, cash, equity = money(nav), money(cash), money(equity)
    if nav < 0 or equity < 0 or cash < 0 or abs(nav - cash - equity) > Decimal("0.01"):
        raise ValueError("NAV、現金與股票市值無法對帳")
    proposed = [*existing_rows, {
        "date": session_date, "nav": str(nav), "cash": str(cash), "equity_value": str(equity),
    }]
    series = modified_dietz(transactions, portfolio_id, proposed)
    linked = next((p for p in (series or []) if p["date"] == session_date), None)
    prior = [r for r in existing_rows if str(r.get("date") or "") < session_date and str(r.get("nav") or "")]
    prior = max(prior, key=lambda r: r["date"]) if prior else None
    daily_pnl = ""
    daily_return = ""
    if prior and linked is not None:
        flow, _ = _flows(transactions, portfolio_id, prior["date"], session_date)
        daily_pnl = format(nav - money(prior["nav"]) - flow, "f")
        daily_return = format(linked["subperiod_return"], "f")
    cumulative = ""
    contributed = contributions_through(transactions, portfolio_id, session_date)
    if contributed > ZERO:
        cumulative = format(nav - contributed, "f")
    # The sheet column is named twr. Do not store a Dietz estimate there.
    note = "區間報酬是 Modified Dietz 估計，不是標準 TWR；缺日不補值，存提不計入報酬"
    return {
        "daily_pnl": daily_pnl,
        "daily_return": daily_return,
        "cumulative_pnl": cumulative,
        "twr": "",
        "note": note,
    }


def entry_eligible(stock):
    """Same current-observation entry gate as the bank's shared candidates view.

    Historical filing-time eligibility remains in the historical engines.
    The caller checks the portfolio funding state before allocating.
    """
    e = stock.get("evaluation") or {}
    return bool(stock.get("integrity") and not stock.get("sourceError")
                and not stock.get("marketEvent") and stock.get("researchRank")
                and e.get("fundamental") and e.get("tradable") and e.get("fresh")
                and stock.get("financial", {}).get("financial_status") == "ok")


def _rank_score(stock):
    rank = (stock or {}).get("researchRank") or {}
    if rank.get("score") in (None, ""):
        return None
    return money(rank["score"])


def _allocate(cash, nav, ranked, limit, *, full_sleeve_only):
    """Core entries may use leftover cash down to 2% of NAV. B extras may not."""
    target = nav * ENTRY_FRACTION
    minimum = nav * MIN_ENTRY_FRACTION
    proposed = []
    for stock in ranked:
        if len(proposed) >= limit:
            break
        if full_sleeve_only:
            if cash + Decimal("0.00000001") < target:
                break
            budget = target
        else:
            budget = min(cash, target)
            if budget + Decimal("0.000000001") < minimum:
                break
        proposed.append((stock, budget))
        cash -= budget
    return proposed, target


def buy_plan(account, valued, market_block, variant="A"):
    """Evaluate A core slots first; B extras only after core is exactly full.

    A new extra must score strictly above every core holding. Suggestions use
    published closes and are not orders.
    """
    if variant not in ("A", "B"):
        raise ValueError("Only main strategy A/B supported")
    macro = market_block["macro"]
    if valued["nav"] is None:
        return {"status": "估值不完整，暫停建議", "candidates": [], "mode": None}
    if macro.get("decision") != "non_red":
        return {"status": "資金紅燈或狀態待確認，不新增", "candidates": [], "mode": None}
    stocks = {s["symbol"]: s for s in market_block["stocks"]}
    holdings = account["lots"]
    core = {s for s, rows in holdings.items() if any(l["role"] == "core" for l in rows)}
    if len(core) > MAX_CORE:
        return {"status": "核心持股超過 10 檔，需人工確認", "candidates": [], "mode": None}
    ranked = sorted((s for s in market_block["stocks"] if entry_eligible(s)
                     and s["symbol"] not in holdings),
                    key=lambda s: (s["researchRank"]["rank"], s["symbol"]))
    threshold = None
    if len(core) < MAX_CORE:
        mode, limit, full_only = "core", MAX_CORE - len(core), False
    elif variant == "A":
        return {"status": "A 核心已滿 10 檔，保留現金", "candidates": [],
                "mode": "none", "core_count": len(core)}
    else:
        scores = []
        for symbol in core:
            score = _rank_score(stocks.get(symbol))
            if score is None:
                return {"status": "核心持股有評分或資料缺口，無法核實 B 加碼門檻",
                        "candidates": [], "mode": None, "core_count": len(core)}
            scores.append(score)
        threshold = max(scores)
        ranked = [s for s in ranked if _rank_score(s) > threshold]
        mode, limit, full_only = "extra", len(ranked), True
    proposed, target = _allocate(
        valued["cash"], valued["nav"], ranked, limit, full_sleeve_only=full_only)
    if not proposed and valued["cash"] < target:
        status = "可用現金不足以建立完整 10% 目標部位" if full_only else "可用現金低於 2% 淨值，不新增"
    elif proposed:
        status = "符合候選檢查；實際交易前需確認開盤及成本"
    else:
        status = "本次無可新增股票"
    return {"status": status, "mode": mode, "core_count": len(core),
            "threshold": threshold, "target": target,
            "candidates": [{"symbol": s["symbol"], "name": s.get("name", ""),
                            "score": s["researchRank"]["score"],
                            "price_date": s["priceDate"], "target": budget,
                            "role": mode} for s, budget in proposed]}


def divergence_plan(account, valued, market_block):
    """Buy-hold divergence candidates. Confirmed only when published buy_hold is true.

    Holding rules are independent: do not apply the main strategy's forced exits.
    """
    if valued["nav"] is None:
        return {"status": "估值不完整，暫停背離候選", "candidates": [], "hold_rule": _HOLD_RULE}
    if market_block["macro"].get("status") != "vintage_verified":
        return {"status": "資金序列尚未驗證，不產生背離買入訊號", "candidates": [], "hold_rule": _HOLD_RULE}
    held = set(account["lots"])
    missing_flag = False
    ranked = []
    for stock in market_block["stocks"]:
        if stock["symbol"] in held:
            continue
        evaluation = stock.get("evaluation") or {}
        if "buy_hold" not in evaluation:
            missing_flag = True
            continue
        if evaluation.get("buy_hold") is True and stock.get("researchRank"):
            ranked.append(stock)
    if missing_flag and not ranked:
        return {"status": "背離進場旗標缺失，不產生買入訊號", "candidates": [], "hold_rule": _HOLD_RULE}
    ranked.sort(key=lambda s: (s["researchRank"]["rank"], s["symbol"]))
    slots = max(0, MAX_CORE - len(held))
    proposed, target = _allocate(
        valued["cash"], valued["nav"], ranked, max(slots, len(ranked)), full_sleeve_only=False)
    sized = {s["symbol"]: budget for s, budget in proposed[:slots]}
    names = [{"symbol": s["symbol"], "name": s.get("name", ""),
              "score": s["researchRank"]["score"], "price_date": s["priceDate"],
              "target": sized.get(s["symbol"])} for s in ranked]
    if not names:
        status = "目前沒有可確認的新背離候選"
    elif slots <= 0:
        status = "研究上有背離候選；此組合已滿 10 檔，不建議再加"
    else:
        status = "背離研究候選；不會自動下單，持有規則獨立於主策略"
    return {"status": status, "target": target, "hold_rule": _HOLD_RULE, "candidates": names}


_HOLD_RULE = ("背離長抱僅在進場同時要求背離、基本面、可交易，以及已驗證的舊版資金壓力不大於 1。"
              "持有後不套用主策略的營收、利潤率或資金紅燈強制出場。")


def funding_decision(macro):
    """Official 2-of-3 funding rule. Unknown is never treated as non-red.

    Category states, when present, are the same tri-state inputs the publisher
    stores. A bare non_red label without those states is not enough.
    """
    states = macro.get("categoryStates") if isinstance(macro, dict) else None
    keys = ("rates", "conditions", "inflation")
    if isinstance(states, dict) and all(key in states for key in keys):
        flags = []
        for key in keys:
            raw = states[key]
            if raw in (1, "1", True):
                flags.append(1)
            elif raw in (0, "0", False):
                flags.append(0)
            else:
                flags.append(None)
        triggered = sum(flag == 1 for flag in flags)
        ceiling = triggered + sum(flag is None for flag in flags)
        if triggered >= 2:
            return "red"
        if ceiling < 2:
            return "non_red"
        return "unknown"
    if isinstance(macro, dict) and macro.get("decision") == "red":
        return "red"
    return "unknown"


def _first_lot(holding):
    return min(holding["lots"], key=lambda lot: (str(lot.get("at") or ""), str(lot.get("entry_filing_period") or "")))


def _stale_financial(financial):
    age = financial.get("financial_age_days")
    if age in (None, ""):
        return True
    try:
        return money(age) > MAX_FINANCIAL_AGE_DAYS
    except ValueError:
        return True


def exit_review(holding, macro):
    """Main-strategy A/B exit. Missing inputs stay 無法判定, not a clean pass."""
    stock = holding["stock"]
    financial = stock.get("financial") or {}
    reasons = []
    unknown = []
    if not stock.get("integrity") or stock.get("sourceError"):
        unknown.append("無法判定：行情來源未確認")
    funding = funding_decision(macro)
    if funding == "red":
        reasons.append("全球資金溫度已達紅燈門檻")
    elif funding != "non_red":
        unknown.append("無法判定：全球資金溫度未知，不能當成未觸發或綠燈")
    published = [str(item) for item in (stock.get("evaluation") or {}).get("fundamentalReasons") or []]
    incomplete = financial.get("financial_status") != "ok"
    stale = _stale_financial(financial)
    if incomplete:
        unknown.append("無法判定：最新財報必要欄位不完整")
    if stale:
        unknown.append("無法判定：財報超過 180 天或無法確認時效，暫停確定出場訊號")
    financial_unconfirmed = incomplete or stale or any(item in UNCONFIRMED_REASONS for item in published)
    for item in published:
        if item in UNCONFIRMED_REASONS or financial_unconfirmed:
            unknown.append("無法判定：" + item)
        else:
            reasons.append(item)
    lot = _first_lot(holding)
    growth_known = lot.get("entry_growth") not in (None, "")
    margin_known = lot.get("entry_margin") not in (None, "")
    growth = financial.get("revenue_growth")
    margin = financial.get("operating_margin")
    if not growth_known or not margin_known:
        why = lot.get("baseline_reason") or "最早一筆持股沒有官方申報日可證明的建倉財報基準"
        unknown.append("無法判定：" + why)
    elif financial_unconfirmed:
        unknown.append("無法判定：最新財報不足以和建倉基準比較營收成長與營益率")
    else:
        if growth is not None and money(growth) <= money(lot["entry_growth"]) - EXIT_GROWTH_DROP:
            reasons.append("營收成長較進場時下降至少 10 個百分點")
        if margin is not None and money(margin) <= money(lot["entry_margin"]) - EXIT_MARGIN_DROP:
            reasons.append("營益率較進場時下降至少 5 個百分點")
        if growth in (None, ""):
            unknown.append("無法判定：最新營收成長率缺失")
        if margin in (None, ""):
            unknown.append("無法判定：最新營益率缺失")
    if reasons:
        status = "觸發出場條件"
    elif unknown:
        status = "無法判定"
    else:
        status = "未觸發出場條件"
    change = {}
    if growth_known and growth not in (None, ""):
        change["growth_pp"] = str((money(growth) - money(lot["entry_growth"])) * 100)
    if margin_known and margin not in (None, ""):
        change["margin_pp"] = str((money(margin) - money(lot["entry_margin"])) * 100)
    return {"status": status, "reasons": list(dict.fromkeys(reasons)),
            "unknown": list(dict.fromkeys(unknown)), "symbol": stock["symbol"],
            "asof": stock.get("priceDate"), "financial_period": financial.get("latest_period"),
            "observed_at": stock.get("financialObservedAt"),
            "baseline": {
                "growth": lot.get("entry_growth") or "",
                "margin": lot.get("entry_margin") or "",
                "period": lot.get("entry_filing_period") or "",
                "filed": lot.get("baseline_filed") or "",
                "source": lot.get("baseline_source") or "",
                "version": lot.get("baseline_version") or "",
                "verification": lot.get("baseline_verification") or "",
                "note": lot.get("baseline_note") or "",
                "reason": lot.get("baseline_reason") or "",
            },
            "latest": {"growth": "" if growth is None else growth,
                       "margin": "" if margin is None else margin,
                       "period": financial.get("latest_period") or ""},
            "change": change}
