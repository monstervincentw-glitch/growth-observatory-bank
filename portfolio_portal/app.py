"""Private owner-only investment dashboard (Streamlit + Google OIDC + Sheets).

Deploy this file as an independent Streamlit app from a PRIVATE GitHub repo.
It never places portfolio data or credentials in the public bank repository.
"""
from __future__ import annotations
from datetime import date, timedelta
from decimal import Decimal
import json

import pandas as pd
import streamlit as st

from core import buy_plan, compute_account, portfolio_value
from monitor import portfolio_reviews, review_holding, data_too_old
from research import latest_research
from storage import PortfolioSheets, sheets_client

st.set_page_config(page_title="我的投資組合｜私人版", page_icon="🔐",
                   layout="wide", initial_sidebar_state="expanded")


def auth_gate():
    st.title("🔐 我的投資組合")
    if not st.user.is_logged_in:
        st.caption("此網站需要你的 Google 帳號登入。未登入者無法讀取任何持股資料。")
        if st.button("使用 Google 帳號登入", type="primary"):
            st.login()
        st.stop()
    allowed = str(st.secrets.get("owner_email", "")).strip().casefold()
    email = str(st.user.get("email", "")).strip().casefold()
    verified = st.user.get("email_verified", False)
    if not allowed or not email or email != allowed or verified is not True:
        st.error("已登入，但不是經授權且已驗證的擁有者。")
        if st.button("登出"):
            st.logout()
        st.stop()
    return email


owner_email = auth_gate()


@st.cache_resource
def private_store():
    private_id = st.secrets.get("private_portfolio_sheet_id", "")
    info = dict(st.secrets["google_service_account"])
    return PortfolioSheets(sheets_client(info), private_id)


@st.cache_data(ttl=180, show_spinner=False)
def research_snapshot():
    client = sheets_client(dict(st.secrets["google_service_account"]))
    return latest_research(client)


with st.sidebar:
    st.caption("只有經授權的 Google 帳號能存取")
    st.write(f"**{owner_email}**")
    if st.button("登出"):
        st.logout()
    if st.button("重新讀取最新研究"):
        research_snapshot.clear()
        st.rerun()

try:
    store = private_store()
except Exception as exc:
    st.error("私人 Google Sheets 尚未完成授權或設定。")
    st.caption("請先依 README 設定 owner_email、private_portfolio_sheet_id、"
               "google_service_account 及 Google OIDC，並將工作表分享給服務帳號。")
    st.exception(exc)
    st.stop()

try:
    entry, research_source, research_issues = research_snapshot()
except Exception as exc:
    st.error("銀行版研究資料無法讀取或驗證；不會依過期行情產生新交易建議。")
    st.exception(exc)
    st.stop()

snapshot = entry["snapshot"]
with st.sidebar:
    st.caption(f"資料來源：{research_source}")
    st.caption(f"研究批次：{entry['batch']}")
    for m, name in (("US","美股"),("TW","台股")):
        st.caption(f"{name}：{snapshot['markets'][m]['macro']['marketAsOf']}")
for message in research_issues:
    st.warning(message)

try:
    portfolios = store.portfolios()
    transactions = store.transactions()
except Exception as exc:
    st.error("讀取私人交易紀錄失敗，禁止建立建議或寫入新資料。")
    st.exception(exc)
    st.stop()

# Review all real holdings first, before offering any new purchase candidates.
try:
    live_reviews=portfolio_reviews(portfolios,transactions,snapshot,entry["batch"])
    event_history=store.monitor_history()
except Exception as exc:
    st.error("持股監控或歷史交易資料驗證失敗，已停止提供交易建議。")
    st.exception(exc)
    st.stop()
urgent=[r for r in live_reviews if r["status"]=="觸發出場條件"]
pending=[r for r in live_reviews if r["status"]=="資料待確認"]
st.subheader("今日持股監控")
summary_cols=st.columns(3)
summary_cols[0].metric("觸發出場條件",len(urgent))
summary_cols[1].metric("資料待確認",len(pending))
summary_cols[2].metric("目前監控持股",len(live_reviews))
if urgent or pending:
    st.dataframe([{"組合":next((p["name"] for p in portfolios if p["id"]==r["portfolio_id"]),""),
                   "市場":r["market"],"股票":r["symbol"],"狀態":r["status"],
                   "原因":r["reasons"] or r["unknown"],"行情日":r["signal_date"]}
                  for r in urgent+pending],hide_index=True,use_container_width=True)
elif live_reviews:
    st.caption("目前未新增正式出場警示；其他持股仍須定期檢查。")
with st.expander("監控事件歷史與存檔"):
    st.caption("Google Sheets 僅保存第一次觸發或狀態變化；排程部署後可自動記錄。")
    if st.button("記錄本次監控變化至私人 Google Sheets"):
        try:
            recorded=store.append_monitor_results(live_reviews)
            st.success(f"已儲存 {len(recorded)} 筆新增／變更事件")
            st.rerun()
        except Exception as exc:
            st.error("寫入監控紀錄失敗："+str(exc))
    if event_history:
        st.dataframe(event_history[::-1][:100],hide_index=True,use_container_width=True)
    else:
        st.caption("尚無已儲存的監控事件。")

st.subheader("我的投資組合")
with st.expander("新增投資組合", expanded=not bool(portfolios)):
    with st.form("create-portfolio"):
        name = st.text_input("名稱", placeholder="美股主策略 A")
        c1,c2 = st.columns(2)
        market = c1.selectbox("市場", ["US","TW"], format_func=lambda x:"美股" if x=="US" else "台股")
        strategy = c2.selectbox("策略", ["A","B","背離長抱","自訂"])
        note = st.text_input("備註（可空白）")
        if st.form_submit_button("建立組合", type="primary"):
            try:
                item = store.create_portfolio(name=name, market=market, strategy=strategy,
                                               currency="USD" if market=="US" else "TWD", note=note)
                st.success("已儲存至私人 Google Sheets："+item["name"])
                st.rerun()
            except Exception as exc:
                st.error("建立失敗："+str(exc))

if not portfolios:
    st.info("尚無投資組合。先建立一個組合，再投入資金及登錄交易。")
    st.stop()

labels = {p["id"]: f"{p['name']} · {p['market']} · {p['strategy']}" for p in portfolios}
selected_id = st.selectbox("目前組合", list(labels), format_func=lambda pid:labels[pid])
portfolio = next(p for p in portfolios if p["id"]==selected_id)
block = snapshot["markets"][portfolio["market"]]
try:
    account = compute_account(transactions, selected_id)
    valued = portfolio_value(account, block)
except Exception as exc:
    st.error("組合交易帳本無法對帳，為避免錯誤買賣訊號已停止分析："+str(exc))
    st.stop()

cols=st.columns(4)
cols[0].metric("現金餘額", f"{valued['cash']:,.2f} {portfolio['currency']}")
cols[1].metric("持股數", len(valued["holdings"]))
cols[2].metric("組合估值", "無法完整估值" if valued["nav"] is None else
               f"{valued['nav']:,.2f} {portfolio['currency']}")
cols[3].metric("累計已實現損益", f"{valued['realized']:,.2f}")

# Maintain original private site's closing NAV chart from actual snapshots.
# Never manufacture missing trading-day values or compute historical NAV from
# the latest prices. Historical points must be owner-confirmed or verified close.
try:
    nav_rows=store.nav_snapshots(selected_id)
except Exception as exc:
    st.warning("無法讀取帳戶淨值歷史；本次不顯示推估走勢："+str(exc))
    nav_rows=[]
with st.container(border=True):
    heading,selector=st.columns([3,1],vertical_alignment="center")
    heading.subheader(f"帳戶淨值走勢 · {portfolio['currency']}")
    period=selector.selectbox("區間",["近 30 天","近 90 天","近 1 年","全部"],index=1)
    if nav_rows:
        valid=[r for r in nav_rows if r.get("date") and r.get("nav") not in (None,"")]
        if valid:
            last_date=max(date.fromisoformat(r["date"]) for r in valid)
            days={"近 30 天":30,"近 90 天":90,"近 1 年":365}.get(period)
            if days is not None:
                valid=[r for r in valid if date.fromisoformat(r["date"])>=last_date-timedelta(days=days)]
        if valid:
            curve=pd.DataFrame([
                {"date":pd.to_datetime(r["date"]), "nav":float(r["nav"])}
                for r in valid
            ]).sort_values("date")
            latest=valid[-1]
            st.metric("最新已記錄的收盤淨值",f"{float(latest['nav']):,.2f} {portfolio['currency']}")
            st.caption("最近有效收盤："+latest["date"]+" · 歷史數據不進行缺日補值")
            st.line_chart(curve,x="date",y="nav",height=230,use_container_width=True)
            if latest.get("twr"):
                st.caption("累計 TWR："+f"{float(latest['twr']):+.2%}"
                           +"（依原網站已確認的淨值基準與計算方式）")
            with st.expander("最近完整收盤紀錄"):
                st.dataframe([
                    {"日期":r["date"],"淨值":r["nav"],
                     "現金":r["cash"] or "未記錄","持股市值":r["equity_value"] or "未記錄",
                     "來源":r["source"]}
                    for r in valid[-30:]
                ],hide_index=True,use_container_width=True)
        else:
            st.info("這個區間沒有已核對的歷史淨值。")
    else:
        st.info("尚未建立歷史淨值紀錄；等到有實際收盤紀錄才會顯示走勢圖。")

if valued["missing_prices"]:
    st.warning("以下持股未取得可核對的收盤價，組合淨值及買入額度不予計算："+
               "、".join(valued["missing_prices"]))
st.caption(f"研究行情日 {block['macro']['marketAsOf']} · "
           f"全球資金判定 {block['macro'].get('decision', 'unknown')} · "
           "所有買入/賣出均為研究輔助，不會自動下單。")

tab_hold,tab_plan,tab_trades,tab_market=st.tabs([
    "持股與出場監控","A／B 現金與買入","交易與現金","買入候選及研究"])

with tab_hold:
    with st.expander("原私人站持股市值與損益｜已核對的歷史收盤紀錄"):
        historical=store.historical_positions(selected_id)
        if historical:
            last_day=max(row["date"] for row in historical)
            last=[row for row in historical if row["date"]==last_day]
            st.caption(f"基準日期 {last_day} · 截圖封存紀錄，不代表最新即時行情")
            st.dataframe([
                {"代號":r["symbol"],"公司":r["name"],"股數":r["quantity"],
                 "平均買入成本":r["avg_cost"],"當日持股市值":r["market_value"],
                 "當日未實現損益":r["unrealized_pnl"]}
                for r in last
            ],hide_index=True,use_container_width=True)
        else:
            st.caption("沒有保存的歷史個股快照。")
    if not valued["holdings"]:
        st.info("目前尚未登錄持股；可以先至「交易與現金」補登。")
    for holding in valued["holdings"]:
        with st.expander(f"{holding['symbol']}｜{holding['quantity']} 股｜"
                         f"未實現 {holding['pnl']:,.2f}", expanded=True):
            st.write(f"收盤價 {holding['price']} · 市值 {holding['value']:,.2f} · "
                     f"持有成本 {holding['cost']:,.2f}")
            if portfolio["strategy"] in ("A","B"):
                review = review_holding(holding,portfolio["strategy"],block["macro"])
                if data_too_old(block["macro"]["marketAsOf"]):
                    review={"status":"資料待確認","reasons":[],
                            "unknown":["研究行情日過期，暫停買賣判斷"],
                            "financial_period":None,"observed_at":None,
                            "asof":block["macro"]["marketAsOf"]}
                st.write(f"**{review['status']}**")
                for reason in review["reasons"]:
                    st.error("觸發："+reason)
                for reason in review["unknown"]:
                    st.warning("待確認："+reason)
                st.caption(f"最新財報期 {review['financial_period']} · "
                           f"首次取得 {review['observed_at']} · "
                           f"行情日 {review['asof']}")
            else:
                review=review_holding(holding,portfolio["strategy"],block["macro"])
                st.info("非 A／B 策略｜"+review["status"]+"；不套用主策略強制賣出規則。")
                for reason in review["reasons"]+review["unknown"]:
                    st.caption("• "+reason)
            with st.expander("各筆進場基準"):
                st.dataframe([{"數量":str(l["quantity"]),"成本/股":str(l["unit_cost"]),
                              "類別":l["role"],"登錄時間":l["at"],
                              "進場營收成長率":l["entry_growth"] or "未提供",
                              "進場營益率":l["entry_margin"] or "未提供"}
                              for l in holding["lots"]],hide_index=True)

with tab_plan:
    if portfolio["strategy"] not in ("A","B"):
        st.info("A／B 分析僅適用主策略投資組合；本組合可查看市場候選，但不套用主策略加碼。")
    else:
        for variant in ("A","B"):
            if data_too_old(block["macro"]["marketAsOf"]):
                result={"status":"市場研究資料已過期，暫停新增買入",
                        "candidates":[],"mode":None}
            else:
                result = buy_plan(account, valued, block, variant)
            st.subheader(f"主策略 {variant}｜{result['status']}")
            if result.get("threshold") is not None:
                st.caption(f"B 額外加碼門檻：新候選分數必須嚴格高於 {result['threshold']}")
            if result["candidates"]:
                st.dataframe(result["candidates"],hide_index=True,use_container_width=True)
                st.caption("金額為當時淨值的 10% 目標值，未計入隔日實際成交滑價。"
                           "下單前需另外確認可用現金及交易成本。")
            else:
                st.caption("本方案此次不新增。")
        st.caption("組合選擇 A 或 B 不會被自動改變；僅供你比較。")

with tab_trades:
    st.subheader("新增交易／存提資金")
    with st.form("record-transaction"):
        action = st.selectbox("動作", ["DEPOSIT","BUY","SELL","WITHDRAW"],
                              format_func=lambda x:{"DEPOSIT":"存入現金","BUY":"買入",
                                                   "SELL":"賣出","WITHDRAW":"提領現金"}[x])
        tdate = st.date_input("實際交易日期",value=date.today(),max_value=date.today())
        symbol = st.text_input("股票代號（現金異動可留空）").upper().strip()
        c1,c2,c3=st.columns(3)
        qty=c1.text_input("股數（買／賣）",value="")
        price=c2.text_input("成交價（買／賣）",value="")
        fee=c3.text_input("費用",value="0")
        cash_amount = st.text_input("現金異動金額（存／提）",value="")
        role=st.selectbox("持股類別", ["core","extra"] if portfolio["strategy"]=="B" else ["core"],
                          format_func=lambda x:"核心" if x=="core" else "額外")
        st.caption("歷史補登的進場財報必須是當時實際可得的數值，請手動填寫；"
                   "不可使用今天的財報冒充歷史買進基準。")
        c4,c5=st.columns(2)
        entry_growth=c4.text_input("進場時 TTM 營收年增率（小數，例如 0.25）",value="")
        entry_margin=c5.text_input("進場時營益率（小數，例如 0.15）",value="")
        entry_period=st.text_input("進場當時已取得的財報季度（選填）",value="")
        note=st.text_input("交易備註",value="")
        if st.form_submit_button("確認並寫入私人 Google Sheets",type="primary"):
            try:
                store.append_transaction(
                    portfolio_id=selected_id, action=action, trade_date=tdate.isoformat(),
                    symbol=symbol,quantity=qty,price=price,fee=fee,
                    cash_amount=cash_amount,role=role,
                    entry_growth=entry_growth,entry_margin=entry_margin,
                    entry_filing_period=entry_period,note=note)
                st.success("已寫入並驗證交易流水帳")
                st.rerun()
            except Exception as exc:
                st.error("未完成寫入："+str(exc))
    st.subheader("交易紀錄")
    history=[r for r in transactions if r["portfolio_id"]==selected_id]
    st.dataframe(history[::-1],hide_index=True,use_container_width=True)

with tab_market:
    st.subheader("最新市場買入候選")
    candidates=sorted((s for s in block["stocks"] if s.get("researchRank")),
                      key=lambda s:s["researchRank"]["rank"])
    st.caption("此處為股票研究排名；不是已依你的帳戶現金與持股核准的實際買入單。")
    rows=[{"排名":s["researchRank"]["rank"],"代號":s["symbol"],
           "名稱":s.get("name"),"分數":round(s["researchRank"]["score"],2),
           "行情日":s["priceDate"],"營收年增":s["financial"].get("revenue_growth"),
           "主策略進場條件":"符合個股條件" if s["evaluation"]["fundamental"]
           and s["evaluation"]["tradable"] else "未達條件",
           "技術背離":"是" if s["evaluation"]["divergence"] else "否"}
          for s in candidates]
    st.dataframe(rows,hide_index=True,use_container_width=True)
    st.caption("價格及財報來自銀行版發布的研究資料；私人持股及交易紀錄只在私人 Sheet。")
