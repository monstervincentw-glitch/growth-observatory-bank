"""One daily research writer shared by both sites; no private ledger in inputs."""
from __future__ import annotations
import base64
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import threading
import time
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BOOK = "1I9Alvyhg4UZXi-AdylJN0pjiLHSMzZUleFK0vsXG1K0"
ROOT = Path(__file__).resolve().parent.parent
STATUS = ["market", "session_date", "status", "stage", "completed", "expected", "attempts",
          "at", "error", "next_retry", "financial_reused", "financial_refreshed", "batch"]
RUNTIME = ["scope", "part", "parts", "sha256", "payload"]
START_LOCK = threading.Lock()
STARTED = False


def now():
    return datetime.now(timezone.utc).isoformat()


def get_json(url):
    with urlopen(Request(url, headers={"User-Agent": "GrowthObservatory/1.0 daily research"}), timeout=30) as response:
        data = response.read(20_000_001)
    if len(data) > 20_000_000:
        raise ValueError("Source response too large")
    return json.loads(data)


def ensure_node():
    """Pin the official runtime; download once per deployment, with its checksum."""
    target = ROOT / ".runtime" / "node"
    if target.exists():
        return str(target)
    version = "24.11.1"
    name = f"node-v{version}-linux-x64.tar.xz"
    origin = f"https://nodejs.org/dist/v{version}/"
    with urlopen(origin + "SHASUMS256.txt", timeout=30) as response:
        checks = dict(line.split()[::-1] for line in response.read().decode().splitlines() if line.strip())
    with urlopen(origin + name, timeout=60) as response:
        raw = response.read(80_000_001)
    if len(raw) > 80_000_000 or hashlib.sha256(raw).hexdigest() != checks.get(name):
        raise ValueError("Official Node checksum failed")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:xz") as archive:
        executable = archive.extractfile(f"node-v{version}-linux-x64/bin/node").read()
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(executable)
    target.chmod(0o755)
    return str(target)


def unpack(rows):
    if rows[0] != RUNTIME:
        raise ValueError("Runtime header mismatch")
    groups = {}
    for row in rows[1:]:
        if not row or not row[0]:
            continue
        scope, part, count, digest, text = row
        groups.setdefault(scope, []).append((int(part), int(count), digest, text))
    result = {}
    for scope, records in groups.items():
        records.sort()
        if [r[0] for r in records] != list(range(1, len(records) + 1)) or any(r[1] != len(records) for r in records):
            raise ValueError("Incomplete runtime checkpoint")
        raw = gzip.decompress(base64.b64decode("".join(r[3] for r in records)))
        if any(r[2] != hashlib.sha256(raw).hexdigest() for r in records):
            raise ValueError("Runtime checksum mismatch")
        result[scope] = json.loads(raw)
    return result


def pack(value):
    rows = [RUNTIME]
    for scope, item in sorted(value.items()):
        raw = json.dumps(item, separators=(",", ":"), ensure_ascii=False).encode()
        encoded = base64.b64encode(gzip.compress(raw)).decode()
        chunks = [encoded[i:i + 18000] for i in range(0, len(encoded), 18000)]
        digest = hashlib.sha256(raw).hexdigest()
        rows.extend([scope, index, len(chunks), digest, chunk] for index, chunk in enumerate(chunks, 1))
    return rows


def write_rows(book, rectangle, rows):
    if rectangle == "pipeline_runtime!A1:E500":
        if len(rows) > 500:
            raise ValueError("Runtime checkpoint row limit")
        rows = rows + [["", "", "", "", ""]] * (500 - len(rows))
    book.values_update(rectangle, params={"valueInputOption": "RAW"}, body={"values": rows})


def tab(book, name, headers, rows=500):
    from gspread.exceptions import WorksheetNotFound
    try:
        result = book.worksheet(name)
    except WorksheetNotFound:
        result = book.add_worksheet(name, rows=rows, cols=len(headers))
        write_rows(book, f"'{name}'!A1", [headers])
    actual = book.values_get(f"'{name}'!A1:{chr(64 + len(headers))}1").get("values", [])
    if actual != [headers]:
        raise ValueError(name + " header mismatch")
    return result


def finance_versions(rows):
    result = {}
    for row in rows:
        symbol = str(row.get("symbol", row.get("公司代號", row.get("SecuritiesCompanyCode", "")))).strip()
        if not symbol:
            continue
        period = row.get("period")
        if not period:
            year = int(row.get("年度", row.get("Year"))) + 1911
            quarter = int(row.get("季別", row.get("Season")))
            period = f"{year}-{quarter * 3:02d}-{'31' if quarter in (1, 4) else '30'}"
        revenue = str(row.get("revenue", row.get("營業收入", ""))).replace(",", "")
        operating = str(row.get("operatingProfit", row.get("營業利益（損失）", ""))).replace(",", "")
        # Fingerprints detect a new version; they do not replace the saved audit.
        result[symbol] = {"period": period, "token": "|".join((period, revenue, operating))}
    return result


def latest_finance(runtime, market, day, seed):
    if market == "US":
        return {}
    if seed["officialDay"] == day:
        return seed["TW"]["financialVersions"]
    if runtime.get("officialDay") == day:
        return runtime["financialVersions"]
    rows = []
    # These are the SAME two bulk official sources already used by this project.
    for url in ("https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci",
                "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci"):
        rows.extend(get_json(url))
    tracked = set(seed["TW"]["financialVersions"])
    rows = [r for r in rows if str(r.get("公司代號", r.get("SecuritiesCompanyCode", ""))).strip() in tracked]
    versions = finance_versions(rows)
    # Income-period equality alone cannot detect cash-flow corrections. Reuse
    # the project's existing official material-announcement feeds as a version
    # change signal, then let the original financial provider refresh the data.
    checked = runtime.get("financialCheckedAt", seed["TW"].get("financialCheckedAt", ""))
    notices = []
    for url in ("https://openapi.twse.com.tw/v1/opendata/t187ap04_L",
                "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O"):
        notices.extend(get_json(url))
    changed = {}
    for row in notices:
        symbol = str(row.get("公司代號", row.get("SecuritiesCompanyCode", ""))).strip()
        title = str(row.get("主旨", row.get("主旨 ", "")))
        if symbol not in tracked or not any(word in title for word in ("財務報告", "財報", "現金流量", "重編")):
            continue
        raw_day = str(row.get("發言日期", "")).replace("/", "").replace("-", "")
        if len(raw_day) == 7:
            raw_day = str(int(raw_day[:3]) + 1911) + raw_day[3:]
        raw_time = str(row.get("發言時間", "00:00:00")).replace(":", "").zfill(6)
        try:
            instant = datetime.strptime(raw_day + raw_time, "%Y%m%d%H%M%S").replace(
                tzinfo=ZoneInfo("Asia/Taipei")).astimezone(timezone.utc).isoformat()
        except ValueError:
            raise ValueError("官方財報公告時間格式異常：" + symbol)
        if instant > checked:
            changed.setdefault(symbol, []).append(instant + "|" + title)
    for symbol, version in versions.items():
        saved = runtime.get("financialVersions", {}).get(symbol, {})
        notice = saved.get("notice", "")
        if symbol in changed:
            notice = hashlib.sha256("|".join(sorted(changed[symbol])).encode()).hexdigest()
        version["notice"] = notice
        if notice:
            version["token"] += "|" + notice
    return versions


def run_cycle(client, node, seed):
    from research import decode_envelope
    book = client.open_by_key(BOOK)
    # Check the saved cohort independently of quote refresh. A completed close
    # must not suppress the daily membership audit. No second scheduler/writer.
    from pool_check import audit, envelope as pool_envelope
    checked_entry = decode_envelope(book.values_get("bank_snapshot!A1:E300").get("values", []))
    audited = audit(checked_entry["snapshot"], now())
    if audited is not None:
        rows = pool_envelope(audited)
        incoming = decode_envelope(rows)
        latest = decode_envelope(book.values_get("bank_snapshot!A1:E300").get("values", []))
        if latest["checksum"] != checked_entry["checksum"]:
            raise RuntimeError("股票池檢核期間已有較新批次，等下次接續")
        write_rows(book, "bank_snapshot!A1:E300", rows + [["", "", "", "", ""]] * (300 - len(rows)))
        print(json.dumps({"pool_check": {market: incoming["snapshot"]["markets"][market]["macro"]["poolCheck"]["status"]
                                       for market in ("US", "TW")}}), flush=True)
    rows = book.values_get("market_latest!A1:I3").get("values", [])
    if not rows:
        return
    quotes = {row[0]: json.loads(row[8]) for row in rows[1:] if len(row) == 9 and row[8]}
    current_entry = decode_envelope(book.values_get("bank_snapshot!A1:E300").get("values", []))
    needs_us_live = any(not stock.get("marketEvent") and stock["financial"].get("date_basis") != "LIVE_OBSERVED_SEC"
                        for stock in current_entry["snapshot"]["markets"]["US"]["stocks"])
    if not needs_us_live and all(batch["day"] <= current_entry["snapshot"]["markets"][market]["macro"]["marketAsOf"]
           for market, batch in quotes.items()):
        return  # No history downloads, checkpoint reads, or source requests after success.
    tab(book, "pipeline_runtime", RUNTIME)
    tab(book, "pipeline_status", STATUS, 20)
    stored = book.values_get("pipeline_runtime!A1:E500").get("values", [RUNTIME])
    checkpoints = unpack(stored) if len(stored) > 1 else {m: dict(seed[m]) for m in ("US", "TW")}
    for market in ("US", "TW"):
        if not checkpoints[market].get("bars"):
            archived = get_json(seed[market]["historySeed"])
            checkpoints[market]["bars"] = {symbol: q["bars"] for symbol, q in archived["quotes"].items()}
    for market in ("US", "TW"):
        if market not in quotes:
            continue
        batch = quotes[market]
        entry = decode_envelope(book.values_get("bank_snapshot!A1:E300").get("values", []))
        day, state = batch["day"], checkpoints.setdefault(market, seed[market])
        cutoff = entry["snapshot"]["markets"][market]["macro"]["marketAsOf"]
        refresh_financial = market == "US" and needs_us_live
        if day <= cutoff and not refresh_financial:
            continue
        if state.get("attemptDay") != day:
            state.update(attemptDay=day, attempts=0, nextRetry=0)
        if state.get("attempts", 0) >= 3 or time.time() < state.get("nextRetry", 0):
            continue
        state["attempts"] += 1
        state["nextRetry"] = time.time() + 20 * 60
        # Persist attempt before doing network work; restarts cannot reset limits.
        write_rows(book, "pipeline_runtime!A1:E500", pack(checkpoints))
        def status(result, stage, error="", outcome=None):
            row = [market, day, result, stage, len(batch["quotes"]) if result == "COMPLETE" else 0,
                   batch["expected"], state["attempts"], now(), error[:500],
                   state.get("nextRetry", 0), (outcome or {}).get("financialReused", ""),
                   (outcome or {}).get("financialRefreshed", ""), (outcome or {}).get("batch", "")]
            write_rows(book, f"pipeline_status!A{2 if market == 'US' else 3}:M{2 if market == 'US' else 3}", [row])
        status("RUNNING", "daily_update")
        try:
            versions = latest_finance(state, market, day, seed)
            payload = {"market": market, "batch": batch, "prior": {"snapshot": entry["snapshot"]},
                       "runtime": state, "financialVersions": versions, "refreshFinancial": refresh_financial}
            with tempfile.TemporaryDirectory(prefix="daily-research-") as directory:
                source, target = Path(directory) / "input.json", Path(directory) / "output.json"
                source.write_text(json.dumps(payload))
                environment = dict(os.environ, NODE_USE_ENV_PROXY="1")
                result = subprocess.run([node, "--max-old-space-size=192", str(ROOT / "engine/pipeline-job.mjs"), str(source), str(target)],
                                        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=600)
                if result.returncode:
                    raise RuntimeError((result.stderr or result.stdout)[-1500:])
                outcome = json.loads(target.read_text())
            if outcome.get("unchanged"):
                continue
            incoming = decode_envelope(outcome["rows"])
            if incoming["snapshot"]["markets"][market]["macro"]["marketAsOf"] != day:
                raise ValueError("Calculated session does not match captured prices")
            # Compare again immediately before publication, preventing a stale
            # worker from rolling back another market or a newer publication.
            current = decode_envelope(book.values_get("bank_snapshot!A1:E300").get("values", []))
            if current["checksum"] != entry["checksum"]:
                raise RuntimeError("研究批次已由其他工作更新，等下一次接續")
            status("RUNNING", "publishing")
            padded = outcome["rows"] + [["", "", "", "", ""]] * (300 - len(outcome["rows"]))
            write_rows(book, "bank_snapshot!A1:E300", padded)
            checked_at = (seed["TW"].get("financialCheckedAt") if market == "TW" and seed["officialDay"] == day else now())
            state.update(outcome["runtime"], officialDay=day, financialCheckedAt=checked_at,
                         attempts=state["attempts"], nextRetry=0)
            write_rows(book, "pipeline_runtime!A1:E500", pack(checkpoints))
            status("COMPLETE", "published", outcome=outcome)
            print(json.dumps({"pipeline": "published", "market": market, "day": day,
                              "count": len(batch["quotes"]), "batch": outcome["batch"]}), flush=True)
            # Reuse the existing owner-only monitor. It never writes US NAV;
            # that remains owned by the Google capture task.
            try:
                from monitor_job import run
                run()
            except Exception as error:
                print(json.dumps({"pipeline_monitor": type(error).__name__}), flush=True)
        except Exception as error:
            status("FAILED", "daily_update", str(error))
            print(json.dumps({"pipeline": "failed", "market": market, "day": day,
                              "error": str(error)[:500]}), flush=True)


def loop():
    from storage import sheets_client
    try:
        node = ensure_node()
        seed = json.loads((Path(__file__).parent / "pipeline_seed.json").read_text())
        client = sheets_client(json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]))
    except Exception as error:
        print(json.dumps({"pipeline_start": type(error).__name__, "error": str(error)[:300]}), flush=True)
        return
    while True:
        try:
            local = datetime.now(ZoneInfo("Asia/Taipei"))
            # Google heartbeat wakes the existing free service only while work
            # is pending. No background AI/API use and no new paid service.
            if 6 <= local.hour <= 23:
                run_cycle(client, node, seed)
        except Exception as error:
            print(json.dumps({"pipeline_cycle": type(error).__name__, "error": str(error)[:300]}), flush=True)
        time.sleep(300)


def start_pipeline():
    global STARTED
    with START_LOCK:
        if STARTED:
            return
        STARTED = True
        threading.Thread(target=loop, daemon=True, name="daily-research").start()
