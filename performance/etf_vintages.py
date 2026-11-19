"""Keep sealed observations immutable; reconcile later provider bar revisions explicitly."""
from hashlib import sha256
import json

import numpy as np
import pandas as pd


def sealed_vintage(inputs, prior, anchor, manifest):
    path = inputs/'etf-inputs'/f'{anchor}.json'
    ledger_path = prior.parent/'etf-ledger'/f'{anchor}.json'
    raw = path.read_bytes()
    feed = json.loads(raw)
    ledger = json.loads(ledger_path.read_text())
    # Link the archived source to the actual previously published calculation.
    if (feed.get('status') != 'VERIFIED' or feed.get('cutoff') != anchor or
            ledger.get('batch') != manifest['batch'] or
            ledger.get('source_batch') != feed.get('batch') or ledger.get('price_date') != anchor):
        raise ValueError('ETF sealed source/ledger identity mismatch')
    digest = sha256(raw).hexdigest()
    receipts = [json.loads(p.read_text()).get('results',{}).get('ETF',{})
                for p in (prior.parent/'runs').glob('*.json')]
    if not any(r.get('status') == 'VERIFIED' and r.get('through') == anchor and
               r.get('source_batch') == feed['batch'] and r.get('input_sha256') == digest
               for r in receipts):
        raise ValueError('ETF sealed source has no matching published SHA-256 receipt')
    return feed, ledger, dict(path=str(path.relative_to(inputs)), sha256=digest,
        batch=feed['batch'], observedAt=feed['observedAt'])


def reconcile_anchor(symbol, frame, adjusted, mark, anchor, overlap, sealed, ledger):
    """Only an unused historical opening quote can differ; closes stay strictly checked.

    No input is edited. Select the sealed source for the already calculated row,
    and the new source exclusively for future rows. Corporate-action rebasing
    must still pass the original independent close-overlap checks in live_etf.
    """
    old = pd.DataFrame(sealed['prices'][symbol]).set_index('date')
    old.index = pd.DatetimeIndex(old.index)
    base = old.loc[anchor]
    scale = mark['tr_close']/float(base.adjClose)
    np.testing.assert_allclose(base.adjOpen/base.adjClose, base.open/base.close, rtol=1e-9)
    np.testing.assert_allclose(float(base.adjOpen)*scale, mark['tr_open'], rtol=0, atol=1e-8)
    reference = pd.Series(overlap,dtype=float)
    reference.index = pd.DatetimeIndex(reference.index)
    np.testing.assert_allclose(old.loc[reference.index,'adjClose']*scale, reference, rtol=1e-6, atol=1e-8)
    new = frame.loc[anchor]
    # Distribution/split changes affect close adjustment factors too. They must
    # never be classified as an isolated provider opening-price revision.
    np.testing.assert_allclose(new['close'], base['close'], rtol=0, atol=1e-8)
    np.testing.assert_allclose(new.adjClose/new['close'], base.adjClose/base['close'], rtol=0, atol=1e-12)
    used = [t for t in ledger['trades'] if t['date'] == str(anchor.date()) and t['symbol'] == symbol]
    if used:
        raise ValueError(f'{symbol}: revised anchor opening price was used by sealed trades; review required')
    audit = dict(symbol=symbol,date=str(anchor.date()),classification='PROVIDER_UNUSED_HISTORICAL_OPEN_REVISION',
        sealedOpen=float(base.open),laterOpen=float(new.open),sealedClose=float(base['close']),
        laterClose=float(new['close']),sealedAdjustmentFactor=float(base.adjClose/base['close']),
        laterAdjustmentFactor=float(new.adjClose/new['close']),anchorTrades=0,
        policy='Sealed source through anchor; new source strictly after anchor; no historical recalculation')
    selected = pd.DataFrame([{'tr_open':float(base.adjOpen)*scale,
                              'tr_close':float(base.adjClose)*scale}],index=[anchor])
    return pd.concat([selected,adjusted.loc[adjusted.index>anchor]]), audit
