"""Regression checks for quota-efficient, fail-closed private Sheets reads.

All sheet cells and credentials are synthetic. No external API calls are made.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portfolio_portal"))
from storage import (  # noqa: E402
    PortfolioSheets, PORTFOLIO_HEADERS, LEDGER_HEADERS, EVENT_HEADERS,
    NAV_HEADERS, POSITION_SNAPSHOT_HEADERS, BASELINE_HEADERS,
    EXIT_TRANSITION_HEADERS, CHANGE_HEADERS,
)

DEFINITIONS = {
    "portfolios": PORTFOLIO_HEADERS,
    "ledger": LEDGER_HEADERS,
    "events": EVENT_HEADERS,
    "nav_history": NAV_HEADERS,
    "position_snapshots": POSITION_SNAPSHOT_HEADERS,
    "entry_baselines": BASELINE_HEADERS,
    "exit_transitions": EXIT_TRANSITION_HEADERS,
    "portfolio_changes": CHANGE_HEADERS,
}


class FakeWorksheet:
    def __init__(self, title):
        self.title = title
        self.appended = []

    def append_row(self, row, **kwargs):
        self.appended.append(list(row))


class FakeBook:
    def __init__(self):
        self.tabs = {title: FakeWorksheet(title) for title in DEFINITIONS}
        self.headers = {title: list(columns) for title, columns in DEFINITIONS.items()}
        self.data = {title: [] for title in DEFINITIONS}
        self.metadata_reads = 0
        self.batch_calls = []
        self.created = []

    def worksheets(self):
        self.metadata_reads += 1
        return list(self.tabs.values())

    def values_batch_get(self, ranges):
        self.batch_calls.append(list(ranges))
        if all(r.endswith("!1:1") for r in ranges):
            return {"valueRanges": [
                {"values": [self.headers[r.split("'")[1]]]} for r in ranges
            ]}
        return {"valueRanges": [
            {"values": self.data[r.split("'")[1]]} for r in ranges
        ]}

    def add_worksheet(self, title, rows, cols):
        self.created.append(title)
        sheet = FakeWorksheet(title)
        self.tabs[title] = sheet
        return sheet


class FakeClient:
    def __init__(self, book):
        self.book = book

    def open_by_key(self, _spreadsheet_id):
        return self.book


class PrivateSheetQuotaTests(unittest.TestCase):
    def setUp(self):
        self.book = FakeBook()

    def make_store(self):
        return PortfolioSheets(FakeClient(self.book), "synthetic-private-sheet-id")

    def test_startup_validates_eight_headers_with_one_batch(self):
        self.make_store()
        self.assertEqual(self.book.metadata_reads, 1)
        self.assertEqual(len(self.book.batch_calls), 1)
        self.assertEqual(len(self.book.batch_calls[0]), 8)
        self.assertEqual(self.book.created, [])

    def test_owner_snapshot_fetches_five_tables_in_one_request(self):
        book = self.book
        book.data["portfolios"] = [["p1", "Example"], ["", "ignore"]]
        book.data["ledger"] = [["t1", "p1", "at"]]
        book.data["entry_baselines"] = [["b1", "at", "p1"]]
        book.data["exit_transitions"] = [["e1", "at", "p1"]]
        book.data["nav_history"] = [
            ["p1", "2026-11-16", "130"],
            ["p1", "2026-11-15", "120"],
        ]
        store = self.make_store()
        snapshot = store.read_snapshot()
        self.assertEqual(len(book.batch_calls), 2)
        self.assertEqual(len(book.batch_calls[-1]), 5)
        self.assertEqual(book.batch_calls[-1][0], "'portfolios'!A2:H")
        self.assertEqual(book.batch_calls[-1][1], "'ledger'!A2:O")
        self.assertEqual(snapshot["portfolios"][0]["name"], "Example")
        self.assertEqual(len(snapshot["portfolios"]), 1)
        self.assertEqual(snapshot["transactions"][0]["id"], "t1")
        self.assertEqual(snapshot["baselines"][0]["id"], "b1")
        self.assertEqual(snapshot["transitions"][0]["id"], "e1")
        self.assertEqual([row["date"] for row in snapshot["nav"]],
                         ["2026-11-15", "2026-11-16"])

    def test_missing_sheet_bootstraps_only_that_sheet(self):
        del self.book.tabs["portfolio_changes"]
        store = self.make_store()
        self.assertEqual(self.book.created, ["portfolio_changes"])
        self.assertEqual(store.change_tab.appended, [CHANGE_HEADERS])

    def test_schema_mismatch_fails_closed_without_writing(self):
        self.book.headers["ledger"][0] = "wrong"
        with self.assertRaisesRegex(ValueError, "ledger"):
            self.make_store()
        self.assertEqual(self.book.created, [])

    def test_incomplete_batch_read_fails_closed(self):
        class IncompleteBook(FakeBook):
            def values_batch_get(self, ranges):
                response = super().values_batch_get(ranges)
                if ranges and ranges[0].endswith("A2:H"):
                    response["valueRanges"].pop()
                return response
        book = IncompleteBook()
        with self.assertRaisesRegex(RuntimeError, "批次讀取不完整"):
            PortfolioSheets(FakeClient(book), "synthetic-private-sheet-id").read_snapshot()


if __name__ == "__main__":
    unittest.main()
