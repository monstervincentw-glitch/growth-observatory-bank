"""Presentation components for the public bank research dashboard."""

from html import escape
from numbers import Integral, Real
from pathlib import Path
import re

import altair as alt
import pandas as pd
import streamlit as st

STYLE = Path(__file__).with_name("bank_layout.css")
INK = "#2d3930"
GRID = "#e6dfd1"
PLOT = "#fffdf8"
SERIES = ["#286253", "#9a621b", "#50588a", "#943e48"]

FUNDING_INDICATORS = (
    ("rates", "利率", "90 日實質利率升逾 0.50 個百分點，或政策利率升逾 0.25 個百分點。",
     (("DFII10", "實質"), ("FEDFUNDS", "政策"))),
    ("conditions", "金融條件", "NFCI 高於 0，或 90 日升逾 0.25 指數點。", (("NFCI", "NFCI"),)),
    ("inflation", "通膨", "CPI 年增逾 4%，且近 3 個月年化高於年增率。", (("CPIAUCSL", "CPI"),)),
)
LIGHT_STATE = {0: ("clear", "未觸發"), 1: ("red", "觸發"), None: ("unknown", "未知／資料不足")}
FUNDING_SUMMARY = {
    "non_red": ("未達 2 燈門檻", "資金面通過，進場仍須符合個股與策略條件"),
    "red": ("已達 2 燈門檻", "停止新買，主策略依規則退出"),
    "unknown": ("2 燈門檻待確認", "暫停新買，等待可核對的資金判定"),
}


def layout_style():
    st.html(STYLE)


def sidebar_brand():
    st.sidebar.html(
        '<div class="bank-sidebar-brand"><span>成長股觀測站</span><small>銀行版 · 公開研究</small></div>'
    )


def header(title):
    st.html(
        f'<header class="bank-hero"><span>成長股觀測站｜銀行版</span><h1>{escape(title)}</h1></header>'
    )


def status_line(source, us, tw, through):
    labels = [("來源", source), ("美股", us), ("台股", tw), ("回測封存", through)]
    items = "".join(f"<span>{label} <b>{escape(str(value))}</b></span>" for label, value in labels)
    st.html(f'<div class="bank-status" aria-label="資料來源及截止日">{items}</div>')


def funding_dashboard(macro, calculated_at, signal_available):
    """Describe the published decision and its observation dates."""
    cards = []
    for key, label, rule, series in FUNDING_INDICATORS:
        kind, status = LIGHT_STATE[macro["categoryStates"][key]]
        dates = " · ".join(
            f"{name} {macro['seriesMeta'].get(code, {}).get('observationDate') or '待核對'}"
            for code, name in series
        )
        cards.append(
            f'<article class="bank-light bank-light-{kind}">'
            f'<div class="bank-light-status"><span class="bank-dot" aria-hidden="true"></span>'
            f'<div><h3>{label}</h3><strong>{status}</strong></div></div>'
            f'<p>{rule}</p><small>最近資料　{escape(dates)}</small></article>'
        )
    low, high = macro["scoreRange"]
    score = str(low) if low == high else f"{low}–{high}"
    decision = macro["decision"]
    threshold, action = FUNDING_SUMMARY[decision]
    if not signal_available:
        action = "暫停新買，正式訊號待更新"
    vintage = macro.get("macro_vintage_date") or "待核對"
    warning = macro.get("warning")
    note = f'<p class="bank-funding-note" role="status">{escape(warning)}</p>' if warning else ""
    st.html(
        '<section class="bank-funding" aria-label="全球資金溫度">'
        '<div class="bank-funding-head"><h2>全球資金溫度</h2>'
        f'<div>收盤 <b>{escape(macro["marketAsOf"])}</b> · 總經版本 <b>{escape(vintage)}</b>'
        f' · 計算 <b>{escape(calculated_at)}</b>（台北時間）</div></div>'
        f'<div class="bank-lights">{"".join(cards)}</div>'
        f'<div class="bank-risk bank-risk-{decision}">'
        f'<div class="bank-risk-score"><span>風險分數</span><strong>{escape(score)}<small> / 3</small></strong></div>'
        f'<div class="bank-risk-action"><strong>{threshold}</strong><span>{action}</span></div></div>'
        f'{note}</section>'
    )


def fund_heading(name, details):
    disclosure = re.search(r"\s*[（(](?:本基金|基金之)", name)
    title = escape(name)
    if disclosure:
        split = disclosure.start()
        title = (f'{escape(name[:split])}<span class="bank-fund-disclosure">'
                 f'{escape(name[split:])}</span>')
    st.html(f'<div class="bank-fund-heading"><h3 title="{escape(name)}">{title}</h3>'
            f'<p>{escape(details)}</p></div>')


def readable_table(frame, percentages=(), *, label, layout="standard"):
    """Render aligned, wrap-enabled cells and retain exact numeric values in tooltips."""
    numeric_columns = {
        col for col in frame if col in percentages or pd.api.types.is_numeric_dtype(frame[col].dtype)
        or frame[col].dropna().map(lambda value: isinstance(value, str) and bool(re.fullmatch(r"[+-]?\d+(?:\.\d+)?%", value))).any()
    }
    headings = "".join(
        f'<th scope="col" role="columnheader" class="{"bank-number" if col in numeric_columns else ""}">{escape(str(col))}</th>'
        for col in frame
    )
    rows = []
    for record in frame.itertuples(index=False, name=None):
        cells = []
        for col, raw in zip(frame, record):
            tone = ""
            tooltip = ""
            if pd.isna(raw):
                value = "—"
            elif col in percentages:
                value = f"{raw:.2%}"
            elif isinstance(raw, bool):
                value = "是" if raw else "否"
            elif isinstance(raw, Integral):
                value = str(raw) if "年" in col else f"{raw:,}"
            elif isinstance(raw, Real):
                value = f"{raw:,.2f}"
                tooltip = f' title="{escape(str(raw))}"'
            else:
                value = str(raw)
                if len(value) > 20:
                    tooltip = f' title="{escape(value)}"'
            numeric = isinstance(raw, Real) and not isinstance(raw, bool)
            if re.fullmatch(r"[+-]?\d+(?:\.\d+)?%", value):
                numeric = True
                signed = float(value[:-1].replace(",", ""))
                tone = " bank-negative" if signed < 0 else " bank-positive" if signed > 0 else ""
            if numeric or col in numeric_columns:
                tone = " bank-number" + tone
            cells.append(
                f'<td role="cell" class="{tone.strip()}" data-label="{escape(str(col))}"{tooltip}>{escape(value)}</td>'
            )
        rows.append('<tr role="row">' + "".join(cells) + "</tr>")
    if not rows:
        st.caption("目前沒有可顯示的資料。")
        return
    st.html(f'<div class="bank-table bank-table-{layout}">'
            f'<table role="table" aria-label="{escape(label)}"><thead role="rowgroup"><tr role="row">{headings}</tr></thead>'
            f'<tbody role="rowgroup">{"".join(rows)}</tbody></table></div>')


def styled(chart, height):
    return (chart.properties(height=height)
            .configure(background=PLOT, font="sans-serif", padding=12)
            .configure_axis(labelFontSize=13, titleFontSize=13, labelColor=INK, titleColor=INK,
                            labelPadding=7, gridColor=GRID, domainColor="#a29783", tickColor="#a29783")
            .configure_legend(labelFontSize=14, titleFontSize=13, labelColor=INK, titleColor=INK,
                              symbolStrokeWidth=3, padding=8)
            .configure_view(stroke=None))


def trend_chart(frame, x, series, *, ylabel, value_format=",.2f", year_axis=False, height=360):
    chart_data = frame[[x, *series]].melt(x, var_name="序列", value_name="數值")
    x_type = "Q" if year_axis else "T"
    x_format = "d" if year_axis else "%Y-%m"
    chart = alt.Chart(chart_data).mark_line(strokeWidth=3, clip=True).encode(
        x=alt.X(f"{x}:{x_type}", title=x, scale=alt.Scale(zero=False) if year_axis else alt.Undefined,
                axis=alt.Axis(format=x_format, labelOverlap="greedy", tickCount=6)),
        y=alt.Y("數值:Q", title=ylabel, axis=alt.Axis(format=value_format)),
        color=alt.Color("序列:N", scale=alt.Scale(domain=series, range=SERIES[:len(series)]),
                        legend=alt.Legend(title=None, orient="top") if len(series) > 1 else None),
        tooltip=[alt.Tooltip(f"{x}:{x_type}", format="d" if year_axis else "%Y-%m-%d"),
                 alt.Tooltip("序列:N"), alt.Tooltip("數值:Q", format=".2%" if value_format.endswith("%") else value_format)],
    )
    if len(series) > 1:
        chart = chart.encode(strokeDash=alt.StrokeDash(
            "序列:N", scale=alt.Scale(domain=series, range=[[1, 0], [7, 4]]),
            legend=alt.Legend(title=None, orient="top"),
        ))
    st.altair_chart(styled(chart, height), use_container_width=True, theme=None)


def strategy_comparison(frame, labels):
    columns = ["方案", "累積報酬", "CAGR", "最大回撤"]
    return_metric = "CAGR" if frame["CAGR"].notna().all() else "累積報酬"
    metrics = [return_metric, "最大回撤"]
    graph = frame[["方案", *metrics]].melt("方案", var_name="指標", value_name="比率").dropna()
    chart = alt.Chart(graph).mark_bar(cornerRadiusEnd=3, size=13).encode(
        x=alt.X("比率:Q", title=None, axis=alt.Axis(format="%", labelOverlap=True, tickCount=5)),
        y=alt.Y("方案:N", title=None, sort=list(labels)),
        yOffset=alt.YOffset("指標:N", sort=metrics),
        color=alt.Color("指標:N", scale=alt.Scale(domain=metrics, range=[SERIES[0], SERIES[3]]),
                        legend=alt.Legend(title=None, orient="top")),
        tooltip=[alt.Tooltip("方案:N"), alt.Tooltip("指標:N"), alt.Tooltip("比率:Q", format=".2%")],
    )
    chart_col, table_col = st.columns([1.05, 1], gap="medium")
    with chart_col:
        st.altair_chart(styled(chart, 238), use_container_width=True, theme=None)
    with table_col:
        display = frame[columns].copy()
        display["方案"] = display["方案"].map(labels)
        readable_table(display, columns[1:], label="A／B／C／D 績效比較", layout="strategies")


def event_drawdown_chart(frame, strategy, drawdown, event_label, *, value_label="事件最大回撤"):
    data = frame[[strategy, drawdown]].rename(columns={strategy: "方案", drawdown: value_label}).dropna()
    if data.empty:
        return
    plan_codes = list(dict.fromkeys(data["方案"]))
    left = min(0.0, float(data[value_label].min()))
    domain = [left * 1.35 if left < 0 else -.01, max(0.0, float(data[value_label].max()) * 1.2)]
    chart = alt.Chart(data).encode(
        x=alt.X(f"{value_label}:Q", title=value_label, scale=alt.Scale(domain=domain, nice=False),
                axis=alt.Axis(format="%", tickCount=5)),
        y=alt.Y("方案:N", title=None, sort=plan_codes),
    )
    bars = chart.mark_bar(size=26, cornerRadiusEnd=3).encode(
        color=alt.Color("方案:N", legend=None, scale=alt.Scale(domain=plan_codes, range=SERIES[:len(plan_codes)])),
        tooltip=[alt.Tooltip("方案:N"), alt.Tooltip(f"{value_label}:Q", format=".2%")],
    )
    values = chart.mark_text(align="right", dx=-6, fontSize=14, fontWeight="bold", color=INK).encode(
        text=alt.Text(f"{value_label}:Q", format=".2%"),
    )
    st.caption(event_label)
    st.altair_chart(styled(bars + values, 215), use_container_width=True, theme=None)
