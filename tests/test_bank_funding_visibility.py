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

    def test_global_funding_before_page_content(self):
        shell = self.app.split('with st.container(key="bank-content"):', 1)[1]
        self.assertLess(shell.index("header(page.title)"),
                        shell.index('funding_dashboard(data["markets"][funding_market]["macro"])'))
        self.assertLess(shell.index('funding_dashboard(data["markets"][funding_market]["macro"])'),
                        shell.index("page.run()"))
        self.assertEqual(self.app.count("funding_dashboard("), 1)

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
