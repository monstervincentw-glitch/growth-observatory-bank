"""Read the audited Taiwan mutual-fund study without changing existing research."""
import gzip
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from bank_layout import fund_heading, readable_table, strategy_comparison

ARCHIVE = Path(__file__).resolve().parent / "data" / "taiwan-fund-research.json.gz"
NAME = "基金完整中文名稱"
EVENTS = {
    "GFC": "2008 金融海嘯",
    "COVID": "2020 疫情",
    "2022": "2022 升息",
    "2025_tariff": "2025 關稅回檔",
}
CATEGORIES = ["科技股票", "美國成長", "全球股票", "亞洲及新興市場股票", "一般股票", "平衡型",
              "多重資產", "投資等級債", "非投資等級債", "短存續期債券", "其他債券", "債券型", "貨幣市場", "其他基金"]
PLANS = {
    "A": "A 買入持有",
    "B": "B 紅燈全退持現金",
    "C": "C 紅燈減碼至 50%",
    "D": "D 紅燈轉 SHY",
}


@st.cache_data(show_spinner=False)
def load_research():
    parts = sorted(ARCHIVE.parent.glob(ARCHIVE.name + ".part*"))
    raw = b"".join(part.read_bytes() for part in parts) if parts else ARCHIVE.read_bytes()
    data = json.loads(gzip.decompress(raw))
    frames = {
        key: pd.DataFrame(data[key]["rows"], columns=data[key]["columns"])
        for key in ("catalog", "performance", "events", "compare", "rank", "common")
    }
    frames["events"]["事件代碼"] = frames["events"]["事件代碼"].astype(str)
    vitax = pd.read_csv(ARCHIVE.with_name("vitax-common-periods.csv"))
    frames["common"] = pd.concat([frames["common"], vitax], ignore_index=True)
    return data["end"], frames, data["expansion"]


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


def rankings(data):
    rank = data["rank"]
    common = data["common"]
    st.subheader("同期間排名")
    st.caption("同一基準日收盤重新定基，保留該日原有部位。每個子基金以一個主要級別參與排名。")
    dates = sorted(rank["基準日"].unique(), reverse=True)
    date_col, category_col, plan_col, metric_col = st.columns([1, 1.25, 1.2, 1.25], gap="small")
    start = date_col.selectbox("共同比較基準日", dates, key="tw-fund-common-start")
    period = rank[rank["基準日"].eq(start)]
    categories = [category for category in CATEGORIES if category in set(period["分類"])]
    category = category_col.selectbox("排名基金類別", categories, key="tw-fund-rank-category")
    method = plan_col.selectbox("排名方案", list(PLANS), format_func=PLANS.__getitem__, key="tw-fund-rank-method")
    metrics = ["CAGR", "累積報酬", "最大回撤", "相較A回撤改善", "CAGR除以回撤絕對值"]
    metric = metric_col.selectbox("排名指標", metrics, key="tw-fund-rank-metric")
    view = period[period["分類"].eq(category) & period["方案"].eq(method)].sort_values(metric, ascending=False)
    st.caption(f"共同結束日 {period['結束日'].iloc[0]} · 本組 {len(view)} 檔。回撤改善正值代表跌幅較買入持有小。")
    readable_table(view[[NAME, "ISIN", *metrics, "CAGR名次", "最大回撤名次", "回撤改善名次", "風控效率名次"]],
                   ["CAGR", "累積報酬", "最大回撤", "相較A回撤改善"], label="同期間排名")
    export(view, "目前排名 CSV", "taiwan-fund-period-ranking.csv", "tw-fund-rank-download")
    with st.expander("VITAX、FSPTX、HYG、JNK 與既有研究對照"):
        reference = common[common["基準日"].eq(start) & common["方案"].eq(method)]
        benchmarks = reference[reference["ISIN"].isin(["VITAX", "FSPTX", "HYG", "JNK"])]
        st.caption("原顧問基準以同日匯率換算台幣。基準的現金部位為美元，台灣基金以台幣持有現金。")
        readable_table(benchmarks[[NAME, "ISIN", "基準日", "結束日", *metrics]],
                       ["CAGR", "累積報酬", "最大回撤", "相較A回撤改善"], label="同期間基準")
        export(reference, "同期間完整對照 CSV", "taiwan-fund-period-comparison.csv", "tw-fund-common-download")


def fund_catalog(visible):
    with st.expander(f"基金名錄（{len(visible)} 檔）"):
        readable_table(visible[[NAME, "基金公司", "分類", "級別與配息政策", "幣別", "級別避險", "ISIN", "主要比較級別", "回測驗證狀態"]],
                       label="基金名錄")
        export(visible, "基金名錄 CSV", "taiwan-fund-catalog.csv", "tw-fund-catalog-download")


def fund_source(item):
    with st.expander("銀行淨值期間與商品連結"):
        st.caption(f"銀行淨值期間 {item['銀行NAV起日']} 至 {item['銀行NAV迄日']} · 成立日 {item['成立日']}")
        st.link_button("銀行正式商品資料", item["銀行產品頁"])


def render(view):
    through, data, summary = load_research()
    if view == "同期間排名":
        st.caption(f"封存至 {through} · 新臺幣帳戶 · 摩根、聯博 {summary['primary']} 個主要級別，新增 {summary['new_verified']} 檔已驗證。")
        rankings(data)
        return

    with st.container(horizontal=True, gap="small", vertical_alignment="bottom"):
        query = st.text_input("搜尋基金", placeholder="完整中文名稱或 ISIN", key="tw-fund-search", width=208).strip()
        issuers = ["摩根", "聯博", "既有其他基金"]
        with st.popover("基金篩選"):
            category = st.multiselect("基金分類", CATEGORIES, default=CATEGORIES, key="tw-fund-category")
            issuer = st.multiselect("基金公司", issuers, default=issuers, key="tw-fund-issuer")
        catalog = data["catalog"]
        company = catalog["基金公司"].replace("", "既有其他基金")
        visible = catalog[catalog["分類"].isin(category) & company.isin(issuer)]
        if query:
            visible = visible[
                visible[NAME].str.contains(query, case=False, regex=False)
                | visible["ISIN"].str.contains(query, case=False, regex=False)
            ]
        if visible.empty:
            st.info("目前篩選沒有符合的基金。")
            return

        isin = st.selectbox("查看基金（ISIN）", visible["ISIN"].tolist(), key="tw-fund-product", width=164)
        item = visible.loc[visible["ISIN"].eq(isin)].iloc[0]
        performance = data["performance"].loc[data["performance"]["ISIN"].eq(isin)].copy()
        if not performance.empty:
            years = sorted(performance["起始年"].unique())
            year = st.selectbox("投資起始年", years, key=f"tw-fund-year-{isin}", width=138)
    fund_heading(item[NAME], f"{item['級別與配息政策']} · {item['幣別']} · {isin} · {item['回測驗證狀態']}")
    if performance.empty:
        st.info(item["注意事項"])
        fund_source(item)
        fund_catalog(visible)
        return

    performance.insert(0, NAME, item[NAME])
    cohort = performance[performance["起始年"].eq(year)]
    st.subheader("A／B／C／D 績效比較")
    strategy_comparison(cohort, PLANS)
    if item["注意事項"]:
        st.info(item["注意事項"])
    st.caption(f"封存至 {through} · 新臺幣帳戶 · 摩根、聯博 {summary['primary']} 個主要級別，新增 {summary['new_verified']} 檔已驗證。")

    with st.expander("各策略明細與年度績效 CSV"):
        columns = ["方案", "起始日", "結束日", "累積報酬", "CAGR", "最大回撤", "回本日", "高點至回本日數"]
        percentage = ["累積報酬", "CAGR", "最大回撤"]
        if cohort["最差年度"].notna().any():
            columns += ["最差年度", "最差年度報酬", "最差年度為部分區間", "買入次數", "賣出次數", "直接成本TWD"]
            percentage.append("最差年度報酬")
        show_table(cohort[columns], percentage)
        export(performance, "年度回測績效 CSV", f"{isin}-annual-performance.csv", "tw-fund-perf-download")
    fund_source(item)
    fund_catalog(visible)

    st.subheader("市場事件回撤")
    event_col, _ = st.columns([1, 3])
    event = event_col.selectbox("基金市場事件", list(EVENTS), format_func=EVENTS.__getitem__, key="tw-fund-event")
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

    st.subheader("不同年度起跑比較")
    st.caption("各年度從該基金第一個可用淨值日獨立起跑，統一於封存日結束。")
    with st.expander("展開各年度 A／B／C／D 比較"):
        show_table(performance[["起始年", "方案", "累積報酬", "CAGR", "最大回撤"]],
                   ["累積報酬", "CAGR", "最大回撤"])
    compare = data["compare"].loc[data["compare"]["ISIN"].eq(isin)].copy()
    compare.insert(0, NAME, item[NAME])
    export(compare, "與買入持有同日起跑比較 CSV", f"{isin}-same-start-comparison.csv", "tw-fund-compare-download")
    st.caption("申贖、費用、匯率與交割按研究假設估計。短於一年時 CAGR 留白。")
