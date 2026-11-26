"""Entry-time Taiwan baselines from already verified, timestamped archives."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import json,math,time
from urllib.request import urlopen
from functools import lru_cache
SOURCE='https://raw.githubusercontent.com/monstervincentw-glitch/growth-observatory-bank/research-data/financial-versions/TW.json'
OBSERVED='VERIFIED_OBSERVATION'
ACCEPTED=('OFFICIAL_FILING',OBSERVED)

def instant(value):
    value=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if value.tzinfo is None:raise ValueError('財報觀測時間缺少時區')
    return value

def buy_cutoff(day,recorded_at=None):
    # A date-only historical entry must not inherit a later recording time.
    if recorded_at:
        at=instant(recorded_at)
        if at.astimezone(ZoneInfo('Asia/Taipei')).date().isoformat()==day:return at
    return datetime.fromisoformat(day+'T09:00:00+08:00')

@lru_cache(maxsize=1)
def _load_registry(bucket):
    with urlopen(SOURCE,timeout=30) as response:raw=response.read(8_000_001)
    if len(raw)>8_000_000:raise ValueError('台股財報版本來源超過限制')
    result=json.loads(raw)
    if not isinstance(result,dict) or not result:raise ValueError('台股財報版本來源不完整')
    return result

def load_registry():
    return _load_registry(int(time.time()//300))

def resolve(symbol,day,registry,recorded_at=None):
    cutoff=buy_cutoff(day,recorded_at);eligible=[]
    for version in (registry.get(symbol,{}) or {}).get('versions',{}).values():
        f=version.get('financial') or {}
        if (version.get('source')!='FinMind' or f.get('financial_status')!='ok'
                or version.get('period')!=f.get('latest_period') or str(version.get('period',''))>day):continue
        if not all(type(f.get(k)) in (int,float) and math.isfinite(f[k]) for k in ('revenue_growth','operating_margin')):continue
        try:
            seen=instant(version['firstVerifiedAt'])
            known=instant(version['eligibleFrom'])
        except (KeyError,ValueError,TypeError):continue
        if seen>cutoff or known>cutoff:continue
        eligible.append(version)
    if not eligible:
        from entry_basis import _unverified
        return _unverified(symbol,day,'','沒有早於該筆建倉時間的已驗證台股財報版本')
    v=max(eligible,key=lambda x:(x['period'],instant(x['firstVerifiedAt'])))
    official=v.get('officialPublishedAt');proof=v.get('officialSource','')
    is_official=bool(official and proof.startswith('https://') and instant(official)<=cutoff)
    return dict(ok=True,symbol=symbol,trade_date=day,revenue_growth=v['financial']['revenue_growth'],
        operating_margin=v['financial']['operating_margin'],filing_period=v['period'],
        official_published_at=official if is_official else '',
        source=proof if is_official else SOURCE+'#'+symbol,
        version_id=('TW_OFFICIAL_' if is_official else 'TW_OBS_')+v['id'],
        verification='OFFICIAL_FILING' if is_official else OBSERVED,
        detail='建倉前已驗證封存觀測 '+v['firstVerifiedAt']+'；基準可用時間 '+v['eligibleFrom']+'；版本固定，不隨每日更新改寫')
