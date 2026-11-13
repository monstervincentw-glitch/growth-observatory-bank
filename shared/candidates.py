"""Presentation of the published research and formal flags."""
from .validation import number

RESEARCH = "背離研究候選"
FORMAL = "背離正式買入訊號"
PENDING = "待驗證"
SCOPES = ["研究候選", RESEARCH, FORMAL, PENDING, "主策略正式候選", "全部研究池"]


def candidate_status(stock, macro, signal_available, market_reason=None):
    evaluation = stock["evaluation"]
    research = bool((stock["researchRank"] or {}).get("divergence"))
    formal = signal_available and evaluation["buy_hold"]
    pending = []
    blocked = []
    if not signal_available:
        pending.append(market_reason or "共用研究批次待驗證")
    if stock.get("marketEvent"):
        blocked.append(stock["marketEvent"]["summary"])
    elif not evaluation["fresh"]:
        pending.append(stock.get("sourceError") or "行情待核對")
    if stock["financial"]["financial_status"] != "ok":
        pending.append("財報必要欄位不足")
    elif not evaluation["fundamental"]:
        blocked.extend(evaluation["fundamentalReasons"])
    if not evaluation["filingVerified"]:
        pending.append("官方財報申報時間、版本或收盤前最新申報未驗證")
    if not evaluation["tradable"]:
        for reason in evaluation["reasons"]:
            if reason not in evaluation["fundamentalReasons"] and any(term in reason for term in
                    ("交易日", "成交額", "市值", "P/S", "加速度", "股數")):
                blocked.append(reason)
    technical = stock["features"]
    if not all(number(technical.get(k)) for k in ("drawdown_126", "relative_63")):
        pending.append("技術背離資料不足")
    elif not evaluation["divergence"]:
        if not -.5 <= technical["drawdown_126"] <= -.1:
            blocked.append("126 日回撤未落在 −50% 至 −10%")
        if technical["relative_63"] >= 0:
            blocked.append("63 日相對報酬未低於基準")
    legacy = macro.get("legacyStress")
    if macro["status"] != "vintage_verified" or not number(legacy):
        pending.append("舊制資金分數待驗證" + ("（通膨年增基期缺值）" if
            macro["categoryStates"]["inflation"] is None and macro.get("cpi_yoy") is None else ""))
    elif legacy > 1:
        blocked.append("舊制資金壓力超過 1 分")
    return {
        "research": research,
        "formal": formal,
        "pending": bool(pending),
        "label": "符合正式條件" if formal else "待驗證" if pending else "未達條件",
        "reasons": [] if formal else list(dict.fromkeys(pending + blocked)),
    }


def in_scope(stock, state, scope, signal_available):
    return {
        "研究候選": stock["researchRank"] is not None,
        RESEARCH: state["research"],
        FORMAL: state["formal"],
        PENDING: state["pending"],
        "主策略正式候選": signal_available and stock["evaluation"]["primary"],
        "全部研究池": True,
    }.get(scope, False)
