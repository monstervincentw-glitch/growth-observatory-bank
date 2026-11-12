"""Read and verify the research-only publication envelope."""
import base64
import csv
import gzip
import hashlib
import io
import json
import re
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

BASE = Path(__file__).resolve().parent
HEADER = ["batch_id", "part", "parts", "sha256", "payload_json"]
SHEET_ID = "1I9Alvyhg4UZXi-AdylJN0pjiLHSMzZUleFK0vsXG1K0"
COUNTS = {"US": 89, "TW": 80}


def read_gzip(path):
    with gzip.open(BASE / path, "rt", encoding="utf-8") as file:
        return json.load(file)


def check(snapshot):
    if snapshot.get("schema") != 1 or snapshot.get("historyThrough") != "2026-11-04":
        raise ValueError("資料版本不符")
    if set(snapshot) != {"schema", "sourceVersion", "historyThrough", "generatedAt", "qa", "markets", "jobs"}:
        raise ValueError("研究欄位不符")
    for market, count in COUNTS.items():
        block = snapshot["markets"][market]
        stocks, macro = block["stocks"], block["macro"]
        if len(stocks) != count or len({stock["symbol"] for stock in stocks}) != count:
            raise ValueError("研究池筆數錯誤")
        lights = list(macro["categoryStates"].values())
        if len(lights) != 3 or any(value not in (0, 1, None) for value in lights):
            raise ValueError("燈號欄位錯誤")
        low, high = lights.count(1), lights.count(1) + lights.count(None)
        if macro["scoreRange"] != [low, high] or macro["decision"] != ("red" if low >= 2 else "non_red" if high < 2 else "unknown"):
            raise ValueError("資金溫度不一致")
        ranks = sorted((s["researchRank"] for s in stocks if s["researchRank"]), key=lambda r: r["rank"])
        for index, rank in enumerate(ranks, 1):
            if rank["rank"] != index or rank["date"] != macro["marketAsOf"]:
                raise ValueError("排名版本不一致")
            points = sum(x["weight"] * x["percentile"] * 100 for x in rank["detail"]["parts"])
            if abs(points - rank["score"]) > 1e-8:
                raise ValueError("評分不一致")
    return snapshot


def parse_rows(rows):
    if not rows or rows[0] != HEADER or len(rows) < 2:
        raise ValueError("資料封套不完整")
    records = [r for r in rows[1:] if any(r)]
    batch, _, count, checksum, _ = records[0]
    if not re.fullmatch(r"v29-[0-9a-f]{16}", batch) or len(records) != int(count):
        raise ValueError("批次不完整")
    encoded = []
    for index, (bid, part, parts, sha, text) in enumerate(records, 1):
        if bid != batch or int(part) != index or int(parts) != len(records) or sha != checksum:
            raise ValueError("分段批次不一致")
        encoded.append(text)
    raw = gzip.decompress(base64.b64decode("".join(encoded), validate=True))
    if hashlib.sha256(raw).hexdigest() != checksum:
        raise ValueError("資料校驗失敗")
    return check(json.loads(raw)), batch


def fetch_rows(public_csv_url, service_account=None):
    if service_account:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2.service_account import Credentials
        credentials = Credentials.from_service_account_info(dict(service_account),
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        with AuthorizedSession(credentials) as session:
            response = session.get(
                f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/bank_snapshot!A1:E300",
                params={"valueRenderOption": "UNFORMATTED_VALUE"}, timeout=15)
            response.raise_for_status()
            return response.json()["values"]
    if not public_csv_url:
        raise ValueError("未設定 Sheets 讀取網址")
    target = urlparse(public_csv_url)
    valid_publish = target.hostname == "docs.google.com" and target.path.startswith("/spreadsheets/d/e/") and target.path.endswith("/pub")
    valid_script = target.hostname == "script.google.com" and re.fullmatch(r"/macros/s/[A-Za-z0-9_-]+/exec", target.path)
    if target.scheme != "https" or not (valid_publish or valid_script):
        raise ValueError("Sheets 發布網址不符")
    with urlopen(public_csv_url, timeout=15) as response:
        raw = response.read(2_000_000)
    return list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
