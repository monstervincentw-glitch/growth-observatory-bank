"""Presentation components for the public bank research dashboard."""

from html import escape
from numbers import Integral, Real
from pathlib import Path
import re

import altair as alt
import pandas as pd
import streamlit as st

STYLE = Path(__file__).with_name("bank_layout.css")
INK = "#303849"
GRID = "#DFE5EE"
PLOT = "#FFFFFF"
SERIES = ["#48698C", "#947341", "#657286", "#B76B64"]

# Values and their units come from the same published macro snapshot as the detail table.
FUNDING_INDICATORS = (
    ("rates", "利率", "90 日實質利率升逾 0.50 個百分點，或政策利率升逾 0.25 個百分點。", (
        ("DFII10", "實質殖利率", "real_yield", ".2f", "%", "real_yield_90d_change", "+.2f", " 個百分點"),
        ("FEDFUNDS", "政策利率", "fed_funds", ".2f", "%", "fed_funds_90d_change", "+.2f", " 個百分點"),
    )),
    ("conditions", "金融條件", "NFCI 高於 0，或 90 日升逾 0.25 指數點。", (
        ("NFCI", "NFCI 指數", "financial_conditions", ".3f", "", "financial_conditions_90d_change", "+.3f", " 點"),
    )),
    ("inflation", "通膨", "CPI 年增逾 4%，且近 3 個月年化高於年增率。", (
        ("CPIAUCSL", "CPI 年增", "cpi_yoy", ".2%", "", None, "", ""),
        ("CPIAUCSL", "近 3 個月年化", "cpi_3m_annualized", ".2%", "", None, "", ""),
    )),
)
FUNDING_STATE = {0: ("clear", "未觸發"), 1: ("triggered", "已觸發"), None: ("unknown", "未知")}
NUMERIC_TEXT = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?"


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


def display_value(value, spec, suffix):
    return "—" if value is None or pd.isna(value) else f"{value:{spec}}{suffix}"


def funding_dashboard(macro):
    """Show the published states with readings available on expansion."""
    cards = []
    for key, label, rule, series in FUNDING_INDICATORS:
        kind, status = FUNDING_STATE[macro["categoryStates"][key]]
        readings = []
        dates = {}
        for code, name, field, spec, suffix, change, change_spec, change_suffix in series:
            value = display_value(macro.get(field), spec, suffix)
            movement = ""
            if change:
                delta = display_value(macro.get(change), change_spec, change_suffix)
                movement = f'<span class="bank-reading-change">90 日 {escape(delta)}</span>'
            date = macro["seriesMeta"].get(code, {}).get("observationDate") or "待核對"
            dates.setdefault(code, (name, date))
            readings.append(
                f'<div><dt>{name}</dt><dd>{escape(value)}{movement}</dd></div>'
            )
        dated = " · ".join(
            f"{name} {date}" if len(dates) > 1 else date for name, date in dates.values()
        )
        cards.append(
            f'<details class="bank-funding-card bank-funding-card-{kind}">'
            f'<summary><span class="bank-card-name">{label}</span><strong>{status}</strong>'
            f'<small>{rule}</small></summary><div class="bank-card-detail">'
            f'<dl class="bank-readings">{"".join(readings)}</dl>'
            f'<small>資料日 <b>{escape(dated)}</b></small></div></details>'
        )
    warning = macro.get("warning")
    note = f'<p class="bank-funding-note" role="status">{escape(warning)}</p>' if warning else ""
    st.html(
        '<section class="bank-funding" aria-label="全球資金溫度">'
        '<h2>全球資金溫度</h2>'
        f'<div class="bank-funding-cards">{"".join(cards)}</div>'
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


def ordered_rows(frame, label):
    """Sort only the table view and keep the original ordering by default."""
    with st.popover(f"排序｜{label}"):
        column = st.selectbox("排序欄位", [None, *frame.columns],
                              format_func=lambda value: "原始順序" if value is None else str(value),
                              key=f"table-column-{label}")
        direction = st.segmented_control("排序方向", ["由小到大", "由大到小"], default="由小到大",
                                         key=f"table-order-{label}")
    ascending = direction != "由大到小"
    if column is None:
        return frame
    values = frame[column]
    if pd.api.types.is_numeric_dtype(values.dtype):
        return frame.sort_values(column, ascending=ascending, kind="stable", na_position="last")
    visible = values.astype("string").str.strip().replace({"—": pd.NA, "": pd.NA})
    if visible.dropna().str.fullmatch(NUMERIC_TEXT).all():
        visible = pd.to_numeric(visible.str.replace(",", "").str.removesuffix("%"))
    else:
        visible = visible.str.casefold()
    return frame.sort_values(column, ascending=ascending, kind="stable", na_position="last", key=lambda _: visible)


def readable_table(frame, percentages=(), *, label, layout="standard"):
    """Render aligned, wrap-enabled cells and retain exact numeric values in tooltips."""
    if layout != "market" and len(frame) > 1:
        frame = ordered_rows(frame, label)
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
                            labelPadding=7, gridColor=GRID, domainColor="#69768A", tickColor="#69768A")
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
