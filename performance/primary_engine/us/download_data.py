"""Public-source acquisition. Cached raw bytes + SHA256 audit manifest; no API key.

Yahoo is an unofficial research price source. Verify licensing before business use.
SEC identity can be supplied with SEC_USER_AGENT (name and contact recommended).
"""
from __future__ import annotations
import argparse, concurrent.futures, hashlib, io, json, os, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data/raw"

def get(url, path, user_agent="NasdaqGrowthResearch/0.1 personal research", refresh=False):
    path = Path(path)
    if path.exists() and not refresh:
        return path.read_bytes()
    error = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(request, timeout=40) as response:
                content = response.read()
            if not content or content[:30].lower().startswith(b"<!doctype html"):
                raise ValueError("Expected data, received empty/HTML response")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            metadata = {"url": url, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                        "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
            path.with_suffix(path.suffix + ".meta.json").write_text(json.dumps(metadata, indent=2))
            return content
        except Exception as exc:
            error = exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(str(error))

def chart_url(symbol, start, end):
    a = int(pd.Timestamp(start, tz="UTC").timestamp())
    b = int((pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=2)).timestamp())
    return (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
            f"?period1={a}&period2={b}&interval=1d&events=div%2Csplits")

def acquire_prices(symbol, cfg, refresh=False):
    p = RAW / f"{symbol}_chart.json"
    url = chart_url(symbol, cfg["price_start"], cfg["end"])
    try:
        data = json.loads(get(url, p, "Mozilla/5.0 NasdaqGrowthResearch/0.1", refresh))
        item = data["chart"]["result"][0]
        n = len(item["timestamp"])
        return {"symbol": symbol, "prices": "ok", "sessions": n}
    except Exception as exc:
        return {"symbol": symbol, "prices": "failed", "price_error": str(exc)}

def acquire_facts(row, cfg, refresh=False):
    symbol, cik = row
    if cik is None:
        return {"symbol": symbol, "facts": "missing_current_SEC_mapping"}
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json"
    try:
        content = get(url, RAW / f"{symbol}_facts.json", os.getenv("SEC_USER_AGENT", "NasdaqGrowthResearch/0.1 personal research"), refresh)
        data = json.loads(content)
        if "facts" not in data:
            raise ValueError("Missing facts key")
        time.sleep(.3)  # two workers, comfortably below SEC's 10 requests/s limit
        return {"symbol": symbol, "facts": "ok", "cik": int(cik)}
    except Exception as exc:
        return {"symbol": symbol, "facts": "failed", "facts_error": str(exc)}

def acquire_macro(cfg, refresh=False):
    """Each column is a vintage at that exact signal date, not today's revision."""
    qqq = load_chart(RAW / "QQQ_chart.json")
    calendar = qqq.index[(qqq.index >= pd.Timestamp(cfg["start"]) - pd.Timedelta(days=40)) &
                         (qqq.index <= pd.Timestamp(cfg["end"]))]
    dates = [qqq.index[qqq.index < pd.Timestamp(cfg["start"])][-1]]
    dates += list(pd.Series(calendar, index=calendar).groupby(calendar.to_period("M")).last())
    dates = sorted(set(dates))
    audit = []
    for series in cfg["macro_series"]:
        for batch in range(0, len(dates), 10):
            ds = dates[batch:batch+10]
            query = urllib.parse.urlencode({"id": ",".join([series]*len(ds)),
                        "vintage_date": ",".join(d.strftime("%Y-%m-%d") for d in ds)})
            url = "https://alfred.stlouisfed.org/graph/alfredgraph.csv?" + query
            p = RAW / f"alfred_{series}_{batch:03}.csv"
            try:
                content = get(url, p, refresh=refresh)
                df = pd.read_csv(io.BytesIO(content))
                expected = [f"{series}_{d:%Y%m%d}" for d in ds]
                if not set(expected).issubset(df.columns):
                    raise ValueError(f"Unexpected vintage columns {df.columns.tolist()}")
                audit.append({"series": series, "batch": batch, "status": "ok"})
            except Exception as exc:
                audit.append({"series": series, "batch": batch, "status": "failed", "error": str(exc)})
            print(f"macro {series} {batch} {audit[-1]['status']}", flush=True)
    return audit

def load_chart(path):
    obj = json.loads(Path(path).read_text())["chart"]["result"][0]
    quotes = obj["indicators"]["quote"][0]
    df = pd.DataFrame(quotes)
    # US daily bar timestamps map to US calendar dates; UTC dates coincide for regular bars.
    df.index = pd.to_datetime(obj["timestamp"], unit="s", utc=True).tz_convert("America/New_York").normalize().tz_localize(None)
    df["adj_close"] = obj["indicators"]["adjclose"][0]["adjclose"]
    df["adj_open"] = df["open"] * df["adj_close"] / df["close"]
    df = df[~df.index.duplicated()].sort_index()
    df.index.name = "date"
    return df

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--only", choices=["all", "prices", "facts", "macro"], default="all")
    args = parser.parse_args()
    cfg = json.loads((ROOT / "config.json").read_text())
    symbols = pd.read_csv(ROOT / "universe.csv")["symbol"].tolist()
    RAW.mkdir(parents=True, exist_ok=True)
    manifest = {"started_at": datetime.now(timezone.utc).isoformat(), "config_sha256": hashlib.sha256((ROOT/"config.json").read_bytes()).hexdigest()}
    if args.only in ["all", "prices"]:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            items = list(ex.map(lambda s: acquire_prices(s, cfg, args.refresh), ["QQQ", "SOXX", "IGV"] + symbols))
        manifest["prices"] = items
        print("prices", sum(r["prices"] == "ok" for r in items), "/", len(items), flush=True)
    if args.only in ["all", "facts"]:
        mapping = json.loads(get("https://www.sec.gov/files/company_tickers_exchange.json", RAW/"sec_tickers.json"))
        rows = pd.DataFrame(mapping["data"], columns=mapping["fields"])
        lookup = dict(zip(rows.ticker, rows.cik))
        # Acquired issuers absent from today's SEC ticker file retain their historical CIKs.
        lookup.update({"ATVI": 718877, "XLNX": 743988, "CTXS": 877890, "NUAN": 1002517, "CERN": 804753, "ANSS": 1013462, "INFN": 1138639})
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            items = list(ex.map(lambda row: acquire_facts(row, cfg, args.refresh), [(s, lookup.get(s)) for s in symbols]))
        manifest["facts"] = items
        print("facts", sum(r["facts"] == "ok" for r in items), "/", len(items), flush=True)
    if args.only in ["all", "macro"]:
        manifest["macro"] = acquire_macro(cfg, args.refresh)
    path = ROOT/"data"/f"acquisition_{args.only}.json"
    path.write_text(json.dumps(manifest, indent=2))
    print("saved", path, flush=True)

if __name__ == "__main__":
    main()
