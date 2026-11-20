"""Audit the original saved research cohorts. Never infer membership from failed sources.

The archived US/TW configs describe manual cohorts, not an index selection rule.
This check therefore reports drift for review, without inventing additions, removals,
execution dates or sales. The daily worker is the only writer; both sites read the
same signed research envelope. No financial or portfolio source is accessed here.
"""
from __future__ import annotations
import base64
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from zoneinfo import ZoneInfo

SOURCE = ('https://raw.githubusercontent.com/monstervincentw-glitch/'
          'growth-observatory-bank/main/data/snapshot.json.gz')
RULES = {
    'US': 'performance/primary_engine/us/config.json:universe_type',
    'TW': 'performance/primary_engine/tw/config.json:universe',
}
LIMIT = 8_000_000


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('股票池檢核時間缺少時區')
    return result


def cohort(block, market):
    stocks = block['stocks']
    if not isinstance(stocks, list) or not stocks or len(stocks) > 10000:
        raise ValueError('原始名單不是完整非空批次')
    symbols = []
    for item in stocks:
        symbol = item['symbol']
        if (not isinstance(symbol, str) or not symbol or item.get('market') != market
                or item.get('key') != market + ':' + symbol
                or any(not item.get(k) for k in ('ticker', 'name', 'industry', 'benchmark', 'currency'))):
            raise ValueError('原始名單標識或必要資料不完整')
        symbols.append(symbol)
    if len(set(symbols)) != len(symbols):
        raise ValueError('原始名單有重複標的')
    return set(symbols)


def decode_source(raw):
    if len(raw) > LIMIT:
        raise ValueError('股票池來源超過限制')
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        unpacked = stream.read(LIMIT + 1)
    if len(unpacked) > LIMIT:
        raise ValueError('股票池來源解壓超過限制')
    source = json.loads(unpacked)
    if source.get('schema') != 1 or source.get('sourceVersion') != 29:
        raise ValueError('原始研究名單版本不符')
    stamp(source['generatedAt'])
    # Validate BOTH markets before accepting either. An empty/partial download
    # can never be interpreted as a mass removal.
    if set(source['markets']) != set(RULES):
        raise ValueError('股票池來源市場不完整')
    for market in RULES:
        cohort(source['markets'][market], market)
    return source


def fetch_source():
    import requests
    with requests.get(SOURCE, timeout=(10, 30), stream=True) as response:
        response.raise_for_status()
        raw = bytearray()
        for chunk in response.iter_content(65536):
            raw.extend(chunk)
            if len(raw) > LIMIT:
                raise ValueError('股票池來源超過限制')
    return decode_source(bytes(raw)), hashlib.sha256(raw).hexdigest()


def due(previous, instant):
    if not previous:
        return True
    at = stamp(instant)
    checked = stamp(previous['checkedAt'])
    if at.astimezone(ZoneInfo('Asia/Taipei')).date() != checked.astimezone(ZoneInfo('Asia/Taipei')).date():
        return True
    return (previous.get('status') in ('SOURCE_FAILED', 'CHECKING')
            and previous.get('attempts', 0) < 3
            and (at - checked).total_seconds() >= 1200)


def comparison(current, source, market, instant, digest, previous=None):
    old = cohort(current['markets'][market], market)
    new = cohort(source['markets'][market], market)
    added, removed = sorted(new - old), sorted(old - new)
    changes = [{'symbol': s, 'change': action, 'effectiveDate': None,
                'reason': '原保存名單與正式批次不同；缺少原增刪規則與生效依據，尚未套用',
                'applied': False}
               for action, symbols in (('待確認新增', added), ('待確認移除', removed)) for s in symbols]
    identity = hashlib.sha256(json.dumps([market, sorted(old), sorted(new)],
                                         separators=(',', ':')).encode()).hexdigest()
    return {'schema': 1, 'status': 'PENDING_RULES' if changes else 'MATCHED_BASELINE',
            'checkedAt': instant, 'verifiedAt': instant, 'attempts': 1,
            'source': SOURCE, 'sourceHash': digest, 'sourceAsOf': source['generatedAt'],
            'ruleReference': RULES[market], 'scope': '原保存人工研究名單；非指數即時成分檢核',
            'automaticMembershipEnabled': False, 'applied': False,
            'currentCount': len(old), 'sourceCount': len(new), 'comparisonId': identity,
            'changes': changes, 'error': None,
            'limitation': '原回測未保存動態股票池篩選來源及移出出場規則；尚未啟用自動增刪、接入新標的或移出出場。'}


def audit(snapshot, instant, loader=fetch_source):
    """Return a new snapshot only when due; preserve last verified diff on failure."""
    wanted = [market for market in RULES
              if due(snapshot['markets'][market]['macro'].get('poolCheck'), instant)]
    if not wanted:
        return None
    result = json.loads(json.dumps(snapshot))
    try:
        source, digest = loader()
        # Test-injected loaders must satisfy the same complete-batch rules.
        if set(source['markets']) != set(RULES):
            raise ValueError('股票池來源市場不完整')
        if stamp(source['generatedAt']) > stamp(instant):
            raise ValueError('股票池來源版本時間晚於檢查時間')
        for market in RULES:
            cohort(source['markets'][market], market)
        for market in wanted:
            result['markets'][market]['macro']['poolCheck'] = comparison(
                snapshot, source, market, instant, digest)
    except Exception as error:
        for market in wanted:
            prior = snapshot['markets'][market]['macro'].get('poolCheck') or {}
            attempts = prior.get('attempts', 0) if prior.get('checkedAt') and stamp(prior['checkedAt']).astimezone(ZoneInfo('Asia/Taipei')).date() == stamp(instant).astimezone(ZoneInfo('Asia/Taipei')).date() else 0
            result['markets'][market]['macro']['poolCheck'] = {
                **prior, 'schema': 1, 'status': 'SOURCE_FAILED', 'checkedAt': instant,
                'attempts': attempts + 1, 'source': SOURCE,
                'automaticMembershipEnabled': False, 'applied': False,
                'error': type(error).__name__,
                'limitation': '來源失敗，保留上次成功檢核及完整股票池；不視為整池移除。'}
    result['generatedAt'] = instant
    return result


def envelope(snapshot):
    raw = json.dumps(snapshot, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    encoded = base64.b64encode(gzip.compress(raw)).decode()
    chunks = [encoded[i:i + 18000] for i in range(0, len(encoded), 18000)]
    batch = 'v29-' + digest[:16]
    rows = [['batch_id', 'part', 'parts', 'sha256', 'payload_json']]
    rows.extend([batch, i, len(chunks), digest, part] for i, part in enumerate(chunks, 1))
    if len(rows) > 300:
        raise ValueError('研究快照超過發布限制')
    return rows


def render_checks(st, snapshot):
    """Call only INSIDE the existing collapsed update details."""
    st.markdown('**股票池名單檢核**')
    for market, label in (('US', '美股'), ('TW', '台股')):
        check = snapshot.get('markets', {}).get(market, {}).get('macro', {}).get('poolCheck')
        if not check:
            st.caption(label + '：尚未有每日股票池檢核紀錄。')
            continue
        state = {'MATCHED_BASELINE': '與原保存名單一致', 'PENDING_RULES': '發現名單差異，待原規則確認',
                 'SOURCE_FAILED': '來源失敗，保留上次成功結果', 'CHECKING': '檢核中'}.get(check.get('status'), '待確認')
        st.caption(f"{label}：{state} · 檢查時間 {check.get('checkedAt', '未知')}")
        st.caption(check.get('scope', '原保存研究名單檢核'))
        st.caption(check.get('limitation', ''))
        if check.get('sourceAsOf'):
            st.caption('名單來源版本時間：' + check['sourceAsOf'] + ' · 原規則 ' + check['ruleReference'])
        changes = check.get('changes') or []
        if changes:
            st.dataframe([{'代號': r['symbol'], '差異': r['change'], '原因': r['reason'],
                           '生效日期': r.get('effectiveDate') or '未確認', '已套用': '否'}
                          for r in changes], hide_index=True, use_container_width=True)
        elif check.get('status') == 'MATCHED_BASELINE':
            st.caption('本次無名單差異；不代表已完成指數最新成分檢核。')
