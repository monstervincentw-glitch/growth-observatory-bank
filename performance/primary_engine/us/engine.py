"""Monthly research signals; next-session open fills; daily NAV; explicit costs."""
from __future__ import annotations
import json, hashlib, concurrent.futures, inspect
from pathlib import Path
import numpy as np
import pandas as pd
from download_data import ROOT, RAW, load_chart
from fundamentals import CompanyFacts

LABELS = {
 "universe_equal": "研究樣本等權",
 "fundamental": "基本面",
 "fundamental_value": "基本面＋估值",
 "divergence": "基本面＋估值＋背離",
 "confirmation": "背離＋轉強確認",
 "macro_divergence": "背離＋總經調整",
 "divergence_matched_control": "基本估值（匹配背離曝險）",
 "QQQ": "QQQ 買入持有",
 "SOXX": "SOXX 買入持有",
}

def price_features(frame, benchmark):
    p = frame.adj_close
    ratio = frame.adj_close/frame.close
    high = frame.high*ratio
    low = frame.low*ratio
    rsv = 100*(p-low.rolling(9).min())/(high.rolling(9).max()-low.rolling(9).min())
    k = rsv.ewm(alpha=1/3, adjust=False).mean()
    d = k.ewm(alpha=1/3, adjust=False).mean()
    out = pd.DataFrame(index=frame.index)
    out["close"] = frame.close
    out["adj_close"] = p
    out["history_sessions"] = p.notna().cumsum()
    out["dollar_volume"] = (frame.close*frame.volume).rolling(20).mean()
    out["drawdown_126"] = p/p.rolling(126).max()-1
    out["return_63"] = p/p.shift(63)-1
    out["relative_63"] = out.return_63-(benchmark/benchmark.shift(63)-1).reindex(frame.index)
    out["volatility_63"] = p.pct_change(fill_method=None).rolling(63).std()*np.sqrt(63)
    out["relative_z"] = out.relative_63/out.volatility_63.clip(lower=.05)
    out["k"] = k
    out["d"] = d
    out["ma20"] = p.rolling(20).mean()
    out["ma50"] = p.rolling(50).mean()
    out["ma200"] = p.rolling(200).mean()
    out["confirm"] = (k>d) & (p>out.ma20) & (p>p.shift(5))
    return out

class Macro:
    def __init__(self, raw=RAW):
        self.columns = {}
        for path in sorted(Path(raw).glob("alfred_*.csv")):
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            for col in df:
                self.columns[col] = pd.to_numeric(df[col], errors="coerce")

    def series(self, name, asof):
        key = f"{name}_{pd.Timestamp(asof):%Y%m%d}"
        if key not in self.columns:
            return pd.Series(dtype=float)
        # One additional calendar-day observation lag avoids same-day release-time ambiguity.
        return self.columns[key].loc[:pd.Timestamp(asof)-pd.Timedelta(days=1)].dropna()

    def snapshot(self, asof):
        data = {name: self.series(name, asof) for name in ["CPIAUCSL", "FEDFUNDS", "DFII10", "NFCI"]}
        if any(s.empty for s in data.values()):
            return {"macro_status": "missing_vintage", "macro_exposure": 0.0, "macro_stress": np.nan}
        def change(s, days):
            prior = s.loc[:s.index[-1]-pd.Timedelta(days=days)]
            return s.iloc[-1]-prior.iloc[-1] if len(prior) else np.nan
        cpi = data["CPIAUCSL"]
        yoy = cpi.iloc[-1]/cpi.iloc[-13]-1 if len(cpi)>=13 else np.nan
        cpi3 = (cpi.iloc[-1]/cpi.iloc[-4])**4-1 if len(cpi)>=4 else np.nan
        real_change = change(data["DFII10"], 90)
        financial_conditions = data["NFCI"].iloc[-1]
        conditions_change = change(data["NFCI"], 90)
        funds_change = change(data["FEDFUNDS"], 90)
        flags = [real_change > .5, financial_conditions > 0 or conditions_change > .25,
                 yoy > .04 and cpi3 > yoy, funds_change > .25]
        stress = sum(bool(v) for v in flags)
        return {"macro_status": "vintage_verified", "macro_exposure": .5 if stress>=2 else 1.0,
                "macro_stress": stress, "cpi_yoy": yoy, "cpi_3m_annualized": cpi3,
                "real_yield": data["DFII10"].iloc[-1], "real_yield_90d_change": real_change,
                "financial_conditions": financial_conditions, "financial_conditions_90d_change": conditions_change,
                "fed_funds": data["FEDFUNDS"].iloc[-1]}

def rank(series):
    return series.rank(pct=True, method="average")

def share_split_multiplier(chart_payload, shares_date, shares_filed, asof):
    """Bridge stale pre-split SEC share counts to the signal-date raw stock price.

    Filings made after a split may already restate shares. Use the later of the
    share observation and its filing date to avoid double-adjusting those facts.
    """
    if pd.isna(shares_date) or pd.isna(shares_filed):
        return np.nan
    since=max(pd.Timestamp(shares_date),pd.Timestamp(shares_filed))
    factor=1.
    for event in chart_payload.get("events",{}).get("splits",{}).values():
        d=pd.Timestamp(event["date"],unit="s",tz="UTC").tz_convert("America/New_York").tz_localize(None).normalize()
        if since<d<=pd.Timestamp(asof):
            factor*=float(event["numerator"])/float(event["denominator"])
    return factor

def score_snapshot(frame, cfg):
    f = frame.copy()
    needed = ["revenue_growth", "growth_acceleration", "operating_margin", "cfo_margin", "shares", "revenue_ttm"]
    for c in needed:
        if c not in f:
            f[c] = np.nan
    f["shares_for_marketcap"] = f["shares"]*f.get("shares_split_multiplier",1.)
    f["market_cap_estimate"] = f["shares_for_marketcap"]*f["close"]
    f["price_sales"] = f.market_cap_estimate/f.revenue_ttm
    shares_age = (pd.to_datetime(f["asof"])-pd.to_datetime(f.get("shares_date", pd.Series(index=f.index,dtype=str)))).dt.days
    f["eligible"] = ((f.get("financial_status", "") == "ok") &
        (f.revenue_growth >= cfg["minimum_revenue_growth"]) &
        ((f.operating_margin > 0) | (f.cfo_margin > 0)) &
        (f["financial_age_days"].fillna(10000) <= cfg["maximum_financial_age_days"]) &
        (f.history_sessions >= cfg["minimum_history_sessions"]) &
        (f.dollar_volume >= cfg["minimum_average_dollar_volume"]) &
        (f.market_cap_estimate >= 1e9) & shares_age.le(180) &
        f[needed].notna().all(axis=1) & f.price_sales.between(.05, 200))
    e = f.loc[f.eligible]
    # Peer-relative valuation; coarse industries require further production validation.
    value_rank = e.groupby("industry").price_sales.transform(lambda s: rank(-s))
    # A single company in a peer group gets a neutral valuation, not a perfect score.
    counts = e.groupby("industry").price_sales.transform("count")
    value_rank = value_rank.where(counts>=3, .5)
    fs = .45*rank(e.revenue_growth)+.20*rank(e.growth_acceleration)+.20*rank(e.operating_margin)+.15*rank(e.cfo_margin)
    f["fundamental_score"] = fs*100
    f["value_score"] = value_rank*100
    f["fundamental_value_score"] = 100*(.75*fs+.25*value_rank)
    dislocation = .7*rank(-e.relative_z)+.3*rank(-e.drawdown_126)
    f["divergence_score"] = .8*f.fundamental_value_score+.2*dislocation*100
    f["is_divergence"] = f.eligible & f.drawdown_126.between(-.50,-.10) & (f.relative_63<0)
    f["state"] = "未達研究門檻"
    f.loc[f.eligible,"state"] = "成長觀察"
    f.loc[f.is_divergence,"state"] = "背離候選"
    f.loc[f.is_divergence & f.confirm.fillna(False),"state"] = "轉強確認"
    # Risk warnings are descriptive, not a backtested discretionary override.
    f["risk_note"] = np.where(f.revenue_growth<0,"營收TTM衰退",np.where(f.financial_age_days>180,"財報過期",np.where((f.operating_margin<0)&(f.cfo_margin<0),"營業虧損且營業現金流為負", "")))
    risk=(f.financial_status=="ok") & (f.risk_note!="")
    f.loc[risk,"state"]="風險預警"
    return f

def company_rows(arguments):
    s,industry,dates,period_end,cfg=arguments
    path=RAW/f"{s}_facts.json"
    chart=RAW/f"{s}_chart.json"
    if not chart.exists() or not path.exists():
        return [],{"symbol":s,"status":"missing_prices_or_facts"}
    price=load_chart(chart)
    if price.adj_close.dropna().index[-1]<period_end:
        return [],{"symbol":s,"status":"truncated_price_history_excluded","last_price":str(price.index[-1].date())}
    bench="SOXX" if industry in ["semiconductor","equipment"] else ("IGV" if industry in ["software","security"] else "QQQ")
    h=hashlib.sha256(("company-cache-v1"+industry+str(dates)+str(cfg["fundamental_lag_days"])+inspect.getsource(price_features)+inspect.getsource(share_split_multiplier)).encode())
    for p in [path,chart,RAW/f"{bench}_chart.json",ROOT/"fundamentals.py"]:h.update(p.read_bytes())
    key=h.hexdigest()
    folder=ROOT/"data/processed/company_snapshots";folder.mkdir(parents=True,exist_ok=True)
    cache=folder/f"{s}.csv";keypath=folder/f"{s}.sha256"
    if cache.exists() and keypath.exists() and keypath.read_text()==key:
        frame=pd.read_csv(cache,parse_dates=["signal_date"])
        return frame.to_dict("records"),{"symbol":s,"status":"included","financial_snapshots_ok":int((frame.financial_status=="ok").sum())}
    features=price_features(price,load_chart(RAW/f"{bench}_chart.json").adj_close)
    facts=CompanyFacts(path)
    chart_payload=json.loads(chart.read_text())["chart"]["result"][0]
    rows=[]
    for date in dates:
        if date not in features.index or pd.isna(features.loc[date,"adj_close"]):continue
        snap=facts.snapshot(date,cfg["fundamental_lag_days"])
        snap["shares_split_multiplier"]=share_split_multiplier(chart_payload,snap.get("shares_date"),snap.get("shares_filed"),date)
        snap.update(features.loc[date].to_dict())
        snap.update(symbol=s,industry=industry,signal_date=date,benchmark=bench)
        rows.append(snap)
    frame=pd.DataFrame(rows);frame.to_csv(cache,index=False);keypath.write_text(key)
    return rows,{"symbol":s,"status":"included","financial_snapshots_ok":int((frame.financial_status=="ok").sum())}

def build_snapshots(cfg, refresh=False):
    outpath = ROOT/"data/processed/snapshots.csv"
    hashobj=hashlib.sha256(json.dumps(cfg,sort_keys=True).encode())
    for p in sorted(list(RAW.glob("*_facts.json"))+list(RAW.glob("*_chart.json"))+list(RAW.glob("alfred_*.csv"))+[ROOT/"engine.py",ROOT/"fundamentals.py",ROOT/"universe.csv"]):
        hashobj.update(p.name.encode());hashobj.update(p.read_bytes())
    cache_key=hashobj.hexdigest()
    keypath=outpath.with_suffix(".sha256")
    if outpath.exists() and keypath.exists() and keypath.read_text()==cache_key and not refresh:
        return pd.read_csv(outpath, parse_dates=["signal_date"])
    universe = pd.read_csv(ROOT/"universe.csv")
    prices = {}
    for s in ["QQQ","SOXX","IGV"]+universe.symbol.tolist():
        p = RAW/f"{s}_chart.json"
        if p.exists():
            try:
                prices[s] = load_chart(p)
            except (KeyError,TypeError,ValueError):
                pass
    calendar = prices["QQQ"].index
    before = calendar[calendar<pd.Timestamp(cfg["start"])]
    period = calendar[(calendar>=pd.Timestamp(cfg["start"])) & (calendar<=pd.Timestamp(cfg["end"]))]
    dates = [before[-1]] + list(pd.Series(period,index=period).groupby(period.to_period("M")).last())
    all_rows = []
    audit = []
    macro = Macro()
    jobs=[(r.symbol,r.industry,dates,period[-1],cfg) for r in universe.itertuples()]
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as pool:
        for rows,status in pool.map(company_rows,jobs):
            all_rows.extend(rows);audit.append(status)
            print(f"financial snapshots {status['symbol']}: {status.get('financial_snapshots_ok',0)}/{len(dates)} {status['status']}",flush=True)
    panel = pd.DataFrame(all_rows)
    panel.to_csv(ROOT/"data/processed/financial_snapshots_unscored.csv",index=False)
    chunks = []
    macro_rows = []
    for date, group in panel.groupby("signal_date",sort=True):
        ms = macro.snapshot(date)
        macro_rows.append({"signal_date":date,**ms})
        scored = score_snapshot(group,cfg)
        for key,value in ms.items():
            scored[key] = value
        chunks.append(scored)
    panel = pd.concat(chunks,ignore_index=True)
    outpath.parent.mkdir(parents=True,exist_ok=True)
    panel.to_csv(outpath,index=False)
    keypath.write_text(cache_key)
    pd.DataFrame(audit).to_csv(ROOT/"results/data_coverage.csv",index=False)
    pd.DataFrame(macro_rows).to_csv(ROOT/"results/macro_snapshots.csv",index=False)
    return panel

def targets(group,cfg):
    e = group[group.eligible].copy()
    n = cfg["top_n"]
    def top(frame,col):
        return frame.sort_values([col,"symbol"],ascending=[False,True]).head(n)
    def weights(frame, exposure=1, denominator=None):
        if frame.empty:
            return {}
        return dict(zip(frame.symbol,[exposure/(denominator or n)]*len(frame)))
    all_valid = group[(group.history_sessions>=cfg["minimum_history_sessions"]) & (group.dollar_volume>=cfg["minimum_average_dollar_volume"])]
    div = top(e[e.is_divergence],"divergence_score")
    confirm = top(e[e.is_divergence & e.confirm.fillna(False)],"divergence_score")
    count = len(div)
    out = {
      "universe_equal": weights(all_valid, denominator=len(all_valid)),
      "fundamental": weights(top(e,"fundamental_score")),
      "fundamental_value": weights(top(e,"fundamental_value_score")),
      "divergence": weights(div),
      "confirmation": weights(confirm),
      "macro_divergence": weights(div,float(group.macro_exposure.iloc[0])),
      "divergence_matched_control": weights(top(e,"fundamental_value_score").head(count)) if count else {},
    }
    return out

def trade_to_weights(units, cash, opens, weights, cost_rate):
    held = units[units.abs()>1e-14]
    if not opens.reindex(held.index).notna().all():
        raise ValueError("Held security has missing executable price; no silent forward fill")
    if any(pd.isna(opens.get(s)) or opens[s]<=0 for s in weights):
        raise ValueError("Target security lacks next-session execution price")
    old = (units*opens).fillna(0)
    nav = float(old.sum()+cash)
    w = pd.Series(weights,dtype=float).reindex(units.index,fill_value=0.)
    if w.sum()>1+1e-9 or (w<0).any():
        raise ValueError("Invalid long-only target weights")
    cost=0.
    for _ in range(20):
        new = w*(nav-cost)
        cost = float((new-old).abs().sum()*cost_rate)
    new = w*(nav-cost)
    new_units = (new/opens).replace([np.inf,-np.inf],np.nan).fillna(0)
    new_cash = float(nav-cost-new.sum())
    return new_units,new_cash,cost,float((new-old).abs().sum()/nav)

def backtest(panel,cfg,cost_bps=None):
    universe = pd.read_csv(ROOT/"universe.csv").symbol.tolist()
    symbols = sorted(set(panel.symbol))
    charts = {s:load_chart(RAW/f"{s}_chart.json") for s in symbols+["QQQ","SOXX"]}
    calendar = charts["QQQ"].index
    calendar = calendar[(calendar>=pd.Timestamp(cfg["start"])) & (calendar<=pd.Timestamp(cfg["end"]))]
    opens = pd.DataFrame({s:charts[s].adj_open.reindex(calendar) for s in symbols},index=calendar)
    closes = pd.DataFrame({s:charts[s].adj_close.reindex(calendar) for s in symbols},index=calendar)
    # Signal at month-end close; execute strictly on the following market session.
    schedule = {}
    groups = {pd.Timestamp(d):g for d,g in panel.groupby("signal_date")}
    for d,g in groups.items():
        following = calendar[calendar>d]
        if len(following):
            schedule[following[0]] = (d,targets(g,cfg))
    strategies = cfg["strategies"]+["divergence_matched_control"]
    cost_rate = (cfg["one_way_cost_bps"] if cost_bps is None else cost_bps)/10000
    curves={};trades=[];holdings=[]
    for strategy in strategies:
        units=pd.Series(0.,index=symbols);cash=1.;rows=[]
        for date in calendar:
            cost=0.;turnover=0.
            if date in schedule:
                signal, plans = schedule[date]
                desired=plans[strategy]
                units,cash,cost,turnover=trade_to_weights(units,cash,opens.loc[date],desired,cost_rate)
                for symbol,weight in desired.items():
                    holdings.append({"strategy":strategy,"signal_date":signal,"execution_date":date,"symbol":symbol,"target_weight":weight})
                trades.append({"strategy":strategy,"signal_date":signal,"execution_date":date,"positions":len(desired),"target_exposure":sum(desired.values()),"turnover":turnover,"cost_nav_units":cost})
            held=units[units.abs()>1e-14]
            if not closes.loc[date].reindex(held.index).notna().all():
                raise ValueError(f"Missing closing price for held stock on {date}")
            nav=float((units*closes.loc[date]).fillna(0).sum()+cash)
            rows.append({"date":date,"nav":nav,"exposure":(nav-cash)/nav,"positions":len(held),"cost":cost,"turnover":turnover})
        curves[strategy]=pd.DataFrame(rows).set_index("date")
    navs=pd.DataFrame({s:d.nav for s,d in curves.items()})
    # Apply the same initial one-way cost to benchmark; no fictitious monthly benchmark turnover.
    for s in ["QQQ","SOXX"]:
        navs[s]=(1-cost_rate)*charts[s].adj_close.reindex(calendar)/charts[s].adj_open.loc[calendar[0]]
    return navs,pd.DataFrame(trades),pd.DataFrame(holdings),curves

def performance(navs, initial_value=1.):
    rows=[]
    for s in navs:
        p=navs[s].dropna();returns=p.pct_change(fill_method=None)
        if len(p)<2: continue
        returns.iloc[0]=p.iloc[0]/initial_value-1
        years=len(p)/252
        total=p.iloc[-1]/initial_value-1
        cagr=(p.iloc[-1]/initial_value)**(1/years)-1
        peak=p.cummax().clip(lower=initial_value)
        dd=p/peak-1
        vol=returns.std()*np.sqrt(252)
        sharpe=returns.mean()/returns.std()*np.sqrt(252) if returns.std()>0 else np.nan
        rows.append({"strategy":s,"total_return":total,"cagr":cagr,"max_drawdown":dd.min(),
                     "volatility":vol,"sharpe_zero_rf":sharpe,"calmar":cagr/abs(dd.min()) if dd.min()<0 else np.nan})
    return pd.DataFrame(rows)

def forward_events(panel,cfg):
    """Non-overlapping monthly signals; overlapping holding windows -> descriptive only."""
    rows=[]
    qqq=load_chart(RAW/"QQQ_chart.json")
    cache={}
    end=pd.Timestamp(cfg["end"])
    for date,g in panel.groupby("signal_date"):
        plans=targets(g,cfg)
        for strategy in ["fundamental_value","divergence"]:
            for s,w in plans[strategy].items():
                if s not in cache:
                    cache[s]=load_chart(RAW/f"{s}_chart.json")
                p=cache[s]
                cal=qqq.index[(qqq.index>date) & (qqq.index<=end)]
                if len(cal)<1: continue
                entry=cal[0]
                a=float(p.loc[entry,"adj_open"])
                for horizon in [20,60,120]:
                    if len(cal)<horizon: continue
                    exitdate=cal[horizon-1]
                    ret=float(p.loc[exitdate,"adj_close"]/a-1)
                    qr=float(qqq.loc[exitdate,"adj_close"]/qqq.loc[entry,"adj_open"]-1)
                    benchmark=g.loc[g.symbol==s,"benchmark"].iloc[0]
                    if benchmark not in cache:
                        cache[benchmark]=load_chart(RAW/f"{benchmark}_chart.json")
                    b=cache[benchmark]
                    br=float(b.loc[exitdate,"adj_close"]/b.loc[entry,"adj_open"]-1)
                    prices=p.adj_close.reindex(cal[:horizon])
                    rows.append({"strategy":strategy,"signal_date":date,"entry_date":entry,"symbol":s,
                                 "horizon":horizon,"return_net_approx":(1+ret)*(1-cfg['one_way_cost_bps']/10000)**2-1,
                                 "excess_qqq":ret-qr,"excess_sector":ret-br,
                                 "explosive":ret-br>=.10,"worst_before_exit":float(prices.min()/a-1)})
    return pd.DataFrame(rows)
