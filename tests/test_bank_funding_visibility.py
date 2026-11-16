"""Presentation regression checks: capital/funding risk status remains prominent."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FundingVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")
        cls.layout = (ROOT / "bank_layout.py").read_text(encoding="utf-8")
        cls.css = (ROOT / "bank_layout.css").read_text(encoding="utf-8")

    def test_funding_is_only_at_top_of_market_observation(self):
        shell = self.app.split('with st.container(key="bank-content"):', 1)[1]
        # The common page shell is shared by retrospective pages and must not
        # render the current funding signal.
        self.assertNotIn("funding_dashboard(", shell)
        market_page = self.app.split("def market_page():", 1)[1].split("def advisor_page():", 1)[0]
        self.assertLess(market_page.index('funding_dashboard(data["markets"][market]["macro"])'),
                        market_page.index("update_status(state,quote)"))
        self.assertLess(market_page.index('funding_dashboard(data["markets"][market]["macro"])'),
                        market_page.index("markets(market, block,"))
        self.assertEqual(self.app.count("funding_dashboard("), 1)
        self.assertIn('if data is not None:', market_page)
        self.assertIn('st.warning("全球資金溫度資料目前無法驗證', market_page)

    def test_backtest_and_advisor_have_no_live_funding_strip(self):
        for start, end in [
            ("def backtest(research, nav):", "def legacy_backtest(market):"),
            ("def tracking(snapshot_data, baseline):", "def performance_reader():"),
            ("def advisor(research, research_close=None):", "def runner_attempts():"),
            ("def advisor_page():", "layout_style()"),
        ]:
            section = self.app.split(start, 1)[1].split(end, 1)[0]
            self.assertNotIn("funding_dashboard(", section)

    def test_diagnostics_do_not_hide_or_duplicate_funding_cards(self):
        market_segment = self.app.split("def markets(", 1)[1].split("def market_picker(", 1)[0]
        self.assertNotIn("funding_dashboard(", market_segment)
        self.assertIn('if section == "資金數據":\n        funding_details(macro)', market_segment)

    def test_published_overall_status_and_three_categories(self):
        self.assertIn('"red": ("triggered", "整體紅燈")', self.layout)
        self.assertIn('"non_red": ("clear", "整體非紅燈")', self.layout)
        self.assertIn('"unknown": ("unknown", "整體待確認")', self.layout)
        for name in ("利率", "金融條件", "通膨"):
            self.assertIn(f'"{name}"', self.layout)
        self.assertIn(".bank-funding-overall-triggered", self.css)


if __name__ == "__main__":
    unittest.main()
