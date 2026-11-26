"""Point-in-time entry financials. Official SEC filed date plus two days only.

A later financial-versions observation is not evidence that the figures were
public on the buy date. This module never writes a ledger and never invents a
growth rate when the filing record is incomplete.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import hashlib
import json

FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "6-K"}
TAGS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
        "SalesRevenueNet", "SalesRevenueGoodsNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "op": ["OperatingIncomeLoss"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
}
from tw_entry_basis import ACCEPTED, OBSERVED

DAY = 86400000
OFFICIAL = "OFFICIAL_FILING"


def _ms(day):
    return int(datetime.fromisoformat(str(day)[:10] + "T12:00:00+00:00").timestamp() * 1000)


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _finite(value):
    return type(value) in (int, float) and value == value and value not in (float("inf"), float("-inf"))


def _known(payload, metric, asof):
    cutoff = _iso(_ms(asof) - 2 * DAY)
    floor = _ms(asof) - 1100 * DAY
    rows = []
    facts = payload.get("facts") if isinstance(payload, dict) else None
    if not isinstance(facts, dict):
        return []
    for priority, tag in enumerate(TAGS[metric]):
        for taxonomy in ("us-gaap", "dei", "ifrs-full"):
            units = (((facts.get(taxonomy) or {}).get(tag) or {}).get("units") or {}).get("USD") or []
            for row in units:
                filed = str(row.get("filed") or "")
                end = str(row.get("end") or "")
                if (row.get("form") not in FORMS or not filed or not end or not _finite(row.get("val"))
                        or filed > cutoff or end > asof or _ms(end) < floor):
                    continue
                rows.append({"start": row.get("start") or "", "end": end, "filed": filed,
                             "val": float(row["val"]), "priority": priority})
    rows.sort(key=lambda row: (row["filed"], -row["priority"]), reverse=True)
    # filed desc, then priority asc. reverse=True on filed alone would flip priority, so sort stable in two steps.
    rows.sort(key=lambda row: row["priority"])
    rows.sort(key=lambda row: row["filed"], reverse=True)
    used, kept = set(), []
    for row in rows:
        key = row["start"] + "|" + row["end"]
        if key in used:
            continue
        used.add(key)
        kept.append(row)
    return kept


def _quarters(payload, metric, asof):
    known = []
    for row in _known(payload, metric, asof):
        if not row["start"]:
            continue
        days = round((_ms(row["end"]) - _ms(row["start"])) / DAY) + 1
        known.append({**row, "days": days})
    quarters = []
    for row in known:
        if 60 <= row["days"] <= 120:
            quarters.append({**row, "derived": False})
        elif 140 <= row["days"] <= 400:
            previous = [item for item in known
                        if abs(_ms(item["start"]) - _ms(row["start"])) <= 3 * DAY
                        and 60 * DAY <= _ms(row["end"]) - _ms(item["end"]) <= 120 * DAY
                        and item["days"] < row["days"]]
            previous.sort(key=lambda item: (item["end"], item["filed"]), reverse=True)
            if previous:
                base = previous[0]
                quarters.append({
                    "start": _iso(_ms(base["end"]) + DAY), "end": row["end"],
                    "val": row["val"] - base["val"],
                    "filed": row["filed"] if row["filed"] > base["filed"] else base["filed"],
                    "priority": row["priority"], "derived": True, "days": None,
                })
    quarters.sort(key=lambda row: (row["end"], row["filed"], not row["derived"]))
    # Match the publisher: end asc, filed desc, concrete quarter before a derived one.
    quarters.sort(key=lambda row: row["derived"])
    quarters.sort(key=lambda row: row["filed"], reverse=True)
    quarters.sort(key=lambda row: row["end"])
    used, kept = set(), []
    for row in quarters:
        if row["end"] in used:
            continue
        used.add(row["end"])
        kept.append(row)
    return kept


def sec_financial(payload, asof):
    """Same filed-plus-two-days screen the publisher uses. No fallback estimate."""
    asof = str(asof)[:10]
    revenue = _quarters(payload, "revenue", asof)[-8:]
    if (len(revenue) != 8 or any(row["val"] <= 0 for row in revenue)
            or any(abs(_ms(revenue[i]["end"]) - _ms(revenue[i - 1]["end"])) < 60 * DAY for i in range(1, 8))
            or any(_ms(revenue[i]["end"]) - _ms(revenue[i - 1]["end"]) > 120 * DAY for i in range(1, 8))):
        return {"ok": False, "reason": "SEC 申報不足以還原建倉日已公開的八個季度營收"}
    latest, prior = revenue[-4:], revenue[:4]
    ends = {row["end"] for row in latest}
    total = sum(row["val"] for row in latest)
    prior_total = sum(row["val"] for row in prior)
    if prior_total <= 0 or total <= 0:
        return {"ok": False, "reason": "SEC 營收基準不是正數，不估算成長率"}
    operating = [row for row in _quarters(payload, "op", asof) if row["end"] in ends]
    cash = [row for row in _quarters(payload, "cfo", asof) if row["end"] in ends]
    if len(operating) != 4 or len(cash) != 4:
        return {"ok": False, "reason": "SEC 申報缺少建倉日已公開的四季營業利益或營業現金流"}
    used = [*revenue, *operating, *cash]
    filed = max(row["filed"] for row in used)
    growth = total / prior_total - 1
    margin = sum(row["val"] for row in operating) / total
    cfo = sum(row["val"] for row in cash) / total
    period = revenue[-1]["end"]
    identity = [period, growth, margin, cfo, filed]
    version = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    return {
        "ok": True,
        "revenue_growth": format(growth, ".12g"),
        "operating_margin": format(margin, ".12g"),
        "cfo_margin": format(cfo, ".12g"),
        "filing_period": period,
        "official_published_at": filed,
        "available_on": _iso(_ms(filed) + 2 * DAY),
        "source": "SEC Company Facts",
        "version_id": version,
        "verification": OFFICIAL,
        "detail": "採用不晚於交易日前兩個日曆日已申報的 10-Q／10-K／20-F／6-K／40-F",
    }


def late_observation_reason(version, trade_date):
    """Reject a registry row whose first verification is after the buy date."""
    if not isinstance(version, dict):
        return "沒有可核對的財報版本"
    published = str(version.get("officialPublishedAt") or "")[:10]
    trade_date = str(trade_date)[:10]
    official = (version.get("verification") == OFFICIAL and published
                and published <= trade_date and str(version.get("officialSource") or "").startswith("https://"))
    if official:
        return ""
    seen = str(version.get("firstVerifiedAt") or "")
    if seen and seen[:10] > trade_date:
        return "financial-versions 首次驗證晚於建倉日，且沒有不晚於建倉日的官方公布日期"
    if seen:
        return "財報版本只有事後觀測時間，不能回填成建倉當日已知資料"
    return "財報版本缺少官方公布日期"


def apply_official_baselines(transactions, baselines):
    """Return decision copies. Does not mutate the ledger rows or their dicts."""
    chosen = {}
    for row in baselines or []:
        key = (str(row.get("portfolio_id")), str(row.get("ledger_id")))
        slot = chosen.setdefault(key, {"official": None, "latest": None})
        slot["latest"] = row
        if (row.get("verification") in ACCEPTED and row.get("revenue_growth") not in (None, "")
                and row.get("operating_margin") not in (None, "")):
            if slot["official"] is None:slot["official"] = row
    copies = []
    for tx in transactions:
        copied = dict(tx)
        copied["ledger_entry_growth"] = tx.get("entry_growth") or ""
        copied["ledger_entry_margin"] = tx.get("entry_margin") or ""
        if str(tx.get("action", "")).upper() != "BUY":
            copies.append(copied)
            continue
        slot = chosen.get((str(tx.get("portfolio_id")), str(tx.get("id"))))
        official = slot["official"] if slot else None
        latest = slot["latest"] if slot else None
        if official:
            copied["entry_growth"] = official["revenue_growth"]
            copied["entry_margin"] = official["operating_margin"]
            copied["entry_filing_period"] = official.get("filing_period") or ""
            copied["baseline_filed"] = official.get("official_published_at") or ""
            copied["baseline_source"] = official.get("source") or ""
            copied["baseline_version"] = official.get("version_id") or ""
            copied["baseline_verification"] = official["verification"]
            copied["baseline_note"] = official.get("detail") or ""
            copied["baseline_reason"] = ""
        else:
            copied["entry_growth"] = ""
            copied["entry_margin"] = ""
            copied["entry_filing_period"] = ""
            copied["baseline_filed"] = ""
            copied["baseline_source"] = (latest or {}).get("source") or ""
            copied["baseline_version"] = (latest or {}).get("version_id") or ""
            copied["baseline_verification"] = (latest or {}).get("verification") or ""
            copied["baseline_reason"] = (latest or {}).get("detail") or "沒有官方申報日可證明的建倉財報基準"
        copies.append(copied)
    return copies


def _cik_index(stocks):
    found = {}
    for stock in stocks or []:
        symbol = str(stock.get("symbol") or "").upper()
        cik = str(stock.get("cik") or "").strip()
        if symbol and cik.isdigit():
            found[symbol] = cik.zfill(10)
    return found


def resolve_buy_baseline(symbol, trade_date, *, market, payload=None, registry_version=None, cik="", recorded_at=None):
    """Resolve one buy. payload=None means the caller could not fetch SEC facts."""
    trade_date = str(trade_date)[:10]
    symbol = str(symbol or "").upper()
    late = late_observation_reason(registry_version, trade_date) if registry_version else ""
    if market == "TW":
        from tw_entry_basis import load_registry, resolve
        try:return resolve(symbol,trade_date,registry_version if registry_version is not None else load_registry(),recorded_at)
        except Exception as error:return _unverified(symbol,trade_date,"","台股封存財報版本讀取失敗："+type(error).__name__)
    if market != "US":
        return _unverified(symbol, trade_date, cik, "台股沒有可回推至建倉日的官方申報時間，不估算財報基準")
    if late and late.startswith("financial-versions"):
        # The registry itself is disqualified. SEC facts can still prove an earlier filing.
        pass
    if payload is None:
        detail = late or "尚未取得 SEC Company Facts，不能把較晚的觀測版本當成建倉基準"
        return _unverified(symbol, trade_date, cik, detail)
    result = sec_financial(payload, trade_date)
    if not result["ok"]:
        return _unverified(symbol, trade_date, cik, result["reason"])
    if result["available_on"] > trade_date:
        return _unverified(symbol, trade_date, cik, "官方申報加上兩個日曆日後仍晚於建倉日")
    result.update({"symbol": symbol, "trade_date": trade_date, "cik": cik})
    return result


def _unverified(symbol, trade_date, cik, detail):
    code = "NO_PIT_" + hashlib.sha256(detail.encode()).hexdigest()[:16]
    return {
        "ok": False, "symbol": symbol, "trade_date": trade_date, "cik": cik,
        "revenue_growth": "", "operating_margin": "", "filing_period": "",
        "official_published_at": "", "source": "SEC Company Facts" if cik else "",
        "version_id": code, "verification": "UNVERIFIED", "detail": detail,
    }


def missing_baseline_rows(transactions, saved, stocks, facts_for_cik, tw_registry=None):
    """Audit rows still required. facts_for_cik(cik) returns a payload or raises."""
    official = {(str(row.get("portfolio_id")), str(row.get("ledger_id")))
                for row in saved or [] if row.get("verification") in ACCEPTED}
    ciks = _cik_index(stocks)
    rows = []
    for tx in transactions:
        if str(tx.get("action", "")).upper() != "BUY":
            continue
        key = (str(tx.get("portfolio_id")), str(tx.get("id")))
        if key in official:
            continue
        symbol = str(tx.get("symbol") or "").upper()
        trade_date = str(tx.get("trade_date") or "")[:10]
        market = str(tx.get("market") or "")
        if market not in ("US", "TW"):
            market = "US" if symbol in ciks else ""
        cik = ciks.get(symbol, "")
        if market == "TW":
            resolved=resolve_buy_baseline(symbol,trade_date,market="TW",registry_version=tw_registry,recorded_at=tx.get("at"))
            rows.append({**resolved,"portfolio_id":key[0],"ledger_id":key[1]})
            continue
        if market != "US":
            detail = ("台股沒有可回推至建倉日的官方申報時間，不估算財報基準" if market == "TW"
                      else "交易沒有標明美股或台股，不估算財報基準")
            rows.append({**_unverified(symbol, trade_date, "", detail),
                         "portfolio_id": key[0], "ledger_id": key[1]})
            continue
        if not cik:
            rows.append({**_unverified(symbol, trade_date, "", "研究宇宙沒有這檔的唯一 SEC CIK，不估算"),
                         "portfolio_id": key[0], "ledger_id": key[1]})
            continue
        try:
            payload = facts_for_cik(cik)
        except Exception:
            rows.append({**_unverified(symbol, trade_date, cik, "SEC Company Facts 這次沒有取回，稍後再試"),
                         "portfolio_id": key[0], "ledger_id": key[1]})
            continue
        resolved = resolve_buy_baseline(symbol, trade_date, market="US", payload=payload, cik=cik)
        rows.append({**resolved, "portfolio_id": key[0], "ledger_id": key[1]})
    return rows


def fetch_company_facts(cik, session=None):
    """Public SEC filing facts. The caller must not log the response body."""
    return _cached_facts(str(cik).zfill(10))


def _load_company_facts(cik):
    import requests
    response = requests.get(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
        timeout=40,
        headers={
            "User-Agent": "growth-observatory-private-baseline/1.0 (point-in-time filing check)",
            "Accept": "application/json",
        },
    )
    response.raise_for_status()
    return response.json()


_cached_facts = __import__("functools").lru_cache(maxsize=32)(_load_company_facts)


def attach_portfolio_market(transactions, portfolios):
    markets = {str(row["id"]): row.get("market") for row in portfolios}
    attached = []
    for tx in transactions:
        copied = dict(tx)
        copied["market"] = markets.get(str(tx.get("portfolio_id")), "")
        attached.append(copied)
    return attached


def sync_entry_baselines(store, transactions, portfolios, snapshot):
    """Append missing audit rows. Does not change ledger cells."""
    stocks = [stock for market in ("US", "TW") for stock in snapshot["markets"][market]["stocks"]]
    attached = attach_portfolio_market(transactions, portfolios)
    rows = missing_baseline_rows(attached, store.entry_baselines(), stocks, fetch_company_facts)
    return store.append_entry_baselines(rows)
