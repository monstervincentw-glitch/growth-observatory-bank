"""Quote batches cannot relabel financial provenance or mix market sessions."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portfolio_portal"))
from google_market_quotes import UNIVERSE, INACTIVE, validate_quotes
from quotes import pool_reference_valuation


def batch(market):
    day = "2026-11-20"
    quotes = {s: {"close": 100, "priceDate": day,
                  "currency": "USD" if market == "US" else "TWD"}
              for s in UNIVERSE[market] if s not in INACTIVE[market]}
    return {"schema": 1, "market": market, "day": day,
            "status": "REFERENCE_QUOTES_COMPLETE", "expected": len(UNIVERSE[market]),
            "succeeded": len(quotes), "quotes": quotes,
            "missing": [], "inactive": list(INACTIVE[market])}


class MarketPoolTests(unittest.TestCase):
    def test_wrong_symbol_cannot_replace_a_missing_pool_member(self):
        b = batch("TW")
        b["quotes"]["wrong"] = b["quotes"].pop("2330")
        with self.assertRaises(ValueError):
            validate_quotes(b, "TW")

    def test_incomplete_currency_and_date_mismatch_are_rejected(self):
        for change in ({"currency": "USD"}, {"priceDate": "2026-11-19"},
                       {"close": float("nan")}):
            b = batch("TW")
            b["quotes"]["2330"].update(change)
            with self.assertRaises(ValueError):
                validate_quotes(b, "TW")
        b = batch("TW")
        b["missing"] = ["2330"]
        with self.assertRaises(ValueError):
            validate_quotes(b, "TW")

    def test_otc_holding_uses_full_pool_and_keeps_original_signal_stock(self):
        b = batch("TW")
        b["quotes"]["6223"]["close"] = 3560
        stock = {"symbol": "6223", "close": 3500, "priceDate": "2026-11-19",
                 "integrity": True, "financial": {"original": True}}
        block = {"macro": {"marketAsOf": "2026-11-19"}, "stocks": [stock]}
        original = deepcopy(block)
        account = {"lots": {"6223": [{"quantity": Decimal("2"), "unit_cost": Decimal("3000")}]},
                   "cash": Decimal("10"), "realized": Decimal("0")}
        value, info = pool_reference_valuation(account, block, b, "TW", date(2026, 11, 20))
        self.assertEqual(value["nav"], Decimal("7130"))
        self.assertEqual(info["date"], "2026-11-20")
        self.assertEqual(value["holdings"][0]["stock"], stock)
        self.assertEqual(block, original)
        value, info = pool_reference_valuation(account, block, b, "TW", date(2026, 11, 26))
        self.assertFalse(info["fallback"])
        self.assertEqual(value["nav"], Decimal("7010"))
