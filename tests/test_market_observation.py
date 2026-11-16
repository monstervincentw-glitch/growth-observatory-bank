import unittest

from shared.candidates import ALL, DIVERGENCE, MAIN, SCOPES, candidate_status, in_scope


class LiveMarketObservationTests(unittest.TestCase):
    def stock(self, fundamental=True, rank=True, divergence=False):
        reasons = [] if fundamental else ["TTM營收成長低於10%"]
        return {
            "key": "TW:2330", "symbol": "2330",
            "integrity": True, "sourceError": None, "marketEvent": None,
            "financial": {"financial_status": "ok"},
            "researchRank": {"rank": 1, "divergence": divergence} if rank else None,
            "evaluation": {
                "fundamental": fundamental, "fundamentalReasons": reasons,
                "tradable": True, "fresh": True, "divergence": divergence,
                # Not verified for prior market-close, intentionally.
                "filingVerified": False, "primary": False, "buy_hold": False,
            },
        }

    def test_only_three_market_scopes(self):
        self.assertEqual(SCOPES, [MAIN, DIVERGENCE, ALL])

    def test_latest_observed_financial_allows_live_entry_review(self):
        stock = self.stock()
        state = candidate_status(stock, {"decision": "non_red"}, True)
        self.assertTrue(state["buy_ready"])
        self.assertEqual(state["entry_label"], "符合進場條件")
        self.assertTrue(in_scope(stock, state, MAIN, True))

    def test_unknown_funding_never_looks_like_buy(self):
        stock = self.stock()
        state = candidate_status(stock, {"decision": "unknown"}, True)
        self.assertFalse(state["buy_ready"])
        self.assertIn("不新增", state["entry_label"])

    def test_financial_deterioration_flags_holding_review_without_sell_order(self):
        stock = self.stock(fundamental=False, rank=False)
        state = candidate_status(stock, {"decision": "non_red"}, True)
        self.assertEqual(state["exit_label"], "基本面轉弱・檢查出場")
        self.assertFalse(state["buy_ready"])
        self.assertTrue(in_scope(stock, state, MAIN, True))
        self.assertIn("TTM營收成長低於10%", state["reasons"])

    def test_red_funding_blocks_new_entry_and_flags_exit_review(self):
        stock = self.stock()
        state = candidate_status(stock, {"decision": "red"}, True)
        self.assertFalse(state["buy_ready"])
        self.assertIn("紅燈", state["exit_label"])

    def test_divergence_is_observation_not_formal_order(self):
        stock = self.stock(divergence=True)
        state = candidate_status(stock, {"decision": "unknown"}, True)
        self.assertTrue(in_scope(stock, state, DIVERGENCE, True))
        self.assertFalse(state["buy_ready"])

    def test_unusable_batch_cannot_create_entry(self):
        stock = self.stock()
        state = candidate_status(stock, {"decision": "non_red"}, False)
        self.assertFalse(state["buy_ready"])
        self.assertEqual(state["entry_label"], "資料待驗證")


if __name__ == "__main__":
    unittest.main()
