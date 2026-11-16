"""Public, read-only demo using entirely fictional portfolios.

Never connects to the owner's Google Sheets or any personal backend.
"""
from __future__ import annotations
from decimal import Decimal
from datetime import date
import streamlit as st
from core import compute_account, portfolio_value, buy_plan, exit_review

st.set_page_config(page_title="私人投資組合中心｜介面預覽",page_icon="📊",layout="wide")
st.title("私人投資組合中心")
st.info("🔎 這是**介面預覽版**：全部都是虛構股票與模擬持股。**沒有登入、沒有讀取或保存任何真實個人持股，也不提供交易建議。**")

@st.cache_data
def demo():
    shares=[]
    for i in range(10):
        shares.append({
            "symbol":f"CORE{i+1:02d}","name":f"示範核心股票 {i+1:02d}",
            "integrity":True,"sourceError":None,"marketEvent":None,
            "close":100+i*3,"priceDate":"2026-11-13",
            "researchRank":{"rank":i+1,"score":round(87-i*2,1)},
            "financial":{"financial_status":"ok","revenue_growth":.22-i*.005,
                         "quarterly_growth":.17,"operating_margin":.21,
                         "cfo_margin":.15,"latest_period":"2026-Q3"},
            "evaluation":{"fundamental":True,"tradable":True,"fresh":True,
                          "divergence":False,"fundamentalReasons":[]}})
    for sym,score,rank in [("GROWTH-X",94,11),("GROWTH-Y",91,12),("GROWTH-Z",83,13)]:
        shares.append({"symbol":sym,"name":"虛構新增候選 "+sym,
            "integrity":True,"sourceError":None,"marketEvent":None,
            "close":120,"priceDate":"2026-11-13",
            "researchRank":{"rank":rank,"score":score},
            "financial":{"financial_status":"ok","revenue_growth":.25,
                         "quarterly_growth":.18,"operating_margin":.22,
                         "cfo_margin":.19,"latest_period":"2026-Q3"},
            "evaluation":{"fundamental":True,"tradable":True,"fresh":True,
                          "divergence":False,"fundamentalReasons":[]}})
    return shares

stocks=demo()
st.sidebar.header("預覽控制")
st.sidebar.caption("調整後立即顯示 A／B 比較；不會儲存。")
core_count=st.sidebar.slider("核心持股數",0,10,10)
cash=st.sidebar.slider("模擬可用現金 (美元)",0,500000,150000,10000)
strategy=st.sidebar.radio("目前組合策略",["A","B"],horizontal=True)
funding=st.sidebar.selectbox("模擬全球資金狀態",["non_red","red","unknown"],
    format_func=lambda x:{"non_red":"非紅燈","red":"紅燈","unknown":"未知"}[x])
worse=st.sidebar.checkbox("模擬一檔核心基本面轉弱",value=True)
st.sidebar.divider()
st.sidebar.caption("正式版將支援 Google 登入、私人 Google Sheets、各組合交易紀錄及自動監控。")

rows=[{
    "id":"deposit","portfolio_id":"demo","at":"2026-11-13T10:00:00+00:00",
    "trade_date":"2026-11-13","action":"DEPOSIT","cash_amount":str(cash+core_count*1000)}
]
for i in range(core_count):
    rows.append({"id":f"buy{i}","portfolio_id":"demo","at":f"2026-11-13T10:00:{i+1:02d}+00:00",
        "trade_date":"2026-11-13","action":"BUY","symbol":f"CORE{i+1:02d}",
        "quantity":"10","price":"100","fee":"0","role":"core",
        "entry_growth":".32" if worse and i==0 else ".23",
        "entry_margin":".29" if worse and i==0 else ".21"})
if worse and core_count:
    stocks[0]["financial"]["revenue_growth"]=.18
    stocks[0]["evaluation"]["fundamentalReasons"]=["營收成長率較前次財報下降 10 個百分點（示範）"]
macro={"decision":funding,"marketAsOf":"2026-11-13"}
block={"stocks":stocks,"macro":macro}
account=compute_account(rows,"demo")
valued=portfolio_value(account,block)
col1,col2,col3,col4=st.columns(4)
col1.metric("模擬資產淨值",f"US$ {valued['nav']:,.0f}")
col2.metric("模擬可用現金",f"US$ {account['cash']:,.0f}")
col3.metric("核心持股",f"{core_count} / 10")
col4.metric("策略",strategy)

all_reviews=[exit_review(h,macro) for h in valued["holdings"]]
risk=[r for r in all_reviews if r["status"]=="觸發出場條件"]
unknown=[r for r in all_reviews if r["status"]=="資料待確認"]
st.subheader("今日持股風險總覽")
c1,c2,c3=st.columns(3)
c1.metric("觸發出場",len(risk))
c2.metric("資料待確認",len(unknown))
c3.metric("未觸發",len(all_reviews)-len(risk)-len(unknown))
if risk:
    for item in risk:
        st.error(f"**{item['symbol']}｜觸發出場條件（示範）**　"+"、".join(item["reasons"]))
elif not unknown:
    st.success("示範組合未觸發原規則出場。")
if unknown:
    st.warning("資金或財報資料待確認；正式版不會把未知當作安全。")

tab1,tab2,tab3,tab4=st.tabs(["我的組合","A／B 現金配置","最新買入候選","持股出場分析"])
with tab1:
    st.subheader("虛構組合｜核心股票")
    st.dataframe([{"股票":h["symbol"],"數量":str(h["quantity"]),
                   "模擬成本":f"{h['cost']:,.0f}","模擬市值":f"{h['value']:,.0f}",
                   "未實現損益":f"{h['pnl']:+,.0f}"}
                  for h in valued["holdings"]],hide_index=True,use_container_width=True)
    st.caption("正式版可建立多組台美股投資組合，連接你自己 Google Sheets 的持股、交易、成本和現金。")
with tab2:
    st.subheader("同一個模擬組合的 A／B 選擇")
    a=buy_plan(account,valued,block,"A")
    b=buy_plan(account,valued,block,"B")
    ca,cb=st.columns(2)
    for col,variant,result in [(ca,"A",a),(cb,"B",b)]:
        with col:
            st.markdown(f"### 主策略 {variant}")
            st.write(result["status"])
            if result.get("threshold") is not None:
                st.caption(f"高分加碼門檻：> {result['threshold']}")
            if result["candidates"]:
                st.dataframe([{"股票":r["symbol"],"分數":r["score"],
                               "示範目標額度":f"{r['target']:,.0f}"} for r in result["candidates"]],
                             hide_index=True,use_container_width=True)
            else:
                st.caption("沒有符合條件的買入候選")
    st.caption("試算僅展示規則，尚未包含實際交易滑價、成本與即時股價確認。")
with tab3:
    st.subheader("銀行版研究候選功能的預覽")
    st.dataframe([{"排名":s["researchRank"]["rank"],"股票":s["symbol"],
                   "評分":s["researchRank"]["score"],"營收年增":f"{s['financial']['revenue_growth']:.1%}",
                   "基本面狀態":"合格" if s["evaluation"]["fundamental"] else "待查"}
                  for s in stocks],hide_index=True,use_container_width=True)
    st.caption("示範為合成數據；正式版會核對銀行版 Google Sheet 最新公開研究批次。")
with tab4:
    for h,review in zip(valued["holdings"],all_reviews):
        with st.expander(f"{h['symbol']}｜{review['status']}"):
            st.write("原因："+("、".join(review["reasons"]) or "本次無具體出場理由"))
            if review["unknown"]:
                st.warning("待確認："+ "、".join(review["unknown"]))
            st.caption(f"模擬財報期：{review['financial_period']}｜模擬行情日：{review['asof']}")
st.divider()
st.caption("預覽資料皆為虛構；正式版需要驗證你的 Google 登入、Google Sheets 存取與研究來源後才會使用真實資料。")
