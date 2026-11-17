"""Range-based NAV review of the *existing* A/B starting-year cohorts.

Monthly archived NAV is the only available older-history frequency. Append the
normal daily continuation, if any, without rewriting the original backtest.
The base value of each starting-year cohort is 1 at the beginning of that year.
"""
import math

import pandas as pd


def available_end_year(nav, market, live=None):
    """Latest actual observation year, not a future calendar year."""
    years = []
    for rows in nav[market].values():
        if rows:
            years.append(pd.Timestamp(rows[-1][0]).year)
    if live and live.get("market") == market and live.get("through"):
        years.append(pd.Timestamp(live["through"]).year)
    if not years:
        raise ValueError("沒有可用的歷史淨值")
    return max(years)


def cohort_window(nav, live, market, start_year, end_year):
    """Return chronological A/B NAV and sampled return/drawdown to end_year.

    end_year is inclusive. It is never allowed to precede the selected cohort
    start, and all observations after end_year are excluded from both the chart
    and statistics.
    """
    start_year, end_year = int(start_year), int(end_year)
    if end_year < start_year:
        raise ValueError("回測結束年不可早於起始年")
    rows = nav[market][str(start_year)]
    frame = pd.DataFrame(rows, columns=["日期", "A", "B"])
    if frame.empty:
        raise ValueError("所選起始年沒有歷史淨值")
    frame["日期"] = pd.to_datetime(frame["日期"]).dt.normalize()
    frame = frame.set_index("日期")
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("歷史淨值日期重複或順序錯誤")
    for plan in "AB":
        frame[plan] = pd.to_numeric(frame[plan], errors="raise")
    if live is not None:
        updates = {}
        for plan in "AB":
            key = f"{market}_{start_year}_{plan}"
            updates[plan] = {
                pd.Timestamp(day).normalize(): float(value)
                for day, value in live["curves"][key]
            }
        new = pd.DataFrame(updates).sort_index()
        if new.isna().any().any():
            raise ValueError("A／B 續算淨值日期不一致")
        overlap = frame.index.intersection(new.index)
        if len(overlap):
            difference = (frame.loc[overlap, ["A", "B"]] -
                          new.loc[overlap, ["A", "B"]]).abs()
            if (difference > 1e-8).any().any():
                raise ValueError("正式回測與封存淨值有衝突")
            new = new.loc[~new.index.isin(overlap)]
        frame = pd.concat([frame, new]).sort_index()
    frame = frame.loc[(frame.index.year >= start_year) &
                      (frame.index.year <= end_year), ["A", "B"]]
    if frame.empty or frame.index.has_duplicates:
        raise ValueError("所選年份沒有完整淨值資料")
    if not all(math.isfinite(float(v)) and float(v) > 0 for v in
               frame[["A", "B"]].to_numpy().flat):
        raise ValueError("歷史淨值含無效數字")
    start = pd.Timestamp(year=start_year, month=1, day=1)
    if frame.index.min() > start:
        base = pd.DataFrame({"A": [1.], "B": [1.]}, index=[start])
        frame = pd.concat([base, frame])
    # Keep the 1x cohort starting basis; do not rebase to a later year.
    if abs(float(frame.iloc[0]["A"]) - 1.) > 1e-8 or abs(float(frame.iloc[0]["B"]) - 1.) > 1e-8:
        if frame.index[0] == start:
            raise ValueError("起始淨值不是標準化本金 1")
    stats = {}
    for plan in "AB":
        values = frame[plan]
        stats[plan] = {
            "cumulative": float(values.iloc[-1] - 1),
            "sampled_drawdown": float((values / values.cummax() - 1).min()),
        }
    return frame.rename_axis("日期").reset_index(), stats, frame.index[-1].date().isoformat()
