"""Read bank research; prefer owner-shared Google Sheet, safely fall back to GitHub.

Never change public research; all choices operate on actual first-observed data.
"""
from __future__ import annotations
import base64
from datetime import datetime, timezone, timedelta
import gzip
import hashlib
import io
import json
import re

import requests

BANK_SHEET_ID = "1I9Alvyhg4UZXi-AdylJN0pjiLHSMzZUleFK0vsXG1K0"
BANK_RELEASE = ("https://raw.githubusercontent.com/"
                "monstervincentw-glitch/growth-observatory-bank/research-data/release.json")
HEADER = ["batch_id","part","parts","sha256","payload_json"]
EXPECTED = {"US":89,"TW":80}


def decode_envelope(rows):
    if not isinstance(rows,list) or len(rows)<2 or rows[0]!=HEADER:
        raise ValueError("研究資料封套或標頭無效")
    records=rows[1:]
    if not all(len(row)==5 for row in records):
        raise ValueError("研究資料缺漏欄位")
    batch,count,digest=records[0][0],int(records[0][2]),records[0][3]
    if not re.fullmatch(r"v29-[0-9a-f]{16}",str(batch)) or count!=len(records):
        raise ValueError("研究資料批次不完整")
    if not re.fullmatch(r"[0-9a-f]{64}",str(digest)):
        raise ValueError("研究雜湊格式錯誤")
    parts=[]
    for offset,row in enumerate(records,1):
        bid,part,total,sha,data=row
        if (bid!=batch or int(part)!=offset or int(total)!=count or
                sha!=digest or not isinstance(data,str) or len(data)>18000 or
                not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}",data)):
            raise ValueError("研究資料混合批次或分段不一致")
        parts.append(data)
    compressed=base64.b64decode("".join(parts),validate=True)
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        raw=stream.read(8_000_001)
    if len(raw)>8_000_000 or hashlib.sha256(raw).hexdigest()!=digest:
        raise ValueError("研究資料檢查碼失敗")
    snapshot=json.loads(raw)
    validate_snapshot(snapshot)
    return {"snapshot":snapshot,"batch":batch,"checksum":digest}


def validate_snapshot(snapshot):
    if snapshot.get("schema")!=1 or snapshot.get("sourceVersion")!=29:
        raise ValueError("研究快照版本錯誤")
    generated=datetime.fromisoformat(snapshot["generatedAt"].replace("Z","+00:00"))
    if generated.tzinfo is None or generated>datetime.now(timezone.utc)+timedelta(minutes=5):
        raise ValueError("研究快照發布時間異常")
    for market,count in EXPECTED.items():
        block=snapshot["markets"][market]
        stocks=block["stocks"]
        if len(stocks)!=count or len({s["symbol"] for s in stocks})!=count:
            raise ValueError(market+" 研究池數量不符")
        if block["macro"]["marketComplete"] is not True:
            raise ValueError(market+" 研究池並非完整批次")
        cutoff=block["macro"]["marketAsOf"]
        for s in stocks:
            if s["market"]!=market or s["key"]!=f'{market}:{s["symbol"]}':
                raise ValueError(market+" 股票標識不一致")
            if s.get("integrity") is not True or s.get("sourceError"):
                raise ValueError(market+" 研究行情驗證失敗")
            if s.get("marketEvent") is None and s.get("priceDate")!=cutoff:
                raise ValueError(market+" 研究股票行情日期不符")
    return snapshot


def load_from_sheet(client):
    book=client.open_by_key(BANK_SHEET_ID)
    rows=book.worksheet("bank_snapshot").get_all_values()
    return decode_envelope(rows)


def load_from_github(session=None):
    session=session or requests.Session()
    response=session.get(BANK_RELEASE,timeout=16,
                         headers={"Cache-Control":"no-cache","Accept":"application/json"})
    response.raise_for_status()
    data=response.json()
    return decode_envelope(data["rows"])


def latest_research(client, github_fallback=True):
    """Return latest validated snapshot; flag Sheet lag instead of hiding it."""
    sheet, github, issues=None,None,[]
    try:
        sheet=load_from_sheet(client)
    except Exception as exc:
        issues.append("銀行版 Google Sheet 讀取失敗："+str(exc)[:180])
    if github_fallback:
        try:
            github=load_from_github()
        except Exception as exc:
            issues.append("銀行版 GitHub 備援讀取失敗："+str(exc)[:180])
    if not sheet and not github:
        raise RuntimeError("無法取得經檢查碼驗證的銀行版研究快照："+"；".join(issues))
    if not sheet:
        return github,"GitHub 備援",issues
    if github:
        sheet_days={k:sheet["snapshot"]["markets"][k]["macro"]["marketAsOf"]
                    for k in EXPECTED}
        github_days={k:github["snapshot"]["markets"][k]["macro"]["marketAsOf"]
                     for k in EXPECTED}
        if any(github_days[k]>sheet_days[k] for k in EXPECTED):
            if all(github_days[k]>=sheet_days[k] for k in EXPECTED):
                issues.append("共用 Google Sheet 落後銀行版 GitHub，已改用較新正式快照")
                return github,"GitHub 更新優先",issues
            issues.append("市場日期交叉落後，請人工核對，不混用不同版本")
    return sheet,"Google Sheet",issues
