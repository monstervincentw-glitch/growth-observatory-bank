"""Read and verify the research-only publication envelope."""
import base64
import csv
import gzip
import hashlib
import io
import json
import re
import tempfile
from threading import Lock
from pathlib import Path

from shared.validation import as_time, health, require, validate
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
    return validate(snapshot)


def parse_envelope(rows, now=None, expected=None):
    require(rows and rows[0] == HEADER and 2 <= len(rows) <= 300, "資料封套不完整")
    records = rows[1:]
    batch, _, count, checksum, _ = records[0]
    require(re.fullmatch(r"v29-[0-9a-f]{16}", batch) and str(count) == str(len(records))
            and re.fullmatch(r"[0-9a-f]{64}", checksum), "批次不完整")
    encoded = []
    for index, (bid, part, parts, sha, text) in enumerate(records, 1):
        require(bid == batch and str(part) == str(index) and str(parts) == str(len(records)) and sha == checksum
                and re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", text) and len(text) <= 18000, "批次或分段不一致")
        encoded.append(text)
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode("".join(encoded), validate=True))) as stream:
        raw = stream.read(8_000_001)
    require(len(raw) <= 8_000_000, "研究快照超過大小限制")
    require(hashlib.sha256(raw).hexdigest() == checksum, "SHA-256 驗證失敗")
    return dict(snapshot=validate(json.loads(raw), now, expected), batch=batch, checksum=checksum)


def parse_rows(rows):
    entry = parse_envelope(rows)
    return entry["snapshot"], entry["batch"]


class SnapshotReader:
    def __init__(self, cache_path=None):
        self.cache_path = Path(cache_path) if cache_path else None
        self.last = None
        self.lock = Lock()

    def read(self, fetch, now=None, expected=None):
        with self.lock:
            if self.last is None and self.cache_path and self.cache_path.exists():
                try:
                    self.last = parse_envelope(json.loads(self.cache_path.read_text()), now)
                except (OSError, ValueError, KeyError, TypeError, EOFError):
                    pass
            try:
                rows = fetch()
                incoming = parse_envelope(rows, now, expected)
                if self.last:
                    before, after = self.last["snapshot"], incoming["snapshot"]
                    require(as_time(after["generatedAt"]) >= as_time(before["generatedAt"])
                            and all(after["markets"][m]["macro"]["marketAsOf"] >= b["macro"]["marketAsOf"]
                                    for m, b in before["markets"].items())
                            and (incoming["batch"] != self.last["batch"] or incoming["checksum"] == self.last["checksum"]),
                            "發布批次倒退或同批內容不同")
                if self.cache_path and (not self.last or incoming["checksum"] != self.last["checksum"]):
                    self.save(rows)
                self.last = incoming
                status = health(self.last, "sheets", now=now, expected=expected)
            except Exception as error:
                status = health(self.last, "last_verified" if self.last else "unavailable", str(error), now, expected)
            return self.last, status

    def save(self, rows):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=self.cache_path.parent, delete=False) as temp:
            try:
                json.dump(rows, temp)
                temp.close()
                Path(temp.name).replace(self.cache_path)
            finally:
                Path(temp.name).unlink(missing_ok=True)


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
        raw = response.read(2_000_001)
    require(len(raw) <= 2_000_000, "研究快照超過大小限制")
    return list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
