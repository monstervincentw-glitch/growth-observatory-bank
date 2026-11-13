"""Filing-date-filtered financial snapshots; never forward-fill from future filings."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet", "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "operating_income": ["OperatingIncomeLoss"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "shares": ["CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"],
}

class CompanyFacts:
    def __init__(self, path=None, payload=None):
        data = payload if payload is not None else json.loads(Path(path).read_text())
        self.cik = data.get("cik")
        self.name = data.get("entityName", "")
        self.metrics = {}
        for metric, tags in TAGS.items():
            rows = []
            for priority, tag in enumerate(tags):
                for taxonomy in ["us-gaap", "dei", "ifrs-full"]:
                    fact = data.get("facts", {}).get(taxonomy, {}).get(tag, {})
                    for row in fact.get("units", {}).get("shares" if metric == "shares" else "USD", []):
                        if row.get("form") not in ["10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "6-K"]:
                            continue
                        if "filed" not in row or "end" not in row:
                            continue
                        item = {k: row.get(k) for k in ["start", "end", "filed", "val", "accn"]}
                        item.update(tag=tag, priority=priority)
                        rows.append(item)
            frame = pd.DataFrame(rows)
            if not frame.empty:
                for col in ["start", "end", "filed"]:
                    frame[col] = pd.to_datetime(frame[col], errors="coerce")
                frame["val"] = pd.to_numeric(frame["val"], errors="coerce")
                frame = frame.dropna(subset=["end", "filed", "val"])
            self.metrics[metric] = frame

    def known(self, metric, asof, lag_days=2):
        frame = self.metrics[metric]
        if frame.empty:
            return frame.copy()
        cutoff = pd.Timestamp(asof) - pd.Timedelta(days=lag_days)
        rows = frame[(frame.filed <= cutoff) & (frame.end <= pd.Timestamp(asof)) &
                     (frame.end >= pd.Timestamp(asof)-pd.Timedelta(days=1100))].copy()
        # Newest available filing wins; standard-tag preference resolves same-day duplicates.
        rows = rows.sort_values(["filed", "priority"], ascending=[False, True])
        return rows.drop_duplicates(["start", "end"], keep="first")

    def quarters(self, metric, asof, lag_days=2):
        known = self.known(metric, asof, lag_days)
        if known.empty:
            return pd.DataFrame(columns=["end", "start", "val", "filed", "accn"])
        known = known.dropna(subset=["start"]).copy()
        known["days"] = (known.end - known.start).dt.days + 1
        rows = []
        for r in known.itertuples():
            if 60 <= r.days <= 120:
                rows.append({"end": r.end, "start": r.start, "val": r.val,
                             "filed": r.filed, "accn": r.accn, "derived": False})
            elif 140 <= r.days <= 400:
                previous = known[(abs((known.start - r.start).dt.days) <= 3) &
                                 ((r.end - known.end).dt.days.between(60, 120)) &
                                 (known.days < r.days)]
                if not previous.empty:
                    prev = previous.sort_values(["end", "filed"], ascending=False).iloc[0]
                    rows.append({"end": r.end, "start": prev.end + pd.Timedelta(days=1),
                                 "val": r.val - prev.val, "filed": max(r.filed, prev.filed),
                                 "accn": str(r.accn) + ";" + str(prev.accn), "derived": True})
        if not rows:
            return pd.DataFrame(columns=["end", "start", "val", "filed", "accn"])
        result = pd.DataFrame(rows).sort_values(["end", "filed", "derived"], ascending=[True, False, True])
        return result.drop_duplicates("end", keep="first").sort_values("end").reset_index(drop=True)

    def instant(self, metric, asof, lag_days=2):
        rows = self.known(metric, asof, lag_days)
        if rows.empty:
            return None
        return rows.sort_values(["end", "filed", "priority"], ascending=[False, False, True]).iloc[0]

    def snapshot(self, asof, lag_days=2):
        asof = pd.Timestamp(asof)
        rev = self.quarters("revenue", asof, lag_days)
        result = {"company": self.name, "cik": self.cik, "asof": str(asof.date())}
        if len(rev) < 8:
            result["financial_status"] = "insufficient_quarterly_revenue"
            return result
        rev = rev.tail(8).reset_index(drop=True)
        gaps = rev.end.diff().dt.days.iloc[1:]
        if not gaps.between(60, 120).all() or (rev.val <= 0).any():
            result["financial_status"] = "noncontiguous_or_nonpositive_revenue"
            return result
        ends = rev.end.tail(4)
        ttm = float(rev.val.tail(4).sum())
        prior = float(rev.val.head(4).sum())
        growth = ttm/prior - 1
        qgrowth = float(rev.val.iloc[-1]/rev.val.iloc[-5] - 1)
        prior_qgrowth = float(rev.val.iloc[-2]/rev.val.iloc[-6] - 1)
        result.update(revenue_ttm=ttm, revenue_growth=growth, quarterly_growth=qgrowth,
                      growth_acceleration=qgrowth-prior_qgrowth,
                      latest_period=str(rev.end.iloc[-1].date()),
                      financial_age_days=int((asof-rev.end.iloc[-1]).days),
                      revenue_latest_filed=str(rev.filed.max().date()),
                      revenue_accessions="|".join(dict.fromkeys(rev.accn.astype(str))),
                      financial_status="ok")
        used_filed = list(rev.filed)
        for metric in ["operating_income", "cfo", "capex"]:
            q = self.quarters(metric, asof, lag_days)
            if q.empty:
                result[metric + "_ttm"] = np.nan
                continue
            matched = q[q.end.isin(ends)]
            if len(matched) != 4:
                result[metric + "_ttm"] = np.nan
            else:
                result[metric + "_ttm"] = float(matched.val.sum())
                result[metric + "_accessions"] = "|".join(dict.fromkeys(matched.accn.astype(str)))
                used_filed += list(matched.filed)
        result["operating_margin"] = result["operating_income_ttm"]/ttm
        result["cfo_margin"] = result["cfo_ttm"]/ttm
        result["fcf_margin"] = (result["cfo_ttm"]-abs(result["capex_ttm"]))/ttm
        result["capex_intensity"] = abs(result["capex_ttm"])/ttm
        shares = self.instant("shares", asof, lag_days)
        if shares is not None:
            result["shares"] = float(shares.val)
            result["shares_date"] = str(shares.end.date())
            result["shares_filed"] = str(shares.filed.date())
            result["shares_accession"] = shares.accn
            used_filed.append(shares.filed)
        else:
            result["shares"] = np.nan
        cash = self.instant("cash", asof, lag_days)
        result["cash"] = float(cash.val) if cash is not None else np.nan
        if cash is not None:
            used_filed.append(cash.filed)
        result["max_input_filed"] = str(max(used_filed).date())
        assert pd.Timestamp(result["max_input_filed"]) <= asof-pd.Timedelta(days=lag_days)
        return result
