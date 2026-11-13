import gzip
import json
from urllib.request import Request, urlopen
from datetime import datetime
from io import BytesIO
from os import environ
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from bank_layout import (event_drawdown_chart, funding_dashboard, header, layout_style,
                         readable_table, sidebar_brand, status_line, strategy_comparison, trend_chart)
from sheets_data import BASE, SnapshotReader, fetch_rows, read_gzip
from shared.validation import health
from shared.candidates import FORMAL, PENDING, RESEARCH, SCOPES, candidate_status, in_scope
from taiwan_fund_research import render as render_taiwan_fund_research
from performance_view import Reader as PerformanceReader, fetch as fetch_performance, overlay as overlay_performance
from performance.publication import decode as decode_performance

st.set_page_config(page_title="成長股觀測站｜銀行版", layout="wide", initial_sidebar_state="auto")

MARKETS = {"美股": "US", "台股": "TW"}
FACTORS = {"growth": "營收年增", "acceleration": "成長加速度", "operating": "營益率",
           "cashflow": "營業現金流率", "value": "同產業 P/S"}
PLANS = {"A": "買入持有", "B": "紅燈全部退出", "C": "紅燈減碼 50%", "D": "紅燈轉入防禦資產"}
WINDOWS = {"GFC": "2008 金融海嘯", "COVID": "2020 疫情", "2022": "2022 升息",
           "2025_tariff": "2025 關稅", "2026_AI": "2026 AI 回檔"}
PCT_ADVISOR = ["cumulative","cagr","mdd","worst_year_return","transaction_cost_initial_fraction",
               "strategy_return","window_mdd","drawdown"]

@st.cache_resource
def sheet_reader():
    return SnapshotReader(environ.get("RESEARCH_CACHE_PATH", "/tmp/growth-bank-research/snapshot.json"))


@st.cache_data(ttl=90, show_spinner=False)
def snapshot():
    config_path = BASE / "data/sheets-url.txt"
    csv_url = environ.get("RESEARCH_SHEETS_URL") or (config_path.read_text().strip() if config_path.exists() else "")
    try:
        account = st.secrets.get("google_service_account")
    except FileNotFoundError:
        account = None
    entry, state = sheet_reader().read(lambda: fetch_rows(csv_url, account))
    return entry["snapshot"] if entry else None, state

@st.cache_data(show_spinner=False)
def load_archive():
    return read_gzip("data/phase2-research.json.gz"), read_gzip("data/tracking-baseline.json.gz"), read_gzip("data/nav-monthly.json.gz")

def stamp(value):
    if not value:
        return "待核對"
    return datetime.fromisoformat(value.replace("Z","+00:00")).astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M")

def percent(value):
    return "—" if value is None or pd.isna(value) else f"{value:.2%}"

def number(value):
    return "—" if value is None else f"{value:,.2f}"

def table(records, *, label):
    readable_table(pd.DataFrame(records), label=label)

def csv_button(label, data, name, key=None):
    st.download_button(label, pd.DataFrame(data).to_csv(index=False).encode("utf-8-sig"), name,
        mime="text/csv", key=key, on_click="ignore")

def report_bytes(name):
    file = BASE / "reports" / (name + ".gz")
    compressed = file.read_bytes() if file.exists() else b"".join(part.read_bytes() for part in sorted(file.parent.glob(file.name + ".part*")))
    return gzip.decompress(compressed)

def report_file(label, name):
    st.download_button(label, report_bytes(name), name, on_click="ignore", key=name)

def funding_details(macro):
    with st.expander("總經數值、變動與資料版本"):
        vintage = macro.get("macro_vintage_date") or "待核對"
        st.caption(f"收盤 {macro['marketAsOf']} · 總經版本 {vintage} · 計算 {stamp(macro.get('calculatedAt'))}（台北時間）")
        rows = []
        for series, label, value, change in [
            ("FEDFUNDS","聯邦基金利率","fed_funds","fed_funds_90d_change"),
            ("DFII10","10 年實質殖利率","real_yield","real_yield_90d_change"),
            ("NFCI","芝加哥聯準銀行金融條件","financial_conditions","financial_conditions_90d_change"),
            ("CPIAUCSL","CPI 年增率／3 個月年化","cpi_yoy","cpi_3m_annualized")]:
            info = macro["seriesMeta"].get(series,{})
            rows.append({"細項":label,"數值":macro.get(value),"變動或年化":macro.get(change),
                         "觀測日期":info.get("observationDate"),"版本日期":info.get("vintageDate"),"來源":series})
        table(rows, label="資金溫度資料版本")
        st.caption("利率、金融條件、通膨各至多一分。最低分已達 2 則觸發，最高分低於 2 則未達門檻，其他為未知。")

def markets(market, block, signal_available, market_reason=None):
    stocks = block["stocks"]
    by_rank = sorted(stocks, key=lambda s: s["researchRank"]["rank"] if s["researchRank"] else 1000)
    funding_dashboard(block["macro"])
    states = {stock["key"]: candidate_status(stock, block["macro"], signal_available, market_reason)
              for stock in stocks}
    scope_control, search_control, industry_control, order_control = st.columns([1.4, 1.4, 1, 1], vertical_alignment="bottom")
    scope = scope_control.selectbox("顯示範圍", SCOPES, key=f"scope-{market}")
    study_count = sum(state["research"] for state in states.values())
    formal_count = sum(state["formal"] for state in states.values())
    formal_summary = f"{formal_count} 檔" if signal_available else "待驗證"
    st.caption(f"背離研究候選 {study_count} 檔 · 背離正式買入訊號 {formal_summary}")
    query = search_control.text_input("搜尋代號、公司或產業", key=f"query-{market}").strip().casefold()
    industry = industry_control.selectbox("產業",["全部", *sorted({s["industry"] for s in stocks if s.get("industry")})],key=f"industry-{market}")
    order = order_control.selectbox("排序",["研究排名","營收成長","126 日回撤"],key=f"sort-{market}")
    def visible(stock):
        scope_ok = in_scope(stock, states[stock["key"]], scope, signal_available)
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
        state = states[stock["key"]]
        rows.append({"排名":rank.get("rank"),"代號":stock["symbol"],"公司":stock["name"],"產業":stock.get("industry"),
            "總分":round(rank["score"],2) if rank else None,"收盤":stock["close"],"行情日":stock["priceDate"],
            "TTM 營收年增":percent(f.get("revenue_growth")),"單季營收年增":percent(f.get("quarterly_growth")),
            "營益率":percent(f.get("operating_margin")),"營業現金流率":percent(f.get("cfo_margin")),
            "P/S":stock["features"].get("price_sales"),"財報期":f.get("latest_period"),
            "申報日":f.get("max_input_filed") or f.get("filingEvidence",{}).get("publishedAt"),
            "資料品質":"待核對" if stock.get("sourceError") or not stock["integrity"] else "已核對",
            "主策略正式訊號":("符合正式條件" if e["primary"] else "—") if signal_available else "待驗證",
            "背離研究":"符合" if state["research"] else "—",
            "背離正式狀態":state["label"],"背離阻擋原因":"｜".join(state["reasons"])})
    if not signal_available:
        st.warning("目前保留可核對的研究排名，正式訊號待更新。")
    if not rows:
        funding_details(block["macro"])
        if scope == FORMAL and not signal_available:
            st.info(market_reason or "正式訊號待驗證，請查看資料狀態。")
        elif scope == FORMAL and not formal_count:
            st.info("本批沒有符合背離正式買入條件的標的。可切換至背離研究候選查看逐檔原因。")
        else:
            st.info("目前篩選條件沒有符合的標的。")
        return
    main_columns = ["排名", "代號", "公司", "產業", "總分", "收盤", "行情日", "TTM 營收年增",
                    "背離正式狀態" if scope in (RESEARCH, FORMAL, PENDING) else "主策略正式訊號"]
    detail_columns = ["代號", "單季營收年增", "營益率", "營業現金流率", "P/S", "財報期", "申報日", "資料品質"]
    if scope in (RESEARCH, FORMAL, PENDING):
        detail_columns += ["背離研究", "背離正式狀態", "背離阻擋原因"]
    page_size = 20
    page_key = f"stocks-page-{market}"
    start = min(st.session_state.get(page_key, 0), (len(rows) - 1) // page_size * page_size)
    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        st.subheader(scope, anchor=False)
        st.write(f"{start + 1}–{min(start + page_size, len(rows))} ／ {len(rows)} 檔")
        if len(rows) > page_size:
            st.button("上一頁", key=f"stocks-prev-{market}", disabled=start == 0,
                      on_click=st.session_state.__setitem__, args=(page_key, start - page_size))
            st.button("下一頁", key=f"stocks-next-{market}", disabled=start + page_size >= len(rows),
                      on_click=st.session_state.__setitem__, args=(page_key, start + page_size))
    visible_rows = pd.DataFrame(rows[start:start + page_size])
    readable_table(visible_rows[main_columns], label="市場研究候選", layout="market")
    with st.expander("本頁候選的財報與資料品質"):
        readable_table(visible_rows[detail_columns], label="候選財報與資料品質")
    csv_button("匯出目前篩選 CSV",rows,f"{market}-research.csv",f"export-{market}")
    funding_details(block["macro"])
    stock = st.selectbox("查看單檔分數、財報與行情",selected_stocks,
        format_func=lambda x: f"{x['symbol']} · {x['name']}",key=f"detail-{market}")
    f,e=stock["financial"],stock["evaluation"]
    rank=stock["researchRank"]
    if rank:
        st.write(f"**#{rank['rank']}　{rank['score']:.2f} 分** · 同業 {rank['detail']['industrySize']} 檔")
        table([{"因子":FACTORS[p["key"]],"原始值":number(p["value"]) if p["key"]=="value" else percent(p["value"]),
            "百分位":percent(p["percentile"]),"權重":percent(p["weight"]),
            "貢獻分":round(p["weight"]*p["percentile"]*100,2)} for p in rank["detail"]["parts"]], label="單檔評分")
    st.caption(f"財報期 {f.get('latest_period') or '待核對'} · 首次／最近可核對觀測 {stamp(stock.get('financialObservedAt'))} · 行情來源 {stock.get('priceSource') or '待核對'}")
    if stock.get("sourceWarning") or stock.get("sourceError"):
        st.warning(stock.get("sourceWarning") or stock["sourceError"])
    state = states[stock["key"]]
    st.write(f"**背離正式狀態** {state['label']}")
    if state["reasons"]:
        st.caption("｜".join(state["reasons"]))
    technical = stock["features"]
    st.caption(f"126 日回撤 {percent(technical.get('drawdown_126'))} · 63 日相對報酬 {percent(technical.get('relative_63'))} · 可用交易日 {technical.get('history_sessions','—')}")
    if stock["history"]:
        history = pd.DataFrame(stock["history"]).rename(columns={"close":"調整收盤"})
        history["date"] = pd.to_datetime(history["date"])
        trend_chart(history.rename(columns={"date": "日期"}), "日期", ["調整收盤"], ylabel="調整收盤", height=315)

def market_picker(column, *, label_visibility="visible"):
    return MARKETS[column.segmented_control("市場", list(MARKETS), default="美股", key="bank-market",
                                            label_visibility=label_visibility) or "美股"]


def fetch_published(path):
    base = "https://raw.githubusercontent.com/monstervincentw-glitch/growth-observatory-bank/performance-data/"
    with urlopen(Request(base + path, headers={"Cache-Control":"no-cache"}), timeout=5) as response:
        raw = response.read(2_000_001)
    if len(raw)>2_000_000:
        raise ValueError("績效資料超過大小限制")
    return json.loads(raw)


@st.cache_data(ttl=90, show_spinner=False)
def performance_status():
    try:
        payload = fetch_published('status.json')
        if payload.get('schema') != 1 or set(payload['results']) != {'ETF','US','TW'}:
            raise ValueError('績效狀態格式錯誤')
        return payload['results']
    except (OSError, ValueError, TypeError, KeyError):
        return {}


@st.cache_data(ttl=90, show_spinner=False)
def primary_performance(market):
    try:
        release = fetch_published(f'primary/{market}/release.json')
        keys = {(row['year'],row['variant']) for row in release['summary']}
        years = range(2006 if market == 'US' else 2016, 2027)
        if (release.get('schema') != 1 or release['market'] != market or
                keys != {(year,plan) for year in years for plan in 'AB'} or
                len(keys) != len(release['summary']) or
                any(row['through'] != release['through'] for row in release['summary'])):
            raise ValueError('主策略績效批次不完整')
        return release
    except (OSError, ValueError, TypeError, KeyError):
        return None


def backtest(research, nav):
    with st.container(horizontal=True, gap="small", vertical_alignment="bottom"):
        market = market_picker(st)
        rows = research[market.lower()]
        year = st.selectbox("歷史淨值起始年", [r["start_year"] for r in reversed(rows)], key=f"curve-{market}", width=152)
    live=primary_performance(market)
    if live:
        summary={(r['year'],r['variant']):r for r in live['summary']}
        rows=[r | {f"{plan}_{field}":summary[r["start_year"],plan][field]
                   for plan in 'AB' for field in ('cumulative','cagr','max_drawdown')} for r in rows]
    st.subheader("A／B 歷史淨值")
    curve = pd.DataFrame(nav[market][str(year)], columns=["日期", "A", "B"])
    curve["日期"] = pd.to_datetime(curve["日期"])
    if live:
        updated=pd.DataFrame({plan:dict(live['curves'][f'{market}_{year}_{plan}']) for plan in 'AB'}).rename_axis("日期").reset_index()
        updated["日期"]=pd.to_datetime(updated["日期"])
        curve=pd.concat([curve,updated],ignore_index=True)
    trend_chart(curve, "日期", ["A", "B"], ylabel="淨值倍數")
    if live:
        st.caption(f"已驗證主策略回測 · 結算 {live['through']} · GitHub 正式績效")
        for plan in 'AB':
            info=live['latest'][f'{market}_{year}_{plan}']
            with st.expander(f"{plan} 版最新模擬持倉與交易"):
                if info['positions']:
                    table(info['positions'],label=f"{plan} 版模擬持倉")
                if info['orders']:
                    table(info['orders'],label=f"{plan} 版模擬交易")
    else:
        st.caption("封存模擬淨值，起始本金標準化為 1。圖為每月最後交易日，截止 2026-11-04。")
    marks = (data or {}).get("markets", {}).get(market, {}).get("marks", {}).get(str(year))
    if marks:
        selected = next(row for row in rows if row["start_year"] == year)
        block = []
        for row in marks["portfolios"]:
            variant = row["variant"]
            original = (selected[f"{variant}_cumulative"] + 1) * (100_000 if market == "US" else 3_000_000)
            block.append({"方案": variant, "持倉估值": number(row["value"]),
                          "相對封存日變動": percent(row["value"] / original - 1)})
        st.caption(f"持倉估值 {marks['date']} · 策略回測 2026-11-04 · 依封存部位及已驗證收盤價")
        table(block, label="封存持股最新估值")
    status=performance_status().get(market,{})
    if status.get('status') == 'WAITING_FOR_CONTINUITY':
        st.warning(f"主策略交易績效待銜接 · 結算 {status['through']} · 研究 {status['requested']}")
    if market=="TW":
        st.warning("台股 2016–2026 起跑組為研究版。現有封存驗收沒有確認符合公告時點、財報版本及成交證據的可信起始年。")
    else:
        st.info("2006 起跑可同時參考發布時點延後情境。早期金融條件採信用利差代理，存在發布時點限制。")
        with st.expander("2006 發布時點敏感度"):
            table([{**row,"cumulative":percent(row["cumulative"]),"cagr":percent(row["cagr"]),"max_drawdown":percent(row["max_drawdown"])} for row in research["usDelay"]], label="2006 發布時點敏感度")
    table([{"起始年":r["start_year"],"A 累積":percent(r["A_cumulative"]),"B 累積":percent(r["B_cumulative"]),
        "A CAGR":percent(r["A_cagr"]),"B CAGR":percent(r["B_cagr"]),"A 最大回撤":percent(r["A_max_drawdown"]),
        "B 最大回撤":percent(r["B_max_drawdown"]),"B 額外買入":r["B_extras"]} for r in rows], label="年度回測比較")
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
            "資料判讀":r["coverage_note"]} for r in old[key]], label="歷史研究比較")
        table([{"代號":r["symbol"],"訊號日":r["signalDate"],"成交日":r["exitDate"],
            "淨報酬":percent(r["returnNet"]),"出場原因":r["reason"],"總經版本":r["macroVintage"]}
            for r in old["exitExamples"] if r["market"]==market], label="逐筆出場")
        if market=="US":
            benchmark=pd.read_csv(BytesIO(report_bytes("us-primary-vs-qqq-2006-2026.csv")))
            st.subheader("美股主策略與 QQQ")
            trend_chart(benchmark.rename(columns={"year": "起始年", "total_return": "主策略", "qqq_return": "QQQ"}),
                        "起始年", ["主策略", "QQQ"], ylabel="累積報酬", value_format=".0%", year_axis=True)
            st.caption("各點為不同年度獨立起跑至共同終點的累積報酬。")
            report_file("主策略與 QQQ 比較 CSV","us-primary-vs-qqq-2006-2026.csv")
            report_file("資金紅燈全清比較 CSV","funding-exit-comparison-2016-2026.csv")
            report_file("資金紅燈出場事件 CSV","funding-exit-events-2016-2026.csv")
        for label,name in [("完整互動回測報告 HTML","growth-backtest-2016-2026.html"),
            ("歷年比較 CSV","comparison-summary-2016-2026.csv"),("逐筆出場稽核 CSV","exit-audits-2016-2026.csv"),
            ("出場案例證據 JSON","exit-examples-evidence.json"),("舊報告對照 CSV","prior-run-reconciliation.csv")]:
            report_file(label,name)

def tracking(snapshot_data, baseline):
    with st.container(horizontal=True, gap="small", vertical_alignment="bottom"):
        market = market_picker(st)
        year=st.selectbox("策略起始年", sorted({p["year"] for p in baseline[market]},reverse=True),key=f"mark-{market}", width=152)
    block = snapshot_data["markets"][market]
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
                "靜態市值":number(marked.get(x["symbol"]))} for x in p["positions"]], label=f"{p['variant']} 版模擬部位")
    table(plans, label="策略估值")
    csv_button("匯出策略估值 CSV",plans,f"{market}-{year}-tracking.csv",f"track-{market}")
    st.caption(f"最近訊號計算 {stamp(block['signalAt'])} · {('USD' if market=='US' else 'TWD')}")

@st.cache_resource
def performance_reader():
    return PerformanceReader(environ.get("PERFORMANCE_CACHE_PATH", "/tmp/growth-bank-performance/release.json"))


@st.cache_data(ttl=90, show_spinner=False)
def advisor_performance():
    live, error = performance_reader().refresh(lambda: fetch_published('etf/release.json'))
    if live:
        return live, error, "GitHub 正式績效"
    book = environ.get("PERFORMANCE_SHEETS_ID")
    try:
        account = st.secrets.get("performance_google_service_account")
    except FileNotFoundError:
        account = None
    if book and account:
        data, warning = performance_reader().refresh(lambda: fetch_performance(book, account))
        if data:
            return data, warning, "Google Sheets"
    else:
        warning = error
    try:
        rows = json.loads((BASE / "data/etf-verified-release.json").read_text())
        return decode_performance(rows), warning, "已驗證績效批次"
    except (OSError, ValueError, TypeError, KeyError):
        return None, warning or "ETF 績效批次讀取失敗，顯示原封存。", None


def advisor(research, research_close=None):
    current, performance_error, performance_source = advisor_performance()
    research = overlay_performance(research, current)
    products=sorted({x["symbol"] for x in research["advisor"]})
    with st.container(horizontal=True, gap="small", vertical_alignment="bottom"):
        product=st.selectbox("研究商品",products,index=products.index("QQQ"),width=132)
        candidates=[r for r in research["advisor"] if r["symbol"]==product]
        years=sorted({r["start_year"] for r in candidates})
        cohort=st.selectbox("投資起始年",years,key=f"advisor-year-{product}",width=138)
    selected=[r for r in candidates if r["start_year"]==cohort]
    comparison = pd.DataFrame(selected).rename(columns={
        "variant": "方案", "cumulative": "累積報酬", "cagr": "CAGR", "mdd": "最大回撤"
    })
    st.subheader("A／B／C／D 績效比較")
    strategy_comparison(comparison, {code: f"{code} {name}" for code, name in PLANS.items()})
    cutoff = selected[0].get("through", "2026-11-04")
    if cutoff == "2026-11-04":
        st.caption("顧問績效封存至 2026-11-04。早期信用利差代理與發布時點存在限制。共同基金採可得淨值。")
    else:
        st.caption(f"ETF 績效截止 {cutoff} · {performance_source} · {current['batch']} · 更新 {stamp(current['calculated_at'])}")
        age = (datetime.now(ZoneInfo("America/New_York")).date() - datetime.fromisoformat(cutoff).date()).days
        if age > 4 or (research_close and cutoff < research_close):
            st.warning("STALE_DATA｜ETF 績效日期落後，顯示最後驗證批次。")
    if cutoff != "2026-11-04":
        status=performance_status().get('ETF',{})
        if status.get('status') in ('PARTIAL_DATA','SOURCE_ERROR','VALIDATION_FAILED'):
            st.warning(f"{status['status']}｜ETF 更新未通過，顯示最後驗證績效。")
    if performance_error:
        st.warning(performance_error)
    with st.expander("各策略明細"):
        table([{"方案":r["variant"]+" "+PLANS[r["variant"]],"累積":percent(r["cumulative"]),"CAGR":percent(r["cagr"]),
            "最大回撤":percent(r["mdd"]),"最差年度":r["worst_year"],"最差年報酬":percent(r["worst_year_return"]),
            "高點至回本（天）":r["peak_to_recovery_calendar_days"],"成本／初始資金":percent(r["transaction_cost_initial_fraction"]),
            "買入次數":r["buy_count"],"賣出次數":r["sell_count"]} for r in selected], label="各策略明細")
    st.subheader("事件回撤")
    event = st.segmented_control("事件回撤比較", list(WINDOWS), format_func=WINDOWS.get,
                                 default="GFC", key="advisor-event") or "GFC"
    events=[r for r in research["windows"] if r["symbol"]==product and r["start_year"]==cohort and str(r["window"])==str(event)]
    if events:
        event_drawdown_chart(pd.DataFrame(events), "variant", "window_mdd", WINDOWS[event])
        table([{"方案":r["variant"]+" "+PLANS[r["variant"]],"起日":r["first"],"迄日":r["last"],
            "部分區間":r["partial"],"事件報酬":percent(r["strategy_return"]),"最大回撤":percent(r["window_mdd"]),
            "紅燈交易日":r["red_sessions"]} for r in events], label="ETF／基金事件回撤")
    else:
        st.caption("此起跑年度在該事件沒有研究區間。")
    st.subheader("新台幣 1,000 萬元模擬組合")
    st.caption("2006 年起投，SPY 40%、QQQ 20%、AGG 30%、IEF 10%，計入匯率。")
    options=list(dict.fromkeys(r["window"] for r in research["example"]))
    example=st.selectbox("台幣組合市場事件",options,format_func=lambda x: WINDOWS.get(str(x),str(x)))
    example_rows = [r for r in research["example"] if r["window"] == example]
    if example_rows:
        event_drawdown_chart(pd.DataFrame(example_rows), "variant", "drawdown",
                             WINDOWS.get(str(example), str(example)), value_label="模擬組合最大回撤")
    table([{"方案":r["variant"]+" "+PLANS[r["variant"]],"高點日":r["peak_date"],"高點淨值":number(r["peak_twd"]),
        "低點日":r["trough_date"],"低點淨值":number(r["trough_twd"]),"虧損":number(r["loss_twd"]),
        "回撤":percent(r["drawdown"]),"同額虧損":number(-r["drawdown"]*10_000_000),"回本日":r["recovery_date"],
        "低點至回本（日）":r["days_trough_to_recovery"]} for r in example_rows], label="台幣模擬組合事件回撤")
    for label,name in [("各商品／年度完整 CSV","advisor-by-year.csv"),("事件比較 CSV","advisor-events.csv"),
        ("事件模擬成交與成本 CSV","advisor-event-fills.csv"),("台幣組合事件 CSV","advisor-twd-events.csv")]:
        report_file(label,name)

@st.cache_data(ttl=90, show_spinner=False)
def runner_attempts():
    base = "https://raw.githubusercontent.com/monstervincentw-glitch/growth-observatory-bank/research-data/"
    try:
        try:
            with urlopen(base + "attempts.json", timeout=5) as response:
                attempts = json.loads(response.read(262_145))
        except HTTPError as error:
            if error.code != 404:
                raise
            with urlopen(base + "attempt.json", timeout=5) as response:
                attempt = json.loads(response.read(262_145))
            attempts = {attempt["market"]: attempt}
        for market, record in attempts.items():
            if market not in ("US", "TW") or record["market"] != market or record["status"] not in ("published", "same_close", "failed"):
                raise ValueError("更新紀錄格式錯誤")
            if datetime.fromisoformat(record["at"]).tzinfo is None:
                raise ValueError("更新紀錄時間缺少時區")
        return attempts, None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}, "更新紀錄無法驗證"


def update_status(state):
    for market, info in state["markets"].items():
        label = "美股" if market == "US" else "台股"
        count = lambda value: "—" if value is None else value
        st.caption(f"{label}｜預期 {info['expected']} · 成功 {count(info['succeeded'])} · 失敗 {count(info['failed'])}"
                   f" · 未處理 {count(info['notAttempted'])}｜最後成功收盤 {info['date'] or '待核對'}"
                   f" · 資料時間 {stamp(info['lastSuccessAt'])}")
        if info["reason"]:
            st.warning(f"{label}資料異常｜{info['reason']}｜正式買入訊號暫停")
            if info["affected"]:
                st.write("受影響股票｜" + "、".join(info["affected"]))
            elif info["marketWide"]:
                st.write(f"受影響範圍｜{label}全市場 {info['expected']} 檔")
            if info["errors"]:
                with st.expander(f"{label}資料錯誤明細"):
                    table([{"標的":e.get("symbol") or "市場","資料類型":e.get("type"),"錯誤":str(e.get("code")),
                            "來源":e.get("provider"),"時間":stamp(e.get("at")),"更新識別":e.get("runId"),
                            "研究批次":e.get("batch")} for e in info["errors"]], label=f"{label}資料錯誤")


def current_update_state():
    attempts, issue = runner_attempts()
    entry = {"snapshot": data, "batch": batch, "checksum": research_state["checksum"]} if data else None
    return health(entry, research_state["source"], research_state["issue"] or issue, attempts=attempts)


def clear_research_caches():
    advisor_performance.clear()
    performance_status.clear()
    primary_performance.clear()
    snapshot.clear()
    runner_attempts.clear()


def records(data, source, batch):
    st.write(f"**來源** {source}　**批次** {batch or '封存備援'}")
    st.write(f"**同步驗收標記** {data['qa']}")
    update_status(current_update_state())
    attempts, _ = runner_attempts()
    for attempt in attempts.values():
        result = {"same_close":"同一收盤，保留快照","failed":"來源更新失敗","published":"研究批次已發布"}[attempt["status"]]
        st.write(f"**最近雲端收盤工作** {attempt['market']} · {result} · {stamp(attempt.get('at'))}")
        if attempt["status"] == "failed":
            st.warning("最近一次更新未通過。頁面沿用已核對快照。")
    for label,market in MARKETS.items():
        block=data["markets"][market]
        macro=block["macro"]
        st.write(f"**{label}**　收盤 {macro['marketAsOf']} · 發布 {stamp(macro.get('lastMarketPublishedAt'))} · {macro['ruleVersion']}")
    table([{"市場":j["market"],"狀態":j["status"],"處理數":j.get("updated"),"失敗數":j.get("failed"),
        "沿用數":j.get("retained"),"備援數":j.get("fallback"),"更新時間":stamp(j.get("updated_at"))} for j in data["jobs"]], label="收盤工作紀錄")
    st.markdown("""### 策略規則
- **主策略 A** 最多十檔核心。**B** 在核心補滿後，從已通過原始進場條件的候選中加買，分數須嚴格超過當日核心最高分。每筆以當時淨值 10% 為目標，額外部位與核心共用出場規則。
- **基本面門檻** TTM 營收年增至少 10%、最新單季營收不年減、營益率和營業現金流率為正。依原版納入財報時效與相較前次財報惡化條件。
- **候選排名** 美股依四項基本面 75% 與同產業 P/S 25% 評分。台股依營收年增 50%、營益率 25%、營業現金流率 25% 評分。單檔明細顯示百分位、權重與貢獻分。
- **背離長抱** 保留原版技術背離與資金條件。研究排名、正式訊號與封存後靜態估值分開呈現。
""")
    report_file("下載研究驗收紀錄","phase1-acceptance.md")


def market_page():
    market = market_picker(market_controls, label_visibility="collapsed")
    state = current_update_state()
    update_status(state)
    if data is None:
        return
    block = data["markets"][market]
    markets(market, block, state["markets"][market]["usable"], state["markets"][market]["reason"])


def advisor_page():
    with st.container(horizontal=True, gap="small"):
        advisor_page = st.segmented_control("顧問風控研究", ["原 14 項 ETF／基金／債券", "台灣可申購共同基金"],
                                            default="原 14 項 ETF／基金／債券", label_visibility="collapsed",
                                            key="bank-advisor-page")
        if advisor_page == "台灣可申購共同基金":
            fund_view = st.segmented_control("基金研究功能", ["基金查詢", "同期間排名"], default="基金查詢",
                                             label_visibility="collapsed", key="tw-fund-mode")
    if advisor_page=="台灣可申購共同基金":
        render_taiwan_fund_research(fund_view)
    else:
        advisor(research, data["markets"]["US"]["macro"]["marketAsOf"] if data else None)


layout_style()
sidebar_brand()
data, research_state = snapshot()
batch = research_state["batch"]
source = {"sheets":"Google Sheets", "last_verified":"Sheets 上次成功快取"}.get(research_state["source"], "研究資料待核對")
research, baseline, nav = load_archive()
page = st.navigation([
    st.Page(market_page, title="市場觀測", icon=":material/monitoring:", default=True),
    st.Page(lambda: backtest(research, nav), title="主策略回測", icon=":material/timeline:", url_path="backtest"),
    st.Page(lambda: tracking(data, baseline) if data else st.warning("研究快照待核對，靜態估值暫停。"), title="策略模擬", icon=":material/stacked_line_chart:", url_path="simulation"),
    st.Page(advisor_page, title="顧問風控", icon=":material/shield:", url_path="advisor"),
    st.Page(lambda: records(data, source, batch) if data else st.warning(research_state["issue"] or "研究快照待核對。"), title="更新紀錄與規則", icon=":material/history:", url_path="updates"),
], expanded=True)

with st.container(key="bank-content"):
    header(page.title)
    if page.title == "市場觀測":
        status, market_controls, refresh = st.columns([5, 1, 1.2], vertical_alignment="center")
    else:
        status, refresh = st.columns([5, 1], vertical_alignment="center")
    with status:
        if data:
            status_line(source, data["markets"]["US"]["macro"]["marketAsOf"],
                        data["markets"]["TW"]["macro"]["marketAsOf"], data["historyThrough"])
            st.caption(f"研究批次 {batch}")
        else:
            st.caption("公開研究日期待核對，歷史回測仍可查看。")
    refresh.button("重新讀取資料", on_click=clear_research_caches)
    page.run()
