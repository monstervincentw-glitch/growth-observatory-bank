"""Presentation components for the bank research pages."""
from html import escape
from pathlib import Path
import re

import altair as alt
import pandas as pd
import streamlit as st

STYLE = Path(__file__).with_name("bank_layout.css")


def layout_style():
    st.html(STYLE)


def header():
    st.html(
        '<header class="bank-hero"><h1>成長股觀測站</h1>'
        '<p>銀行版 <span>·</span> 美股與台股研究、策略回測與顧問風控</p></header>'
    )


def status_line(source, us, tw, through):
    labels = [("來源", source), ("美股", us), ("台股", tw), ("完整回測", through)]
    items = "".join(f"<span>{label} <b>{escape(str(value))}</b></span>" for label, value in labels)
    st.html(f'<div class="bank-status" aria-label="資料來源及截止日">{items}</div>', width="content")


def fund_heading(name, details):
    disclosure = re.search(r"\s*[（(](?:本基金|基金之)", name)
    title = escape(name)
    if disclosure:
        split = disclosure.start()
        title = (f'{escape(name[:split])}<span class="bank-fund-disclosure">'
                 f'{escape(name[split:])}</span>')
    st.html(f'<div class="bank-fund-heading"><h3>{title}</h3>'
            f'<p>{escape(details)}</p></div>')


def readable_table(frame, percentages=(), *, label, layout="named"):
    """Keep long names wrap-enabled and short comparisons visible on mobile."""
    headings = "".join(f'<th scope="col">{escape(str(col))}</th>' for col in frame)
    rows = []
    for record in frame.itertuples(index=False, name=None):
        cells = []
        for col, value in zip(frame, record):
            if pd.isna(value):
                value = "—"
            elif col in percentages:
                value = f"{value:.2%}"
            cells.append(f'<td data-label="{escape(str(col))}">{escape(str(value))}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    st.html(f'<div class="bank-table bank-table-{layout}" tabindex="0" aria-label="{escape(label)}">'
            f'<table aria-label="{escape(label)}"><thead><tr>{headings}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def strategy_comparison(frame, labels):
    columns = ["方案", "累積報酬", "CAGR", "最大回撤"]
    return_metric = "CAGR" if frame["CAGR"].notna().all() else "累積報酬"
    metrics = [return_metric, "最大回撤"]
    graph = frame[["方案", *metrics]].melt("方案", var_name="指標", value_name="比率").dropna()
    chart = alt.Chart(graph).mark_bar(cornerRadiusEnd=3, size=8).encode(
        x=alt.X("比率:Q", title=None, axis=alt.Axis(format="%", labelOverlap=True, tickCount=5)),
        y=alt.Y("方案:N", title=None, sort=list(labels)),
        yOffset=alt.YOffset("指標:N", sort=metrics),
        color=alt.Color("指標:N", sort=metrics, scale=alt.Scale(range=["#17664d", "#9a4b37"]),
                        legend=alt.Legend(title=None, orient="top", labelFontSize=13)),
        tooltip=[alt.Tooltip("方案:N"), alt.Tooltip("指標:N"), alt.Tooltip("比率:Q", format=".2%")],
    ).properties(height=188).configure_axis(
        labelFontSize=12, labelColor="#314b42", gridColor="#dbe6df"
    ).configure_legend(labelColor="#314b42")
    chart_col, table_col = st.columns([1.1, 1], gap="medium")
    with chart_col:
        st.altair_chart(chart)
    with table_col:
        display = frame[columns].copy()
        display["方案"] = display["方案"].map(labels)
        readable_table(display, columns[1:], label="A／B／C／D 績效比較", layout="strategies")
