"""Read complete public market observations without changing research decisions."""
import json
import math
from datetime import datetime, timezone
from urllib.request import urlopen

ENDPOINT = 'https://script.google.com/macros/s/AKfycbz5kl8tTTtNlcYqjPa0fM9sKfo1RTwE_CEFcetfMeaRYXmMvi93s6mrz7BxnbYniAWxSw/exec'
COUNTS = {'US': 89, 'TW': 80}
UNIVERSE = {'US': ('AAOI', 'AAPL', 'ACLS', 'ADBE', 'ADI', 'ADSK', 'AEIS', 'AKAM', 'ALGM', 'AMAT', 'AMD', 'AMKR', 'AMZN', 'APP', 'AVGO', 'BKNG', 'CDNS', 'COHU', 'CRUS', 'CRWD', 'CSCO', 'DBX', 'DDOG', 'DIOD', 'DOCU', 'DUOL', 'EBAY', 'ENPH', 'ETSY', 'FIVN', 'FORM', 'FSLR', 'FTNT', 'GEN', 'GOOGL', 'GTM', 'ICHR', 'INTC', 'INTU', 'KLAC', 'LCID', 'LITE', 'LRCX', 'LSCC', 'MCHP', 'MDB', 'META', 'MGNI', 'MKSI', 'MPWR', 'MRVL', 'MSFT', 'MU', 'NTAP', 'NTNX', 'NVDA', 'NXPI', 'OKTA', 'ON', 'PANW', 'POWI', 'PTON', 'PUBM', 'QCOM', 'RIVN', 'RMBS', 'ROKU', 'RPD', 'SEDG', 'SITM', 'SLAB', 'SMCI', 'SNPS', 'STX', 'SWKS', 'SYNA', 'TEAM', 'TENB', 'TER', 'TSLA', 'TTD', 'TTWO', 'TXN', 'VECO', 'VIAV', 'WDAY', 'WDC', 'ZM', 'ZS'), 'TW': ('2330', '2303', '2454', '2379', '3034', '2458', '3035', '3443', '5269', '6415', '3661', '3529', '5274', '2344', '2408', '2337', '8299', '3260', '4967', '3711', '2449', '6239', '3374', '6223', '6510', '6488', '3532', '2317', '2382', '2357', '2376', '3231', '2356', '4938', '2324', '2353', '2377', '3706', '6669', '2308', '2301', '3044', '3037', '8046', '2313', '3189', '2383', '4958', '6274', '2368', '2492', '2327', '3008', '3019', '3406', '2409', '3481', '2474', '2498', '2371', '2347', '3702', '5434', '3017', '3324', '2421', '3013', '6121', '3533', '3105', '3081', '3363', '3023', '2385', '6117', '6116', '2402', '2481', '5425', '8086')}
INACTIVE = {"US": {"ROKU"}, "TW": set()}

def validate_quotes(payload, market):
    b = payload['markets'][market] if 'markets' in payload else payload
    if b.get('schema') != 1 or b.get('market') != market or b.get('expected') != COUNTS[market]:
        raise ValueError('Google行情批次格式不符')
    if b.get('status') != 'REFERENCE_QUOTES_COMPLETE' or b.get('missing'):
        raise ValueError('Google行情批次未完整')
    day = b['day']
    datetime.strptime(day, '%Y-%m-%d')
    if day > datetime.now(timezone.utc).strftime('%Y-%m-%d'):
        raise ValueError('Google行情日期異常')
    quotes = b['quotes']
    inactive = b.get('inactive', [])
    if set(inactive) != INACTIVE[market] or len(inactive) != len(set(inactive)) or set(quotes) | set(inactive) != set(UNIVERSE[market]):
        raise ValueError('Google行情股票池不符')
    if len(quotes) != b['succeeded'] or len(quotes) + len(inactive) != COUNTS[market] or set(quotes) & set(inactive):
        raise ValueError('Google行情數量不符')
    for symbol,q in quotes.items():
        if q.get('priceDate') != day or type(q.get('close')) not in (int,float) or not math.isfinite(q['close']) or q['close'] <= 0:
            raise ValueError('Google行情價格或日期異常')
        if q.get('currency') != ('USD' if market == 'US' else 'TWD'):
            raise ValueError('Google行情幣別異常')
    return b

def fetch_google_quotes(market):
    with urlopen(ENDPOINT, timeout=18) as response:
        raw = response.read(250_001)
    if len(raw) > 250_000:
        raise ValueError('Google行情批次超過大小限制')
    return validate_quotes(json.loads(raw), market)