"""Google Sheets as owner-controlled, append-only private portfolio ledger.

Only call after Streamlit's Google OIDC owner guard. No credentials in source.
Google Sheets is not a transactional database: this single-owner MVP rejects
invalid writes and duplicate IDs, and refreshes/revalidates after appends.
"""
from __future__ import annotations
from datetime import datetime, timezone
import threading
import uuid

import gspread
from gspread.exceptions import WorksheetNotFound

PORTFOLIO_HEADERS = ["id", "name", "market", "strategy", "currency", "created_at", "note", "archived"]
BANK_RESEARCH_SHEET_ID = "1I9Alvyhg4UZXi-AdylJN0pjiLHSMzZUleFK0vsXG1K0"
LEDGER_HEADERS = ["id", "portfolio_id", "at", "trade_date", "action", "symbol", "quantity", "price", "fee",
                  "cash_amount", "role", "entry_growth", "entry_margin", "entry_filing_period", "note"]
EVENT_HEADERS = ["id", "at", "portfolio_id", "market", "symbol", "strategy", "signal_date",
                 "batch", "status", "reasons", "unknown", "financial_period", "observed_at"]
NAV_HEADERS = ["portfolio_id", "date", "nav", "cash", "equity_value", "liabilities", "daily_pnl",
               "daily_return", "cumulative_pnl", "twr", "source", "note"]
POSITION_SNAPSHOT_HEADERS = ["portfolio_id", "date", "symbol", "name", "quantity", "avg_cost",
                             "cost_total", "market_value", "unrealized_pnl", "source", "note"]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sheets_client(service_account_info):
    return gspread.service_account_from_dict(dict(service_account_info))


class PortfolioSheets:
    def __init__(self, client, spreadsheet_id):
        if not spreadsheet_id:
            raise ValueError("Missing PRIVATE_PORTFOLIO_SHEET_ID")
        if spreadsheet_id == BANK_RESEARCH_SHEET_ID:
            raise ValueError("安全防護：私人持股資料不得寫入銀行版共用研究試算表")
        self.client = client
        self.book = client.open_by_key(spreadsheet_id)
        self._lock = threading.RLock()
        self.portfolios_tab = self._tab("portfolios", PORTFOLIO_HEADERS)
        self.ledger_tab = self._tab("ledger", LEDGER_HEADERS)
        self.events_tab = self._tab("events", EVENT_HEADERS)
        self.nav_tab = self._tab("nav_history", NAV_HEADERS)
        self.positions_snapshot_tab = self._tab("position_snapshots", POSITION_SNAPSHOT_HEADERS)

    def _tab(self, name, headers):
        try:
            sheet = self.book.worksheet(name)
        except WorksheetNotFound:
            sheet = self.book.add_worksheet(title=name, rows=2000, cols=len(headers))
            sheet.append_row(headers, value_input_option="RAW")
        got = sheet.row_values(1)
        if got != headers:
            raise ValueError(f"{name} 欄位與預期不符，已停止寫入以保護現有資料")
        return sheet

    @staticmethod
    def _rows(sheet, headers):
        rows = sheet.get_all_values()[1:]
        return [dict(zip(headers, [*row, *([""] * (len(headers)-len(row)))]))
                for row in rows if row and row[0]]

    def portfolios(self):
        with self._lock:
            return [r for r in self._rows(self.portfolios_tab, PORTFOLIO_HEADERS)
                    if r["archived"].lower() != "true"]

    def transactions(self):
        with self._lock:
            return self._rows(self.ledger_tab, LEDGER_HEADERS)

    def historical_positions(self, portfolio_id):
        """Owner-confirmed per-ticker closing snapshot, distinct from live quotes."""
        with self._lock:
            rows=self._rows(self.positions_snapshot_tab, POSITION_SNAPSHOT_HEADERS)
            return sorted((r for r in rows if r["portfolio_id"]==str(portfolio_id)),
                          key=lambda r:(r["date"],r["symbol"]))

    def nav_snapshots(self, portfolio_id=None):
        """Verified account NAV observations, separately from the trade ledger.

        Historical manual source rows are immutable. Never backfill a missing
        daily close with a guessed value from account cost or later quotes.
        """
        with self._lock:
            rows=self._rows(self.nav_tab, NAV_HEADERS)
            if portfolio_id is not None:
                rows=[r for r in rows if r["portfolio_id"] == str(portfolio_id)]
            return sorted(rows,key=lambda r:(r["date"],r["portfolio_id"]))

    def record_nav_snapshot(self, *, portfolio_id, session_date, nav, cash,
                            equity_value, source="銀行版核驗行情"):
        """Idempotently record one verified close per portfolio and trading date.

        Don't replace a historical screenshot observation. A true correction
        requires a separate owner-audited reconciliation workflow.
        """
        from datetime import date
        from core import money, nav_extension
        at_date=date.fromisoformat(str(session_date))
        if at_date>date.today():
            raise ValueError("禁止寫入未來行情")
        nav,money_cash,equity=money(nav),money(cash),money(equity_value)
        if nav<0 or equity<0 or money_cash<0 or abs(nav-money_cash-equity)>money("0.01"):
            raise ValueError("NAV、現金與股票市值無法對帳")
        with self._lock:
            if portfolio_id not in {r["id"] for r in self.portfolios()}:
                raise ValueError("私人組合不存在")
            existing=self.nav_snapshots(portfolio_id)
            extra=nav_extension(self.transactions(), portfolio_id, existing,
                                str(session_date), nav, money_cash, equity)
            if extra is None:
                return False
            record=dict.fromkeys(NAV_HEADERS,"")
            record.update({
                "portfolio_id":str(portfolio_id),
                "date":str(session_date),
                "nav":str(nav),
                "cash":str(money_cash),
                "equity_value":str(equity),
                "liabilities":"0",
                "daily_pnl":extra["daily_pnl"],
                "daily_return":extra["daily_return"],
                "cumulative_pnl":extra["cumulative_pnl"],
                "twr":extra["twr"],
                "source":str(source),
                "note":extra["note"],
            })
            self.nav_tab.append_row([record[k] for k in NAV_HEADERS],value_input_option="RAW")
            if sum(r["date"]==str(session_date) for r in self.nav_snapshots(portfolio_id))!=1:
                raise RuntimeError("NAV 寫入回讀驗證失敗，請手動核對")
            return True

    def monitor_history(self):
        with self._lock:
            return self._rows(self.events_tab, EVENT_HEADERS)

    def append_monitor_results(self, current):
        """Append changed risks only; do not modify user's trade ledger."""
        from monitor import changed_events
        with self._lock:
            previous=self.monitor_history()
            changes=changed_events(current,previous)
            if not changes:
                return []
            now=utc_now()
            rows=[]
            for event in changes:
                record={"id":str(uuid.uuid4()),"at":now,**event}
                if any(record["id"]==r["id"] for r in previous):
                    raise ValueError("Duplicate monitoring event")
                rows.append(record)
            self.events_tab.append_rows(
                [[record.get(k,"") for k in EVENT_HEADERS] for record in rows],
                value_input_option="RAW")
            saved={record["id"] for record in self.monitor_history()}
            if not all(record["id"] in saved for record in rows):
                raise RuntimeError("監控歷史寫入確認失敗，請核對 Google Sheets")
            return rows

    def create_portfolio(self, *, name, market, strategy, currency, note=""):
        if not str(name).strip() or len(name)>80:
            raise ValueError("請輸入有效的組合名稱")
        if market not in ("US","TW") or strategy not in ("A","B","背離長抱","自訂"):
            raise ValueError("策略或市場不支援")
        if currency != ("USD" if market=="US" else "TWD"):
            raise ValueError("組合幣別不符合交易市場")
        entry = {"id":str(uuid.uuid4()),"name":name.strip(),"market":market,
                 "strategy":strategy,"currency":currency,"created_at":utc_now(),
                 "note":note.strip()[:300],"archived":""}
        with self._lock:
            self.portfolios_tab.append_row([entry[k] for k in PORTFOLIO_HEADERS],
                                           value_input_option="RAW")
        return entry

    def append_transaction(self, *, portfolio_id, action, trade_date="", symbol="", quantity="",
                           price="", fee="", cash_amount="", role="core",
                           entry_growth="", entry_margin="", entry_filing_period="", note=""):
        from datetime import date
        from core import compute_account, money
        for label, value in (("進場營收基準", entry_growth), ("進場營益率基準", entry_margin)):
            if value not in ("", None):
                money(value)  # reject malformed/nan baselines before appending
        if not trade_date:
            trade_date = date.today().isoformat()
        parsed = date.fromisoformat(trade_date)
        if parsed > date.today():
            raise ValueError("交易日期不能在未來")
        with self._lock:
            portfolios={r["id"]:r for r in self.portfolios()}
            if portfolio_id not in portfolios:
                raise ValueError("找不到組合，禁止寫入")
            if action not in ("DEPOSIT","WITHDRAW","BUY","SELL"):
                raise ValueError("交易動作無效")
            symbol=str(symbol).strip().upper()
            if action in ("BUY","SELL") and (not symbol or not symbol.replace("-","").replace(".","").isalnum()):
                raise ValueError("股票代號無效")
            if role not in ("core","extra"):
                raise ValueError("持股分類無效")
            if action == "BUY" and role == "extra" and portfolios[portfolio_id]["strategy"] != "B":
                raise ValueError("只有 B 策略可以記錄額外持股")
            record = {"id":str(uuid.uuid4()),"portfolio_id":portfolio_id,"at":utc_now(),
                      "trade_date":trade_date,"action":action,"symbol":symbol,"quantity":str(quantity or ""),
                      "price":str(price or ""),"fee":str(fee or "0"),
                      "cash_amount":str(cash_amount or ""),
                      "role":role,"entry_growth":str(entry_growth or ""),
                      "entry_margin":str(entry_margin or ""),
                      "entry_filing_period":str(entry_filing_period or ""),
                      "note":str(note or "")[:300]}
            existing=self.transactions()
            if any(x["id"]==record["id"] for x in existing):
                raise ValueError("重複交易識別碼")
            # Reject oversell, negative cash, invalid numbers before any mutation.
            compute_account([*existing,record], portfolio_id)
            self.ledger_tab.append_row([record[k] for k in LEDGER_HEADERS],
                                       value_input_option="RAW")
            saved = self.transactions()
            if sum(x["id"]==record["id"] for x in saved)!=1:
                raise RuntimeError("Google Sheet 寫入確認失敗，請先查詢交易紀錄，勿重複提交")
            return record
