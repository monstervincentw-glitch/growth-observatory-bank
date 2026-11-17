import unittest

from performance.backtest_window import available_end_year, cohort_window


NAV = {
    "TW": {
        "2018": [
            ["2018-01-31", 1.10, .95],
            ["2018-12-31", 1.20, 1.05],
            ["2019-12-31", 1.50, 1.40],
            ["2020-11-04", 1.45, 1.42],
        ],
        "2019": [
            ["2019-02-01", 1.11, 1.10],
            ["2019-12-31", 1.25, 1.30],
            ["2020-11-04", 1.35, 1.40],
        ],
    }
}
LIVE = {
    "market": "TW",
    "through": "2020-11-06",
    "curves": {
        "TW_2018_A": [["2020-11-05", 1.46], ["2020-11-06", 1.48]],
        "TW_2018_B": [["2020-11-05", 1.43], ["2020-11-06", 1.45]],
        "TW_2019_A": [["2020-11-05", 1.36], ["2020-11-06", 1.37]],
        "TW_2019_B": [["2020-11-05", 1.41], ["2020-11-06", 1.42]],
    },
}


class BacktestWindowTest(unittest.TestCase):
    def test_end_year_clips_curve_and_recalculates_return(self):
        frame, stats, through = cohort_window(NAV, LIVE, "TW", 2018, 2018)
        self.assertEqual(through, "2018-12-31")
        self.assertEqual(frame["日期"].dt.year.max(), 2018)
        self.assertEqual(frame["日期"].dt.date.iloc[0].isoformat(), "2018-01-01")
        self.assertAlmostEqual(stats["A"]["cumulative"], .20)
        self.assertAlmostEqual(stats["B"]["sampled_drawdown"], -.05)
        self.assertEqual(len(frame), 3)

    def test_same_cohort_different_end_year_has_different_performance(self):
        earlier, metrics, end = cohort_window(NAV, LIVE, "TW", 2018, 2019)
        self.assertEqual(end, "2019-12-31")
        self.assertAlmostEqual(metrics["A"]["cumulative"], .50)
        self.assertAlmostEqual(metrics["B"]["cumulative"], .40)
        self.assertEqual(len(earlier), 4)

    def test_recent_daily_continuation_is_appended_once(self):
        frame, stats, end = cohort_window(NAV, LIVE, "TW", 2018, 2020)
        self.assertEqual(end, "2020-11-06")
        self.assertEqual(len(frame), 7)
        self.assertAlmostEqual(stats["A"]["cumulative"], .48)
        self.assertAlmostEqual(stats["B"]["cumulative"], .45)
        self.assertEqual(available_end_year(NAV, "TW", LIVE), 2020)

    def test_start_year_changes_selected_cohort(self):
        frame, metrics, end = cohort_window(NAV, LIVE, "TW", 2019, 2019)
        self.assertEqual(end, "2019-12-31")
        self.assertAlmostEqual(metrics["A"]["cumulative"], .25)
        self.assertEqual(frame["日期"].dt.year.min(), 2019)

    def test_end_before_cohort_start_refused(self):
        with self.assertRaisesRegex(ValueError, "不可早於"):
            cohort_window(NAV, LIVE, "TW", 2019, 2018)

    def test_conflicting_overlapping_live_nav_refused(self):
        amended = {**LIVE, "curves": {**LIVE["curves"],
                   "TW_2018_A": [["2020-11-04", 12.0]]}}
        with self.assertRaisesRegex(ValueError, "衝突"):
            cohort_window(NAV, amended, "TW", 2018, 2020)

    def test_no_after_end_rows_leak_into_download(self):
        frame, _, _ = cohort_window(NAV, LIVE, "TW", 2018, 2019)
        self.assertNotIn("2020-11-06", frame["日期"].dt.strftime("%Y-%m-%d").tolist())


if __name__ == "__main__":
    unittest.main()
