"""Validation of the research-only publication contract used by both sites."""
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

CONTRACT = json.loads(Path(__file__).with_name("research-contract.json").read_text())
MARKETS = CONTRACT["counts"]
TAIPEI = ZoneInfo("Asia/Taipei")
UTC = timezone.utc
PRIVATE_FIELDS = {k.casefold() for k in CONTRACT["privateFields"]}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def as_date(value):
    parsed = date.fromisoformat(value)
    require(parsed.isoformat() == value, "日期格式不符")
    return parsed


def as_time(value):
    parsed = datetime.fromisoformat(value)
    require("T" in value and parsed.tzinfo is not None, "時間格式不符")
    return parsed


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def verify_public(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            simulation = len(path) == 4 and path[0] == "markets" and path[2] == "marks"
            require(key.casefold() not in PRIVATE_FIELDS
                    and (key != "portfolios" or simulation), "研究快照包含私人欄位")
            verify_public(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            verify_public(child, (*path, index))


def validate(snapshot, now=None, expected=None):
    now = now or datetime.now(UTC)
    expected = expected or {}
    require(set(snapshot) == {"schema", "sourceVersion", "historyThrough", "generatedAt", "qa", "markets", "jobs"}
            and snapshot["schema"] == CONTRACT["schema"] and snapshot["sourceVersion"] == CONTRACT["sourceVersion"]
            and snapshot["historyThrough"] == CONTRACT["historyThrough"] and set(snapshot["markets"]) == set(MARKETS)
            and isinstance(snapshot["jobs"], list) and (as_time(snapshot["generatedAt"]) - now).total_seconds() <= 300,
            "研究資料版本、欄位或時間無效")
    verify_public(snapshot)
    for market, count in MARKETS.items():
        block = snapshot["markets"][market]
        stocks, macro, signal = block["stocks"], block["macro"], block["signalAt"]
        cutoff = macro["marketAsOf"]
        zone = ZoneInfo("America/New_York") if market == "US" else TAIPEI
        generated = as_time(snapshot["generatedAt"])
        require(set(block) == {"stocks", "macro", "marks", "signalAt"} and isinstance(block["marks"], dict)
                and macro["marketComplete"] is True and macro["ruleVersion"] == CONTRACT["strategy"]
                and as_date(cutoff) <= now.astimezone(zone).date() and as_date(cutoff).weekday() < 5
                and cutoff >= expected.get(market, cutoff) and as_time(macro["lastMarketPublishedAt"]) <= generated
                and (signal is None or as_time(signal) <= generated)
                and len(stocks) == count and len({s["symbol"] for s in stocks}) == count,
                f"{market} 市場快照不完整或交易日不符")
        categories = macro["categoryStates"]
        lights = list(categories.values())
        low, high = lights.count(1), lights.count(1) + lights.count(None)
        decision = "red" if low >= 2 else "non_red" if high < 2 else "unknown"
        require(set(categories) == {"rates", "conditions", "inflation"}
                and all(x is None or (type(x) is int and x in (0, 1)) for x in lights)
                and macro["scoreRange"] == [low, high] and macro["decision"] == decision
                and ((macro["status"] == "vintage_verified" and macro["macro_stress"] == low) if low == high
                     else (macro["status"] == "macro_incomplete" and macro["macro_stress"] is None)),
                f"{market} 資金狀態不一致")
        ranks = []
        for stock in stocks:
            f, e, r = stock["financial"], stock["evaluation"], stock["researchRank"]
            price, event = stock["priceDate"], stock["marketEvent"]
            symbol = stock["symbol"]
            key = f"{market}:{symbol}"
            inactive = event and price < event["effectiveDate"] <= cutoff and as_date(event["effectiveDate"])
            require(isinstance(symbol, str) and symbol and stock["market"] == market and stock["key"] == key
                    and as_date(price) and number(stock["close"]) and stock["close"] > 0 and stock["integrity"] is True
                    and not stock.get("sourceError") and ((r is None and e["fresh"] is False) if inactive else
                    (not event and price == cutoff and stock["priceValidation"]["cutoff"] == cutoff and e["fresh"] is True)),
                    f"{key} 行情不完整或交易日不一致")
            period, filed = f.get("latest_period"), f.get("max_input_filed")
            require(as_date(period) <= as_date(price) and f["fundamental_release_key"].startswith(period + "|")
                    and ((as_date(price) - as_date(filed)).days >= 2 if market == "US" else
                         not filed or as_date(filed) <= as_date(price))
                    and (f["financial_status"] != "ok" or all(number(f.get(k)) for k in
                         ("revenue_growth", "quarterly_growth", "operating_margin", "cfo_margin"))),
                    f"{key} 財報期別、欄位或版本無效")
            evidence = f.get("filingEvidence", {})
            if market == "TW":
                close = as_time(price + "T13:30:00+08:00")
                verified = bool(evidence and evidence["period"] == period and evidence["releaseKey"] == f["fundamental_release_key"]
                                and as_time(evidence["publishedAt"]) <= close <= as_time(evidence["verifiedThrough"])
                                and evidence["source"].startswith("https://"))
                require(e["filingVerified"] is verified, f"{key} 申報驗證旗標不一致")
            technical = stock["features"]
            drawdown, relative = technical.get("drawdown_126"), technical.get("relative_63")
            divergence = number(drawdown) and -.5 <= drawdown <= -.1 and number(relative) and relative < 0
            eligible = e["fundamental"] and e["filingVerified"] and e["tradable"] and e["fresh"]
            legacy = macro.get("legacyStress")
            require(all(type(e[k]) is bool for k in CONTRACT["flags"]) and e["funding"] == (decision == "non_red")
                    and e["divergence"] == divergence and (not e["fundamental"] or f["financial_status"] == "ok")
                    and bool(r) == (e["fundamental"] and e["tradable"] and e["fresh"])
                    and e["primary"] == (eligible and e["funding"])
                    and e["buy_hold"] == (eligible and divergence and macro["status"] == "vintage_verified"
                                         and number(legacy) and legacy <= 1), f"{key} 策略旗標不一致")
            if r is None:
                continue
            parts, weights = r["detail"]["parts"], CONTRACT["weights"][market]
            require(r["date"] == cutoff and r["archived"] is False and r["divergence"] is divergence
                    and r["detail"]["financialPeriod"] == period and r["detail"]["filingDate"] == (evidence.get("publishedAt") or filed)
                    and number(r["score"]) and len(parts) == len(weights) and {p["key"] for p in parts} == set(weights)
                    and all(number(p["value"]) and number(p["weight"]) and abs(p["weight"] - weights[p["key"]]) < 1e-12
                            and number(p["percentile"]) and 0 <= p["percentile"] <= 1 for p in parts)
                    and abs(r["score"] - sum(p["weight"] * p["percentile"] * 100 for p in parts)) < 1e-8,
                    f"{key} 排名、因子或財報版本不一致")
            ranks.append(r)
        ranks.sort(key=lambda r: r["rank"])
        require(all(r["rank"] == i+1 and (i == 0 or r["score"] <= ranks[i-1]["score"] + 1e-8)
                    for i, r in enumerate(ranks)), f"{market} 排名順序不一致")
    return snapshot


def health(entry, source, issue=None, now=None, expected=None, attempts=None):
    now = now or datetime.now(UTC)
    expected, attempts = expected or {}, attempts or {}
    result = {}
    for market in MARKETS:
        block = entry["snapshot"]["markets"][market] if entry else None
        cutoff = block["macro"]["marketAsOf"] if block else None
        published = [record.get(market.lower() + "_close") for record in attempts.values()
                     if record.get("status") == "published" and record.get(market.lower() + "_close")]
        stale = bool(cutoff and ((now.astimezone(TAIPEI).date() - as_date(cutoff)).days > CONTRACT["maxAgeDays"]
                                 or cutoff < expected.get(market, cutoff)
                                 or any(cutoff < close for close in published)))
        attempt = attempts.get(market, {})
        failed = block and attempt.get("status") == "failed" and as_time(attempt["at"]) > as_time(block["macro"]["lastMarketPublishedAt"])
        detail = attempt.get("detail") if failed else None
        reason = issue or (("市場發布失敗，保留前批" + (f"｜{detail[:400]}" if isinstance(detail, str) and detail else ""))
                           if failed else "行情已過期或落後應有交易日" if stale else None)
        result[market] = dict(date=cutoff, stale=stale, reason=reason,
                              usable=source == "sheets" and not reason and bool(block and block["signalAt"]),
                              financialIncomplete=sum(s["financial"]["financial_status"] != "ok" for s in block["stocks"]) if block else None,
                              filingPending=sum(not s["evaluation"]["filingVerified"] for s in block["stocks"]) if block else None,
                              macro=block["macro"]["status"] if block else None)
    return dict(source=source, batch=entry["batch"] if entry else None, checksum=entry["checksum"] if entry else None,
                strategy=CONTRACT["strategy"], issue=issue, markets=result)
