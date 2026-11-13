"""Daily condition-driven positions. Entry and exit are separate; no rank-based sales."""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd
from download_data import ROOT, RAW, load_chart
from engine import Macro, price_features, rank, performance
from prepare_event_data import signal_calendar, weekly_dates

LABELS={"event_divergence":"條件進出：基本面＋資金＋背離", "event_no_divergence":"對照：基本面＋資金（不要求背離）",
        "event_hold_after_entry":"對照：同條件買入後不主動賣出", "v1_monthly_divergence":"V0.1 每月排名換股", "QQQ":"QQQ 買入持有", "SOXX":"SOXX 買入持有"}
LABELS["QQQ_funding_gate"]="QQQ 同資金條件進出"
STRATEGIES=["event_divergence","event_no_divergence","event_hold_after_entry"]
FIN_COLUMNS=["financial_status","company","cik","revenue_growth","quarterly_growth","growth_acceleration","operating_margin","cfo_margin","revenue_ttm","shares","shares_date","shares_filed","latest_period","max_input_filed"]

def add_release_history(events):
    """Carry whole filing snapshots. Missing values in a new release stay missing."""
    events=events.copy().sort_values("effective_date")
    previous=None;current=None;prior_growth=np.nan;prior_margin=np.nan;keys=[];pg=[];pm=[]
    for row in events.to_dict("records"):
        values=[row.get(k) for k in ["latest_period","revenue_growth","operating_margin","cfo_margin"]]
        key="|".join(str(v) for v in values)
        if row.get("financial_status")=="ok" and key!=current:
            if previous is not None:
                prior_growth=previous.get("revenue_growth",np.nan);prior_margin=previous.get("operating_margin",np.nan)
            previous=row;current=key
        keys.append(current or "unknown");pg.append(prior_growth);pm.append(prior_margin)
    events["fundamental_release_key"]=keys
    events["prior_release_growth"]=pg;events["prior_release_margin"]=pm
    return events

def release_weakening(frame,cfg):
    return ((frame.revenue_growth<=frame.prior_release_growth-cfg["exit_revenue_growth_drop"]+1e-12) |
            (frame.operating_margin<=frame.prior_release_margin-cfg["exit_operating_margin_drop"]+1e-12))

def fundamental_good(frame,cfg):
    return ((frame.financial_status=="ok") & frame.revenue_growth.ge(cfg["entry_revenue_growth"]) &
            frame.quarterly_growth.ge(cfg["entry_quarterly_growth"]) & frame.operating_margin.gt(0) & frame.cfo_margin.gt(0) &
            frame.financial_age_days.le(cfg["maximum_financial_age_days"]) & ~release_weakening(frame,cfg))

def build_panel(cfg,rebuild=False):
    path=ROOT/"data/processed/event_signals.csv"
    digest=hashlib.sha256((ROOT/"event_engine.py").read_bytes()+json.dumps(cfg,sort_keys=True).encode())
    sources=list((ROOT/"data/processed/financial_events").glob("*.csv"))+list((ROOT/"data/macro_weekly").glob("*.csv"))+list(RAW.glob("*_chart.json"))
    for p in sorted(sources):digest.update(p.name.encode());digest.update(p.read_bytes())
    key=digest.hexdigest();keypath=path.with_suffix(".sha256")
    if path.exists() and keypath.exists() and keypath.read_text()==key and not rebuild:
        return pd.read_csv(path,parse_dates=["signal_date","financial_event_date","macro_vintage_date"])
    cal=signal_calendar(cfg)
    universe=pd.read_csv(ROOT/"universe.csv").set_index("symbol")
    coverage=pd.read_csv(ROOT/"data/previous_results/data_coverage.csv")
    symbols=coverage.loc[coverage.status=="included","symbol"].tolist()
    charts={s:load_chart(RAW/f"{s}_chart.json") for s in ["QQQ","SOXX","IGV"]+symbols}
    weekly=Macro(ROOT/"data/macro_weekly")
    macro_rows=[]
    for date in weekly_dates(cfg):
        row=weekly.snapshot(date)
        if row["macro_status"]!="vintage_verified":raise ValueError(f"Missing macro vintage {date}")
        macro_rows.append({"macro_vintage_date":date,**row})
    mac=pd.DataFrame(macro_rows).set_index("macro_vintage_date")
    mac["macro_vintage_date"]=mac.index
    mac=mac.reindex(cal,method="ffill")
    pd.DataFrame(macro_rows).to_csv(ROOT/"results/weekly_macro.csv",index=False)
    rows=[]
    for symbol in symbols:
        events=pd.read_csv(ROOT/f"data/processed/financial_events/{symbol}.csv",parse_dates=["effective_date"])
        events=add_release_history(events)
        events["financial_event_date"]=events.effective_date
        for col in FIN_COLUMNS:
            if col not in events:events[col]=np.nan
        fin=events.set_index("effective_date").reindex(cal,method="ffill")
        fin=fin[FIN_COLUMNS+["financial_event_date","fundamental_release_key","prior_release_growth","prior_release_margin"]].copy()
        fin["financial_age_days"]=(pd.Series(cal,index=cal)-pd.to_datetime(fin.latest_period)).dt.days
        industry=universe.loc[symbol,"industry"]
        bench="SOXX" if industry in ["semiconductor","equipment"] else ("IGV" if industry in ["software","security"] else "QQQ")
        features=price_features(charts[symbol],charts[bench].adj_close).reindex(cal)
        data=fin.join(features)
        data["signal_date"]=cal;data["symbol"]=symbol;data["industry"]=industry;data["benchmark"]=bench
        data["asof"]=cal.strftime("%Y-%m-%d")
        since=pd.concat([pd.to_datetime(data.shares_date),pd.to_datetime(data.shares_filed)],axis=1).max(axis=1)
        factor=pd.Series(1.,index=cal)
        obj=json.loads((RAW/f"{symbol}_chart.json").read_text())["chart"]["result"][0]
        for split in obj.get("events",{}).get("splits",{}).values():
            date=pd.Timestamp(split["date"],unit="s",tz="UTC").tz_convert("America/New_York").normalize().tz_localize(None)
            mask=(cal>=date)&(since<date)
            factor.loc[mask]*=float(split["numerator"])/float(split["denominator"])
        data["market_cap_estimate"]=data.shares*factor*data.close
        data["price_sales"]=data.market_cap_estimate/data.revenue_ttm
        shares_age=(pd.Series(cal,index=cal)-pd.to_datetime(data.shares_date)).dt.days
        data["fundamental_good"]=fundamental_good(data,cfg)
        data["tradable"]=(data.history_sessions.ge(cfg["minimum_history_sessions"]) & data.dollar_volume.ge(cfg["minimum_average_dollar_volume"]) &
                          data.market_cap_estimate.ge(1e9) & shares_age.le(180) & data.price_sales.between(.05,200) & data.growth_acceleration.notna())
        data["technical_divergence"]=data.drawdown_126.between(-.50,-.10)&data.relative_63.lt(0)
        data=data.join(mac.drop(columns="macro_exposure"))
        data["funding_good"]=data.macro_stress.le(cfg["entry_max_macro_stress"])
        rows.append(data.reset_index(drop=True))
    panel=pd.concat(rows,ignore_index=True)
    panel["fundamental_value_score"]=np.nan
    for date,index in panel.groupby("signal_date").groups.items():
        g=panel.loc[index];e=g[g.fundamental_good&g.tradable]
        if e.empty:continue
        f=.45*rank(e.revenue_growth)+.20*rank(e.growth_acceleration)+.20*rank(e.operating_margin)+.15*rank(e.cfo_margin)
        v=e.groupby("industry").price_sales.transform(lambda s:rank(-s))
        n=e.groupby("industry").price_sales.transform("count");v=v.where(n>=3,.5)
        panel.loc[e.index,"fundamental_value_score"]=100*(.75*f+.25*v)
    panel=panel.sort_values(["signal_date","symbol"]).reset_index(drop=True)
    panel.to_csv(path,index=False);keypath.write_text(key)
    return panel

def financial_exit_reason(row,entry,cfg):
    reasons=[]
    if row.get("financial_status")!="ok":return "financial_data_unavailable"
    age=row.get("financial_age_days",np.nan)
    if not np.isfinite(age) or age>cfg["maximum_financial_age_days"]:reasons.append("financial_data_stale")
    for c in ["revenue_growth","quarterly_growth","operating_margin","cfo_margin"]:
        if not np.isfinite(row.get(c,np.nan)):return "financial_data_unavailable"
    if row["revenue_growth"]<cfg["entry_revenue_growth"]:reasons.append("revenue_growth_below_10pct")
    if row["quarterly_growth"]<0:reasons.append("quarterly_revenue_decline")
    if row["operating_margin"]<=0:reasons.append("operating_margin_nonpositive")
    if row["cfo_margin"]<=0:reasons.append("operating_cashflow_nonpositive")
    reference=row.get("prior_release_growth",np.nan)
    if np.isfinite(reference) and row["revenue_growth"]<=reference-cfg["exit_revenue_growth_drop"]+1e-12:reasons.append("revenue_growth_drop_since_release")
    reference=row.get("prior_release_margin",np.nan)
    if np.isfinite(reference) and row["operating_margin"]<=reference-cfg["exit_operating_margin_drop"]+1e-12:reasons.append("margin_drop_since_release")
    if row["revenue_growth"]<=entry["entry_revenue_growth"]-cfg["exit_revenue_growth_drop"]+1e-12:reasons.append("revenue_growth_drop_since_entry")
    if row["operating_margin"]<=entry["entry_operating_margin"]-cfg["exit_operating_margin_drop"]+1e-12:reasons.append("margin_drop_since_entry")
    return ";".join(reasons)

def exit_reason(row,entry,cfg,strategy):
    if strategy=="event_hold_after_entry":return ""
    reason=financial_exit_reason(row,entry,cfg)
    if row["macro_status"]!="vintage_verified":reason+=";macro_data_unavailable"
    elif row["macro_stress"]>=cfg["exit_macro_stress"]:reason+=";funding_conditions_weakened"
    return reason.strip(";")

def entry_candidates(group,positions,planned_exits,blocked,cfg,strategy):
    excluded=set(positions)|set(planned_exits)
    eligible=group.fundamental_good & group.funding_good & group.tradable
    if strategy!="event_no_divergence":eligible &= group.technical_divergence
    candidates=group.loc[eligible & ~group.symbol.isin(excluded)].copy()
    if blocked:
        allowed=[s not in blocked or key!=blocked[s] for s,key in zip(candidates.symbol,candidates.fundamental_release_key)]
        candidates=candidates.loc[allowed]
    slots=cfg["maximum_positions"]-(len(positions)-len(planned_exits))
    return candidates.sort_values(["fundamental_value_score","symbol"],ascending=[False,True]).head(max(0,slots)).to_dict("records")

def run_strategy(panel,charts,cfg,strategy,cost_bps=None):
    cal=charts["QQQ"].index
    cal=cal[(cal>=pd.Timestamp(cfg["start"]))&(cal<=pd.Timestamp(cfg["end"]))]
    rate=(cfg["one_way_cost_bps"] if cost_bps is None else cost_bps)/10000
    groups={pd.Timestamp(d):g for d,g in panel.groupby("signal_date",sort=True)}
    index={d:g.set_index("symbol").to_dict("index") for d,g in groups.items()}
    positions={};blocked={};cash=cfg["initial_capital"];ledger=[];actions=[];daily=[];audits=[]
    before=max(d for d in groups if d<cal[0]);prev=groups[before]
    pending={"signal_date":before,"exits":{},"entries":entry_candidates(prev,positions,{},blocked,cfg,strategy)}
    nextid=1
    for i,date in enumerate(cal):
        spent_cost=0.;turnover=0.
        if pending is not None:
            for symbol,reason in pending["exits"].items():
                pos=positions.pop(symbol)
                fill=float(charts[symbol].loc[date,"adj_open"])
                if not np.isfinite(fill) or fill<=0:raise ValueError("Missing exit fill "+symbol)
                gross=pos["units"]*fill;fee=gross*rate;net=gross-fee
                cash+=net;spent_cost+=fee;turnover+=gross
                signal_row=index[pending["signal_date"]][symbol]
                trade={k:v for k,v in pos.items() if k!="units"}
                trade.update(strategy=strategy,status="closed",exit_signal_date=pending["signal_date"],exit_date=date,
                    exit_tr_open=fill,exit_quote_open=float(charts[symbol].loc[date,"open"]),exit_proceeds_net=net,
                    exit_cost=fee,pnl_net=net-pos["entry_cash_out"],return_net=net/pos["entry_cash_out"]-1,
                    holding_sessions=i-pos["entry_index"],exit_reason=reason,exit_revenue_growth=signal_row.get("revenue_growth"),
                    exit_operating_margin=signal_row.get("operating_margin"),exit_macro_stress=signal_row.get("macro_stress"),
                    exit_financial_event=signal_row.get("financial_event_date"),exit_macro_vintage=signal_row.get("macro_vintage_date"),
                    exit_max_input_filed=signal_row.get("max_input_filed"))
                ledger.append(trade)
                actions.append({"strategy":strategy,"date":date,"signal_date":pending["signal_date"],"symbol":symbol,"action":"SELL","trade_id":pos["trade_id"],"notional":gross,"cost":fee,"reason":reason})
                if any(x.startswith(("revenue_","quarterly_","operating_","margin_","financial_")) for x in reason.split(";")):
                    blocked[symbol]=signal_row["fundamental_release_key"]
            nav_open=cash+sum(p["units"]*float(charts[s].loc[date,"adj_open"]) for s,p in positions.items())
            for row in pending["entries"]:
                symbol=row["symbol"]
                if symbol in positions:raise ValueError("Duplicate position")
                budget=min(cash,nav_open*cfg["entry_nav_fraction"])
                if budget+1e-9<nav_open*cfg["minimum_entry_nav_fraction"]:continue
                fill=float(charts[symbol].loc[date,"adj_open"])
                if not np.isfinite(fill) or fill<=0:raise ValueError("Missing entry fill "+symbol)
                gross=budget/(1+rate);fee=budget-gross;units=gross/fill
                cash-=budget;spent_cost+=fee;turnover+=gross
                positions[symbol]={"trade_id":f"{strategy}-{nextid:04}","symbol":symbol,"units":units,"entry_index":i,
                    "entry_signal_date":pending["signal_date"],"entry_date":date,"entry_tr_open":fill,
                    "entry_quote_open":float(charts[symbol].loc[date,"open"]),"entry_cash_out":budget,"entry_cost":fee,
                    "entry_revenue_growth":row["revenue_growth"],"entry_operating_margin":row["operating_margin"],
                    "entry_quarterly_growth":row["quarterly_growth"],"entry_cfo_margin":row["cfo_margin"],
                    "entry_macro_stress":row["macro_stress"],"entry_drawdown":row["drawdown_126"],"entry_relative_63":row["relative_63"],
                    "entry_financial_event":row["financial_event_date"],"entry_max_input_filed":row["max_input_filed"],
                    "entry_macro_vintage":row["macro_vintage_date"],"entry_release_key":row["fundamental_release_key"]}
                actions.append({"strategy":strategy,"date":date,"signal_date":pending["signal_date"],"symbol":symbol,"action":"BUY","trade_id":positions[symbol]["trade_id"],"notional":gross,"cost":fee,"reason":"fundamentals_and_funding_and_divergence" if strategy!="event_no_divergence" else "fundamentals_and_funding"})
                nextid+=1
        quotes={s:float(charts[s].loc[date,"adj_close"]) for s in positions}
        if any(not np.isfinite(p) or p<=0 for p in quotes.values()):raise ValueError("Missing held closing price")
        nav=cash+sum(positions[s]["units"]*quotes[s] for s in positions)
        if cash<-.00001:raise ValueError("Cash went negative")
        daily.append({"date":date,"nav_usd":nav,"nav":nav/cfg["initial_capital"],"cash_usd":cash,
                      "exposure":1-cash/nav,"positions":len(positions),"cost_usd":spent_cost,"turnover_usd":turnover})
        current=index[date]
        exits={}
        for symbol,pos in positions.items():
            reason=exit_reason(current[symbol],pos,cfg,strategy)
            audits.append({"strategy":strategy,"signal_date":date,"symbol":symbol,"trade_id":pos["trade_id"],"exit_required":bool(reason),"exit_reason":reason})
            if reason:exits[symbol]=reason
        entries=entry_candidates(groups[date],positions,exits,blocked,cfg,strategy)
        pending={"signal_date":date,"exits":exits,"entries":entries}
    openrows=[]
    for symbol,pos in positions.items():
        mark=pos["units"]*float(charts[symbol].loc[cal[-1],"adj_close"])
        row={k:v for k,v in pos.items() if k!="units"}
        row.update(strategy=strategy,status="open",valuation_date=cal[-1],market_value=mark,
                   unrealized_pnl=mark-pos["entry_cash_out"],unrealized_return=mark/pos["entry_cash_out"]-1,
                   holding_sessions=len(cal)-pos["entry_index"],pending_exit_reason=pending["exits"].get(symbol,""))
        openrows.append(row)
    return pd.DataFrame(daily).set_index("date"),pd.DataFrame(ledger),pd.DataFrame(actions),pd.DataFrame(openrows),pd.DataFrame(audits)

def run_all(panel,cfg,cost_bps=None):
    symbols=sorted(panel.symbol.unique())
    charts={s:load_chart(RAW/f"{s}_chart.json") for s in ["QQQ","SOXX"]+symbols}
    navs={};closed=[];actions=[];openpos=[];checks=[];daily={}
    for name in STRATEGIES:
        d,t,a,o,c=run_strategy(panel,charts,cfg,name,cost_bps)
        navs[name]=d.nav;daily[name]=d
        if not t.empty:closed.append(t)
        actions.append(a)
        if not o.empty:openpos.append(o)
        checks.append(c)
    nav=pd.DataFrame(navs)
    rate=(cfg["one_way_cost_bps"] if cost_bps is None else cost_bps)/10000
    for s in ["QQQ","SOXX"]:nav[s]=(1-rate)*charts[s].adj_close.reindex(nav.index)/charts[s].loc[nav.index[0],"adj_open"]
    flow=panel.groupby("signal_date").funding_good.first().sort_index()
    units=0.;cash=1.;values=[]
    for date in nav.index:
        previous=flow.loc[flow.index<date].iloc[-1]
        price=charts["QQQ"].loc[date,"adj_open"]
        if previous and units==0:
            units=cash/(1+rate)/price;cash=0.
        elif not previous and units>0:
            cash=units*price*(1-rate);units=0.
        values.append(cash+units*charts["QQQ"].loc[date,"adj_close"])
    nav["QQQ_funding_gate"]=values
    old=pd.read_csv(ROOT/"data/previous_results/nav.csv",index_col=0,parse_dates=True)
    nav["v1_monthly_divergence"]=old.divergence.reindex(nav.index)
    return nav,pd.concat(closed,ignore_index=True) if closed else pd.DataFrame(),pd.concat(actions,ignore_index=True),pd.concat(openpos,ignore_index=True) if openpos else pd.DataFrame(),pd.concat(checks,ignore_index=True),daily
