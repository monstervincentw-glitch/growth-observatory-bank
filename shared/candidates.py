"""Live market observation filters. This does not replay historical orders."""

MAIN = "主策略"
DIVERGENCE = "背離策略"
ALL = "全部股票"
SCOPES = [MAIN, DIVERGENCE, ALL]


def candidate_status(stock, macro, signal_available, market_reason=None):
    """Use the latest *published* observations, never historical filing-time gates.

    Entry eligibility is a stock-level condition, NOT a simulated A/B account order.
    Exits shown here are common-condition checks; comparing to an actual entry
    financial version still requires the account's position state.
    """
    e = stock["evaluation"]
    financial = stock["financial"]
    rank = stock.get("researchRank")
    reasons = list(e.get("fundamentalReasons") or [])
    macro_decision = macro.get("decision", "unknown")
    fresh = bool(signal_available and e["fresh"])
    quote_ok = bool(stock.get("integrity") and not stock.get("sourceError")
                    and not stock.get("marketEvent"))
    entry_core = bool(fresh and quote_ok and e["fundamental"] and e["tradable"] and rank)
    buy_ready = entry_core and macro_decision == "non_red"

    if not signal_available:
        entry_label = "資料待驗證"
    elif not fresh or not quote_ok:
        entry_label = "行情待核對"
    elif financial.get("financial_status") != "ok":
        entry_label = "財報資料不足"
    elif not e["fundamental"]:
        entry_label = "基本面未達門檻"
    elif not e["tradable"]:
        entry_label = "交易條件不符"
    elif macro_decision == "red":
        entry_label = "資金紅燈・不新增"
    elif macro_decision != "non_red":
        entry_label = "資金待確認・不新增"
    elif not rank:
        entry_label = "排名待核對"
    else:
        entry_label = "符合進場條件"

    if not fresh or not quote_ok:
        exit_label = "行情待核對"
    elif financial.get("financial_status") != "ok":
        exit_label = "財報不足・需檢查"
    elif reasons:
        exit_label = "基本面轉弱・檢查出場"
    elif macro_decision == "red":
        exit_label = "資金紅燈・檢查出場"
    elif macro_decision != "non_red":
        exit_label = "資金待確認"
    else:
        exit_label = "共通出場條件未觸發"

    if macro_decision == "red":
        reasons.append("資金溫度達紅燈出場門檻")
    elif macro_decision != "non_red":
        reasons.append("資金溫度待確認，不能推定安全")
    if not fresh:
        reasons.append(market_reason or "研究資料待更新或驗證")

    divergence_research = bool(rank and e["divergence"])
    return {
        "buy_ready": buy_ready,
        "entry_core": entry_core,
        "entry_label": entry_label,
        "exit_label": exit_label,
        "exit_watch": bool(e.get("fundamentalReasons") and fresh and quote_ok),
        "divergence_research": divergence_research,
        "reasons": list(dict.fromkeys(reasons)),
        "funding": macro_decision,
    }


def in_scope(stock, state, scope, signal_available):
    if scope == MAIN:
        # Include possible entry names plus existing-stock fundamentals to review.
        return stock.get("researchRank") is not None or state["exit_watch"]
    if scope == DIVERGENCE:
        return state["divergence_research"]
    if scope == ALL:
        return True
    return False
