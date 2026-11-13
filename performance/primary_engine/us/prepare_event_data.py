"""Build filing-event financial history and weekly ALFRED vintages for daily signals."""
from __future__ import annotations
import argparse, concurrent.futures, hashlib, json, urllib.parse
from pathlib import Path
import pandas as pd
from download_data import ROOT, RAW, get, load_chart
from fundamentals import CompanyFacts

def signal_calendar(cfg):
    cal=load_chart(RAW/"QQQ_chart.json").index
    before=cal[cal<pd.Timestamp(cfg["start"])][-1]
    return cal[(cal>=before)&(cal<=pd.Timestamp(cfg["end"]))]

def weekly_dates(cfg):
    cal=signal_calendar(cfg)
    return list(pd.Series(cal,index=cal).groupby(cal.to_period("W-FRI")).last())

def download_macro(cfg):
    dates=weekly_dates(cfg)
    folder=ROOT/"data/macro_weekly";folder.mkdir(parents=True,exist_ok=True)
    def series_job(name):
        rows=[]
        for offset in range(0,len(dates),10):
            ds=dates[offset:offset+10]
            url="https://alfred.stlouisfed.org/graph/alfredgraph.csv?"+urllib.parse.urlencode({"id":",".join([name]*len(ds)),"vintage_date":",".join(d.strftime("%Y-%m-%d") for d in ds)})
            path=folder/f"alfred_{name}_{offset:03d}.csv"
            try:
                expected={f"{name}_{d:%Y%m%d}" for d in ds}
                if path.exists() and not expected.issubset(pd.read_csv(path,nrows=1).columns):
                    get(url,path,refresh=True)
                get(url,path)
                data=pd.read_csv(path,nrows=1)
                assert expected.issubset(data.columns),"Unexpected vintage headers"
                rows.append({"series":name,"batch":offset,"status":"ok"})
            except Exception as exc:
                rows.append({"series":name,"batch":offset,"status":"failed","error":str(exc)})
            print("macro",name,offset,rows[-1]["status"],flush=True)
        return rows
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        audit=[r for block in pool.map(series_job,cfg["macro_series"]) for r in block]
    (ROOT/"results/macro_acquisition.json").write_text(json.dumps(audit,indent=2))
    if any(r["status"]!="ok" for r in audit):raise RuntimeError("Incomplete weekly macro vintages; see acquisition audit")

def company_history(job):
    symbol,cfg=job
    facts_path=RAW/f"{symbol}_facts.json"
    out=ROOT/f"data/processed/financial_events/{symbol}.csv"
    out.parent.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256(facts_path.read_bytes()+(ROOT/"fundamentals.py").read_bytes()+json.dumps(cfg,sort_keys=True).encode()).hexdigest()
    key=out.with_suffix(".sha256")
    if out.exists() and key.exists() and key.read_text()==digest:
        return symbol,len(pd.read_csv(out))
    facts=CompanyFacts(facts_path)
    cal=signal_calendar(cfg)
    dates={cal[0]}
    for frame in facts.metrics.values():
        if frame.empty:continue
        for filed in frame.filed.unique():
            ready=pd.Timestamp(filed)+pd.Timedelta(days=cfg["fundamental_lag_days"])
            if ready<=cal[0] or ready>cal[-1]:continue
            index=cal.searchsorted(ready)
            if index<len(cal):dates.add(cal[index])
    rows=[]
    for date in sorted(dates):
        snap=facts.snapshot(date,cfg["fundamental_lag_days"])
        snap.update(symbol=symbol,effective_date=date)
        rows.append(snap)
    pd.DataFrame(rows).to_csv(out,index=False);key.write_text(digest)
    return symbol,len(rows)

def prepare_financials(cfg):
    coverage=pd.read_csv(ROOT/"data/previous_results/data_coverage.csv")
    symbols=coverage.loc[coverage.status=="included","symbol"].tolist()
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as pool:
        for s,n in pool.map(company_history,[(s,cfg) for s in symbols]):
            print("financial filing events",s,n,flush=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--only",choices=["all","macro","financials"],default="all");args=parser.parse_args()
    cfg=json.loads((ROOT/"config.json").read_text())
    if args.only in ["all","macro"]:download_macro(cfg)
    if args.only in ["all","financials"]:prepare_financials(cfg)

if __name__=="__main__":main()
