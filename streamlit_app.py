import gzip
import json
from urllib.request import urlopen
from datetime import datetime
from io import BytesIO
from threading import Lock
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from sheets_data import BASE, check, fetch_rows, parse_rows, read_gzip
from taiwan_fund_research import render as render_taiwan_fund_research

st.set_page_config(page_title="成長股觀測站｜銀行版", layout="wide")
st.markdown("""<style>
:root{--ink:#142938;--muted:#586a76;--border:#dbe5e8;--mint:#e8f3ef;--teal:#17664d}
.stApp{color:var(--ink);background:#f4f7f7}
.block-container{max-width:1360px;padding-top:2.3rem}
[data-testid="stMetric"]{border:1px solid var(--border);padding:.85rem 1rem;background:#fff;border-radius:.7rem}
h1,h2,h3{letter-spacing:-.035em}
.bank-hero{background:#102c32;border-radius:16px;color:#fff;padding:24px 28px;margin-bottom:16px}
.bank-hero h1{color:#fff;margin:0;font-size:clamp(1.55rem,4vw,2.8rem);padding:0}
.bank-hero p{margin:.5rem 0 0;color:#cce3da}
.lights{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:1rem 0}
.light{padding:12px 6px;text-align:center;background:#fff;border:1px solid var(--border);border-radius:.7rem}
.dot{display:block;margin:0 auto 7px;width:27px;height:27px;border-radius:50%;background:#728390}
.dot.s1{background:#bf4b47}.dot.s0{background:#258264}
.light small{display:block;color:var(--muted)}
@media(max-width:660px){.block-container{padding:1.25rem .8rem 3rem}.bank-hero{padding:18px}.light{font-size:.86rem}}
</style>""", unsafe_allow_html=True)

MARKETS = {"美股": "US", "台股": "TW"}
LIGHTS = {"rates": "利率", "conditions": "金融條件", "inflation": "通膨"}
DECISION = {"non_red": "未達紅燈門檻", "red": "已達紅燈門檻", "unknown": "風險判定待確認"}
STATES = {0: "未觸發", 1: "紅燈", None: "未知"}
FACTORS = {"growth": "營收年增", "acceleration": "成長加速度", "operating": "營益率",
           "cashflow": "營業現金流率", "value": "同產業 P/S"}
PLANS = {"A": "買入持有", "B": "紅燈全部退出", "C": "紅燈減碼 50%", "D": "紅燈轉入防禦資產"}
WINDOWS = {"GFC": "2008 金融海嘯", "COVID": "2020 疫情", "2022": "2022 升息",
           "2025_tariff": "2025 關稅", "2026_AI": "2026 AI 回檔"}
PCT_ADVISOR = ["cumulative","cagr","mdd","worst_year_return","transaction_cost_initial_fraction",
               "strategy_return","window_mdd","drawdown"]

@st.cache_resource
def last_sheet_snapshot():
    return Lock(), {}

@st.cache_data(ttl=90, show_spinner=False)
def snapshot():
    config_path = BASE / "data/sheets-url.txt"
    csv_url = config_path.read_text().strip() if config_path.exists() else ""
    try:
        account = st.secrets.get("google_service_account")
    except FileNotFoundError:
        account = None
    try:
        data, batch = parse_rows(fetch_rows(csv_url, account))
        lock, last = last_sheet_snapshot()
        with lock:
            last.update(data=data, batch=batch)
        return data, batch, "Google Sheets"
    except (OSError, KeyError, ValueError, TypeError, ImportError):
        lock, last = last_sheet_snapshot()
        with lock:
            if last:
                return last["data"], last["batch"], "Sheets 上次成功快取"
        return check(read_gzip("data/snapshot.json.gz")), None, "封存備援"

@st.cache_data(show_spinner=False)
def load_archive():
    return read_gzip("data/phase2-research.json.gz"), read_gzip("data/tracking-baseline.json.gz"), read_gzip("data/nav-monthly.json.gz")

def stamp(value):
    if not value:
        return "待核對"
    return datetime.fromisoformat(value.replace("Z","+00:00")).astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y/%m/%d %H:%M")

def percent(value):
    return "—" if value is None or pd.isna(value) else f"{value:.2%}"

def number(value):
    return "—" if value is None else f"{value:,.2f}"

def table(records, widths=None):
    st.dataframe(pd.DataFrame(records), hide_index=True, width="stretch", column_config=widths)

def csv_button(label, data, name, key=None):
    st.download_button(label, pd.DataFrame(data).to_csv(index=False).encode("utf-8-sig"), name,
        mime="text/csv", key=key, on_click="ignore")

def report_bytes(name):
    file = BASE / "reports" / (name + ".gz")
    compressed = file.read_bytes() if file.exists() else b"".join(part.read_bytes() for part in sorted(file.parent.glob(file.name + ".part*")))
    return gzip.decompress(compressed)

def report_file(label, name):
    st.download_button(label, report_bytes(name), name, on_click="ignore", key=name)

def funding(macro):
    lights = "".join(f'<div class="light"><span class="dot s{macro["categoryStates"][key]}"></span>'
        f'<strong>{label}</strong><small>{STATES[macro["categoryStates"][key]]}</small></div>' for key,label in LIGHTS.items())
    st.markdown(f'<div class="lights">{lights}</div>', unsafe_allow_html=True)
    low, high = macro["scoreRange"]
    score = str(low) if low == high else f"{low}–{high}"
    st.write(f"**{score}／3　{DECISION[macro['decision']]}**")
    st.caption(f"行情 {macro['marketAsOf']} · 總經版本 {macro.get('macro_vintage_date') or '待核對'} · 計算 {stamp(macro.get('calculatedAt'))}")
    if macro.get("warning"):
        st.warning(macro["warning"])
    with st.expander("總經數值、變動與資料版本"):
        rows = []
        for series, label, value, change in [
            ("FEDFUNDS","聯邦基金利率","fed_funds","fed_funds_90d_change"),
            ("DFII10","10 年實質殖利率","real_yield","real_yield_90d_change"),
            ("NFCI","芝加哥聯準銀行金融條件","financial_conditions","financial_conditions_90d_change"),
            ("CPIAUCSL","CPI 年增率／3 個月年化","cpi_yoy","cpi_3m_annualized")]:
            info = macro["seriesMeta"].get(series,{})
            rows.append({"細項":label,"數值":macro.get(value),"變動或年化":macro.get(change),
                         "觀測日期":info.get("observationDate"),"版本日期":info.get("vintageDate"),"來源":series})
        table(rows)
        st.caption("利率、金融條件、通膨各至多一分。最低分已達 2 為紅燈，最高分低於 2 則未達門檻，其他為未知。")

def markets(market, block, signal_available):
    stocks = block["stocks"]
    by_rank = sorted(stocks, key=lambda s: s["researchRank"]["rank"] if s["researchRank"] else 1000)
    with st.container(border=True):
        st.subheader("全球資金溫度")
        funding(block["macro"])
    c = st.columns(3)
    c[0].metric("研究池", len(stocks))
    c[1].metric("有分數的研究候選", sum(s["researchRank"] is not None for s in stocks))
    c[2].metric("最後完成收盤", block["macro"]["marketAsOf"])
    scope = st.segmented_control("顯示範圍", ["研究候選","全部研究池","主策略正式候選","背離長抱候選"], default="研究候選", key=f"scope-{market}")
    options = st.columns([2,1,1])
    query = options[0].text_input("搜尋代號、公司或產業", key=f"query-{market}").strip().casefold()
    industry = options[1].selectbox("產業",["全部", *sorted({s["industry"] for s in stocks if s.get("industry")})],key=f"industry-{market}")
    order = options[2].selectbox("排序",["研究排名","營收成長","126 日回撤"],key=f"sort-{market}")
    def visible(stock):
        scope_ok = {"研究候選": bool(stock["researchRank"]), "全部研究池": True,
                    "主策略正式候選": signal_available and stock["evaluation"].get("primary"),
                    "背離長抱候選": signal_available and stock["evaluation"].get("buy_hold")}.get(scope, True)
        return scope_ok and (industry=="全部" or stock.get("industry")==industry) and query in " ".join(str(stock.get(k) or "") for k in ("symbol","name","industry")).casefold()
    selected_stocks = list(filter(visible, by_rank))
    if order!="研究排名":
        field = ("financial","revenue_growth") if order=="營收成長" else ("features","drawdown_126")
        def sort_key(stock):
            value = stock[field[0]].get(field[1])
            return (value is None, 0 if value is None else (-value if order=="營收成長" else value))
        selected_stocks.sort(key=sort_key)
    rows = []
    for stock in selected_stocks:
        rank = stock["researchRank"] or {}
        f, e = stock["financial"], stock["evaluation"]
        rows.append({"排名":rank.get("rank"),"代號":stock["symbol"],"公司":stock["name"],"產業":stock.get("industry"),
            "總分":round(rank["score"],2) if rank else None,"收盤":stock["close"],"行情日":stock["priceDate"],
            "TTM 營收年增":percent(f.get("revenue_growth")),"單季營收年增":percent(f.get("quarterly_growth")),
            "營益率":percent(f.get("operating_margin")),"營業現金流率":percent(f.get("cfo_margin")),
            "P/S":stock["features"].get("price_sales"),"財報期":f.get("latest_period"),
            "申報日":f.get("max_input_filed") or f.get("filingEvidence",{}).get("publishedAt"),
            "資料品質":"待核對" if stock.get("sourceError") or not stock["integrity"] else "已核對",
            "正式訊號":("主策略" if e.get("primary") else "背離長抱" if e.get("buy_hold") else "—") if signal_available else "待更新"})
    if not signal_available:
        st.warning("目前保留可核對的研究排名，正式訊號待更新。")
    if not rows:
        st.info("目前篩選條件沒有符合的標的。")
        return
    table(rows)
    csv_button("匯出目前篩選 CSV",rows,f"{market}-research.csv",f"export-{market}")
    stock = st.selectbox("查看單檔分數、財報與行情",selected_stocks,
        format_func=lambda x: f"{x['symbol']} · {x['name']}",key=f"detail-{market}")
    f,e=stock["financial"],stock["evaluation"]
    rank=stock["researchRank"]
    if rank:
        st.write(f"**#{rank['rank']}　{rank['score']:.2f} 分** · 同業 {rank['detail']['industrySize']} 檔")
        table([{"因子":FACTORS[p["key"]],"原始值":number(p["value"]) if p["key"]=="value" else percent(p["value"]),
            "百分位":percent(p["percentile"]),"權重":percent(p["weight"]),
            "貢獻分":round(p["weight"]*p["percentile"]*100,2)} for p in rank["detail"]["parts"]])
    st.caption(f"財報期 {f.get('latest_period') or '待核對'} · 首次／最近可核對觀測 {stamp(stock.get('financialObservedAt'))} · 行情來源 {stock.get('priceSource') or '待核對'}")
    if stock.get("sourceWarning") or stock.get("sourceError"):
        st.warning(stock.get("sourceWarning") or stock["sourceError"])
    if e.get("reasons"):
        st.caption("｜".join(e["reasons"]))
    technical = stock["features"]
    st.caption(f"126 日回撤 {percent(technical.get('drawdown_126'))} · 63 日相對報酬 {percent(technical.get('relative_63'))} · 可用交易日 {technical.get('history_sessions','—')}")
    if stock["history"]:
        history = pd.DataFrame(stock["history"]).rename(columns={"close":"調整收盤"})
        history["date"] = pd.to_datetime(history["date"])
        st.line_chart(history,x="date",y="調整收盤")

def backtest(market, research, nav):
    if market=="TW":
        st.warning("台股 2016–2026 起跑組為研究版。現有封存驗收沒有確認符合公告時點、財報版本及成交證據的可信起始年。")
    else:
        st.info("2006 起跑可同時參考發布時點延後情境。早期金融條件採信用利差代理，存在發布時點限制。")
        table([{**row,"cumulative":percent(row["cumulative"]),"cagr":percent(row["cagr"]),"max_drawdown":percent(row["max_drawdown"])} for row in research["usDelay"]])
    rows = research[market.lower()]
    table([{"起始年":r["start_year"],"A 累積":percent(r["A_cumulative"]),"B 累積":percent(r["B_cumulative"]),
        "A CAGR":percent(r["A_cagr"]),"B CAGR":percent(r["B_cagr"]),"A 最大回撤":percent(r["A_max_drawdown"]),
        "B 最大回撤":percent(r["B_max_drawdown"]),"B 額外買入":r["B_extras"]} for r in rows])
    year=st.selectbox("歷史淨值起始年", [r["start_year"] for r in reversed(rows)],key=f"curve-{market}")
    curve=pd.DataFrame(nav[market][str(year)],columns=["日期","A","B"])
    curve["日期"]=pd.to_datetime(curve["日期"])
    st.line_chart(curve,x="日期",y=["A","B"])
    st.caption("封存模擬淨值，起始本金標準化為 1。圖為每月最後交易日，截止 2026-11-04。")
    csv_button("匯出每月歷史淨值",curve,f"{market}-{year}-nav.csv",f"nav-{market}")
    report_file("年度回測比較 CSV","us-ab.csv" if market=="US" else "tw-ab-research.csv")
    if market=="US":
        report_file("2006 發布時點敏感度 CSV","us-2006-release-delay.csv")
    legacy_backtest(market)

def legacy_backtest(market):
    with st.expander("歷史研究存檔、比較基準與逐筆出場"):
        old = read_gzip("data/legacy-research.json.gz")
        basis = st.selectbox("美股回測版本",["拆股尺度修正版","同條件原算法"]) if market=="US" else None
        key = "usOriginal" if basis=="同條件原算法" else market.lower()
        st.caption("封存研究使用當代研究樣本，有存活者與選樣偏誤。美股同條件原算法保留拆股尺度限制。台股早期財報不足組只列資料品質。")
        table([{"起始年":r["cohort_year"],"主策略累積":percent(None if r.get("partial") else r["primary_total_return"]),
            "背離長抱累積":percent(None if r.get("partial") else r["buy_hold_total_return"]),
            "主策略最大回撤":percent(None if r.get("partial") else r["primary_max_drawdown"]),
            "背離長抱最大回撤":percent(None if r.get("partial") else r["buy_hold_max_drawdown"]),
            "資料判讀":r["coverage_note"]} for r in old[key]])
        table([{"代號":r["symbol"],"訊號日":r["signalDate"],"成交日":r["exitDate"],
            "淨報酬":percent(r["returnNet"]),"出場原因":r["reason"],"總經版本":r["macroVintage"]}
            for r in old["exitExamples"] if r["market"]==market])
        if market=="US":
            benchmark=pd.read_csv(BytesIO(report_bytes("us-primary-vs-qqq-2006-2026.csv")))
            st.subheader("美股主策略與 QQQ")
            st.line_chart(benchmark.rename(columns={"total_return":"主策略","qqq_return":"QQQ"}),
                x="year",y=["主策略","QQQ"])
            st.caption("各點為不同年度獨立起跑至共同終點的累積報酬。")
            report_file("主策略與 QQQ 比較 CSV","us-primary-vs-qqq-2006-2026.csv")
            report_file("資金紅燈全清比較 CSV","funding-exit-comparison-2016-2026.csv")
            report_file("資金紅燈出場事件 CSV","funding-exit-events-2016-2026.csv")
        for label,name in [("完整互動回測報告 HTML","growth-backtest-2016-2026.html"),
            ("歷年比較 CSV","comparison-summary-2016-2026.csv"),("逐筆出場稽核 CSV","exit-audits-2016-2026.csv"),
            ("出場案例證據 JSON","exit-examples-evidence.json"),("舊報告對照 CSV","prior-run-reconciliation.csv")]:
            report_file(label,name)

def tracking(market, snapshot_data, baseline):
    block = snapshot_data["markets"][market]
    year=st.selectbox("策略起始年", sorted({p["year"] for p in baseline[market]},reverse=True),key=f"mark-{market}")
    marks=block["marks"].get(str(year))
    st.info(f"最後完整模擬 {baseline['date']}。靜態估值日 {marks['date'] if marks else '待核對'}。封存後採相同還原價格序列，部位與現金固定。")
    plans = []
    for p in [row for row in baseline[market] if row["year"]==year]:
        mark=next((r for r in marks["portfolios"] if r["variant"]==p["variant"]),None) if marks else None
        core=sum(x["role"]=="core" for x in p["positions"])
        plans.append({"方案":p["variant"],"封存淨值":number(p["nav"]),"靜態估值":number(mark["value"] if mark else None),
            "封存後變動":percent(mark["value"]/p["nav"]-1 if mark else None),"封存現金":number(p["cash"]),
            "核心":core,"額外":len(p["positions"])-core,"封存成本":number(p["cost"])})
        with st.expander(f"{p['variant']} 版核心／額外模擬部位"):
            marked={x["symbol"]:x["markedValue"] for x in mark["positions"]} if mark else {}
            table([{"代號":x["symbol"],"分類":"核心" if x["role"]=="core" else "額外","封存市值":number(x["value"]),
                "靜態市值":number(marked.get(x["symbol"]))} for x in p["positions"]])
    table(plans)
    csv_button("匯出策略估值 CSV",plans,f"{market}-{year}-tracking.csv",f"track-{market}")
    st.caption(f"最近訊號計算 {stamp(block['signalAt'])} · {('USD' if market=='US' else 'TWD')}")

def advisor(research):
    st.caption("封存至 2026-11-04。早期信用利差代理與發布時點存在限制。共同基金採可得淨值，銀行申贖截止時間及費用依通路而異。")
    products=sorted({x["symbol"] for x in research["advisor"]})
    product=st.selectbox("研究商品",products,index=products.index("QQQ"))
    candidates=[r for r in research["advisor"] if r["symbol"]==product]
    years=sorted({r["start_year"] for r in candidates})
    cohort=st.selectbox("投資起始年",years,key=f"advisor-year-{product}")
    selected=[r for r in candidates if r["start_year"]==cohort]
    table([{"方案":r["variant"]+" "+PLANS[r["variant"]],"累積":percent(r["cumulative"]),"CAGR":percent(r["cagr"]),
        "最大回撤":percent(r["mdd"]),"最差年度":r["worst_year"],"最差年報酬":percent(r["worst_year_return"]),
        "高點至回本（天）":r["peak_to_recovery_calendar_days"],"成本／初始資金":percent(r["transaction_cost_initial_fraction"]),
        "買入次數":r["buy_count"],"賣出次數":r["sell_count"]} for r in selected])
    event=st.selectbox("事件回撤比較", list(WINDOWS), format_func=WINDOWS.get)
    events=[r for r in research["windows"] if r["symbol"]==product and r["start_year"]==cohort and str(r["window"])==str(event)]
    if events:
        table([{"方案":r["variant"]+" "+PLANS[r["variant"]],"起日":r["first"],"迄日":r["last"],
            "部分區間":r["partial"],"事件報酬":percent(r["strategy_return"]),"最大回撤":percent(r["window_mdd"]),
            "紅燈交易日":r["red_sessions"]} for r in events])
    else:
        st.caption("此起跑年度在該事件沒有研究區間。")
    st.subheader("新台幣 1,000 萬元模擬組合")
    st.caption("2006 年起投，SPY 40%、QQQ 20%、AGG 30%、IEF 10%，計入匯率。")
    options=list(dict.fromkeys(r["window"] for r in research["example"]))
    example=st.selectbox("台幣組合市場事件",options,format_func=lambda x: WINDOWS.get(str(x),str(x)))
    table([{"方案":r["variant"]+" "+PLANS[r["variant"]],"高點日":r["peak_date"],"高點淨值":number(r["peak_twd"]),
        "低點日":r["trough_date"],"低點淨值":number(r["trough_twd"]),"虧損":number(r["loss_twd"]),
        "回撤":percent(r["drawdown"]),"同額虧損":number(-r["drawdown"]*10_000_000),"回本日":r["recovery_date"],
        "低點至回本（日）":r["days_trough_to_recovery"]} for r in research["example"] if r["window"]==example])
    for label,name in [("各商品／年度完整 CSV","advisor-by-year.csv"),("事件比較 CSV","advisor-events.csv"),
        ("事件模擬成交與成本 CSV","advisor-event-fills.csv"),("台幣組合事件 CSV","advisor-twd-events.csv")]:
        report_file(label,name)

@st.cache_data(ttl=90, show_spinner=False)
def latest_runner_attempt():
    url = "https://raw.githubusercontent.com/monstervincentw-glitch/growth-observatory-bank/research-data/attempt.json"
    try:
        with urlopen(url, timeout=5) as response:
            attempt = json.loads(response.read(30_000))
    except (OSError, ValueError):
        return None
    return attempt if attempt.get("market") in ("US","TW") and attempt.get("status") in ("published","same_close","failed") else None

def records(data, source, batch):
    st.write(f"**來源** {source}　**批次** {batch or '封存備援'}")
    st.write(f"**同步驗收標記** {data['qa']}")
    attempt = latest_runner_attempt()
    if attempt:
        result = {"same_close":"同一收盤，保留快照","failed":"來源更新失敗","published":"研究批次已發布"}[attempt["status"]]
        st.write(f"**最近雲端收盤工作** {attempt['market']} · {result} · {stamp(attempt.get('at'))}")
        if attempt["status"] == "failed":
            st.warning("最近一次更新未通過。頁面沿用已核對快照。")
    for label,market in MARKETS.items():
        block=data["markets"][market]
        macro=block["macro"]
        st.write(f"**{label}**　收盤 {macro['marketAsOf']} · 發布 {stamp(macro.get('lastMarketPublishedAt'))} · {macro['ruleVersion']}")
    table([{"市場":j["market"],"狀態":j["status"],"處理數":j.get("updated"),"失敗數":j.get("failed"),
        "沿用數":j.get("retained"),"備援數":j.get("fallback"),"更新時間":stamp(j.get("updated_at"))} for j in data["jobs"]])
    st.markdown("""### 策略規則
- **主策略 A** 最多十檔核心。**B** 在核心補滿後，從已通過原始進場條件的候選中加買，分數須嚴格超過當日核心最高分。每筆以當時淨值 10% 為目標，額外部位與核心共用出場規則。
- **基本面門檻** TTM 營收年增至少 10%、最新單季營收不年減、營益率和營業現金流率為正。依原版納入財報時效與相較前次財報惡化條件。
- **候選排名** 美股依四項基本面 75% 與同產業 P/S 25% 評分。台股依營收年增 50%、營益率 25%、營業現金流率 25% 評分。單檔明細顯示百分位、權重與貢獻分。
- **背離長抱** 保留原版技術背離與資金條件。研究排名、正式訊號與封存後靜態估值分開呈現。
""")
    report_file("下載研究驗收紀錄","phase1-acceptance.md")

st.markdown('<header class="bank-hero"><h1>成長股觀測站</h1><p>銀行版 · 美股與台股研究、策略回測與顧問風控</p></header>', unsafe_allow_html=True)
if st.button("重新讀取資料"):
    snapshot.clear()
data, batch, source = snapshot()
research, baseline, nav = load_archive()
st.caption(f"來源 {source}　·　美股 {data['markets']['US']['macro']['marketAsOf']}　·　台股 {data['markets']['TW']['macro']['marketAsOf']}　·　完整回測 {data['historyThrough']}")
if source != "Google Sheets":
    st.warning("本次 Sheets 讀取未通過。畫面標示目前採用的最後成功快照或封存備援。")
page=st.radio("研究功能",["市場觀測","主策略回測","策略模擬","顧問風控","更新紀錄與規則"],horizontal=True,label_visibility="collapsed")
if page in ("市場觀測","主策略回測","策略模擬"):
    market=MARKETS[st.segmented_control("市場",list(MARKETS),default="美股") or "美股"]
if page=="市場觀測":
    block = data["markets"][market]
    market_date = datetime.fromisoformat(block["macro"]["marketAsOf"]).date()
    age = (datetime.now(ZoneInfo("Asia/Taipei")).date() - market_date).days
    attempt = latest_runner_attempt()
    failed_since_publish = bool(attempt and attempt["market"]==market and attempt["status"]=="failed" and
        attempt.get("at","") > block["macro"].get("lastMarketPublishedAt",""))
    markets(market,block,source=="Google Sheets" and 0<=age<=5 and not failed_since_publish)
elif page=="主策略回測":
    backtest(market,research,nav)
elif page=="策略模擬":
    tracking(market,data,baseline)
elif page=="顧問風控":
    advisor_page=st.radio("顧問風控研究",["原 14 項 ETF／基金／債券","台灣可申購共同基金"],horizontal=True)
    if advisor_page=="台灣可申購共同基金":
        render_taiwan_fund_research()
    else:
        advisor(research)
else:
    records(data,source,batch)
