"""Read the audited Taiwan mutual-fund study without changing existing research."""
import gzip
import json
from pathlib import Path

import pandas as pd
import streamlit as st

ARCHIVE = Path(__file__).resolve().parent / "data" / "taiwan-fund-research.json.gz"
NAME = "基金完整中文名稱"
EVENTS = {
    "GFC": "2008 金融海嘯",
    "COVID": "2020 疫情",
    "2022": "2022 升息",
    "2025_tariff": "2025 關稅回檔",
}
CATEGORIES = ["科技股票", "美國成長", "平衡型", "債券型"]
PLANS = {
    "A": "A 買入持有",
    "B": "B 紅燈全退持現金",
    "C": "C 紅燈減碼至 50%",
    "D": "D 紅燈轉 SHY",
}


@st.cache_data(show_spinner=False)
def load_research():
    with gzip.open(ARCHIVE, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    frames = {
        key: pd.DataFrame(data[key]["rows"], columns=data[key]["columns"])
        for key in ("catalog", "performance", "events", "compare")
    }
    frames["events"]["事件代碼"] = frames["events"]["事件代碼"].astype(str)
    return data["end"], frames


def export(frame, label, filename, key):
    st.download_button(label, frame.to_csv(index=False).encode("utf-8-sig"),
                       filename, "text/csv", key=key, on_click="ignore")


def show_table(frame, percentage=()):
    view = frame.copy()
    if "方案" in view:
        view["方案"] = view["方案"].map(PLANS)
    for column in percentage:
        view[column] = view[column].map(lambda val: f"{val:.2%}" if pd.notna(val) else "—")
    st.dataframe(view, hide_index=True)


def render():
    through, data = load_research()
    st.subheader("台灣可申購共同基金")
    st.caption(f"封存至 {through} · 新臺幣帳戶 · 15 檔通過驗證，1 檔限 2025 年起，1 檔待驗證。")
    category = st.multiselect("基金分類", CATEGORIES, default=CATEGORIES, key="tw-fund-category")
    query = st.text_input("搜尋完整中文基金名稱或 ISIN", key="tw-fund-search").strip()
    catalog = data["catalog"]
    visible = catalog[catalog["分類"].isin(category)]
    if query:
        visible = visible[
            visible[NAME].str.contains(query, case=False, regex=False)
            | visible["ISIN"].str.contains(query, case=False, regex=False)
        ]
    if visible.empty:
        st.info("目前篩選沒有符合的基金。")
        return
    show_table(visible[[NAME, "分類", "級別與配息政策", "幣別", "ISIN", "回測驗證狀態"]])
    export(visible, "基金名錄 CSV", "taiwan-fund-catalog.csv", "tw-fund-catalog-download")

    labels = visible.set_index("ISIN")[NAME].to_dict()
    isin = st.selectbox("查看基金", list(labels), format_func=labels.__getitem__, key="tw-fund-product")
    item = visible.loc[visible["ISIN"].eq(isin)].iloc[0]
    st.markdown(f"#### {item[NAME]}")
    st.caption(f"{item['級別與配息政策']} · {item['幣別']} · {isin} · {item['回測驗證狀態']}")
    st.caption(f"銀行淨值期間 {item['銀行NAV起日']} 至 {item['銀行NAV迄日']} · 成立日 {item['成立日']}")
    st.link_button("銀行正式商品資料", item["銀行產品頁"])
    performance = data["performance"].loc[data["performance"]["ISIN"].eq(isin)].copy()
    if performance.empty:
        st.info(item["注意事項"])
        return
    if item["注意事項"]:
        st.info(item["注意事項"])
    performance.insert(0, NAME, item[NAME])
    years = sorted(performance["起始年"].unique())
    year = st.selectbox("基金投資起始年", years, key=f"tw-fund-year-{isin}")
    st.markdown("##### A／B／C／D 績效比較")
    cohort = performance[performance["起始年"].eq(year)]
    show_table(cohort[["方案", "起始日", "結束日", "累積報酬", "CAGR", "最大回撤", "回本日", "高點至回本日數"]],
               ["累積報酬", "CAGR", "最大回撤"])
    export(performance, "年度回測績效 CSV", f"{isin}-annual-performance.csv", "tw-fund-perf-download")

    st.markdown("##### 市場事件回撤")
    event = st.selectbox("基金市場事件", list(EVENTS), format_func=EVENTS.__getitem__, key="tw-fund-event")
    history = data["events"]
    history = history[history["ISIN"].eq(isin)].copy()
    history.insert(0, NAME, item[NAME])
    selected = history[history["起始年"].eq(year) & history["事件代碼"].eq(str(event))]
    if selected.empty:
        st.caption("此起跑年度在該事件沒有可用區間。")
    else:
        show_table(selected[["方案", "區間起日", "區間迄日", "部分區間", "事件報酬", "事件最大回撤", "紅燈日數"]],
                   ["事件報酬", "事件最大回撤"])
    export(history, "市場事件回撤 CSV", f"{isin}-event-drawdowns.csv", "tw-fund-event-download")

    st.markdown("##### 不同年度起跑比較")
    st.caption("各年度從該基金第一個可用淨值日獨立起跑，統一於封存日結束。")
    with st.expander("展開各年度 A／B／C／D 比較"):
        show_table(performance[["起始年", "方案", "累積報酬", "CAGR", "最大回撤"]],
                   ["累積報酬", "CAGR", "最大回撤"])
    compare = data["compare"].loc[data["compare"]["ISIN"].eq(isin)].copy()
    compare.insert(0, NAME, item[NAME])
    export(compare, "與買入持有同日起跑比較 CSV", f"{isin}-same-start-comparison.csv", "tw-fund-compare-download")
    st.caption("申贖、費用、匯率與交割按研究假設估計。短於一年時 CAGR 留白。")
