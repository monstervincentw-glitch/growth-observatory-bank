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
BASELINE_HEADERS = ["id", "recorded_at", "portfolio_id", "ledger_id", "symbol", "trade_date",
                    "revenue_growth", "operating_margin", "filing_period", "official_published_at",
                    "source", "version_id", "verification", "detail"]
EXIT_TRANSITION_HEADERS = ["id", "recorded_at", "portfolio_id", "market", "symbol", "condition",
                           "state", "first_triggered_at", "resolved_at", "signal_date", "batch", "detail"]
CHANGE_HEADERS = ["id", "recorded_at", "portfolio_id", "field", "before", "after", "note"]


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
        # One metadata request plus one batched header request. Previously the
        # eight worksheet lookups and eight header reads could exhaust the
        # service-account's per-minute read quota during Render cold starts.
        definitions = (
            ("portfolios_tab", "portfolios", PORTFOLIO_HEADERS),
            ("ledger_tab", "ledger", LEDGER_HEADERS),
            ("events_tab", "events", EVENT_HEADERS),
            ("nav_tab", "nav_history", NAV_HEADERS),
            ("positions_snapshot_tab", "position_snapshots", POSITION_SNAPSHOT_HEADERS),
            ("baseline_tab", "entry_baselines", BASELINE_HEADERS),
            ("exit_transition_tab", "exit_transitions", EXIT_TRANSITION_HEADERS),
            ("change_tab", "portfolio_changes", CHANGE_HEADERS),
        )
        available = {sheet.title: sheet for sheet in self.book.worksheets()}
        existing = [name for _, name, _ in definitions if name in available]
        checks = self.book.values_batch_get([f"'{name}'!1:1" for name in existing]) if existing else {"valueRanges": []}
        returned = checks.get("valueRanges", [])
        if len(returned) != len(existing):
            raise RuntimeError("私人工作表標頭檢查未完整回傳，已停止操作")
        actual_headers = {name: (item.get("values") or [[]])[0]
                          for name, item in zip(existing, returned)}
        for attribute, name, headers in definitions:
            setattr(self, attribute, self._tab(name, headers, available, actual_headers))

    def _tab(self, name, headers, available, actual_headers):
        sheet = available.get(name)
        if sheet is None:
            sheet = self.book.add_worksheet(title=name, rows=2000, cols=len(headers))
            sheet.append_row(headers, value_input_option="RAW")
        elif actual_headers.get(name) != headers:
            raise ValueError(f"{name} 欄位與預期不符，已停止寫入以保護現有資料")
        return sheet

    @staticmethod
    def _decode_rows(rows, headers):
        return [dict(zip(headers, [*row, *([""] * (len(headers)-len(row)))]))
                for row in rows if row and row[0]]

    @classmethod
    def _rows(cls, sheet, headers):
        return cls._decode_rows(sheet.get_all_values()[1:], headers)

    def read_snapshot(self):
        """Read five owner-only private tables in one quota-counted API request.

        This is strictly read-only. Keep the exact same header/row semantics as
        the existing individual readers, and never log or persist row contents.
        """
        with self._lock:
            definitions = (
                ("portfolios", "portfolios", PORTFOLIO_HEADERS),
                ("transactions", "ledger", LEDGER_HEADERS),
                ("baselines", "entry_baselines", BASELINE_HEADERS),
                ("transitions", "exit_transitions", EXIT_TRANSITION_HEADERS),
                ("nav", "nav_history", NAV_HEADERS),
            )
            ranges = [
                f"'{name}'!A2:{gspread.utils.rowcol_to_a1(1, len(headers)).rstrip('0123456789')}"
                for _, name, headers in definitions
            ]
            response = self.book.values_batch_get(ranges)
            results = response.get("valueRanges", [])
            if len(results) != len(definitions):
                raise RuntimeError("私人資料批次讀取不完整，已停止分析及寫入")
            output = {
                key: self._decode_rows(part.get("values") or [], headers)
                for (key, _name, headers), part in zip(definitions, results)
            }
            output["nav"].sort(key=lambda row: (row["date"], row["portfolio_id"]))
            return output

    def all_portfolios(self):
        with self._lock:
            return self._rows(self.portfolios_tab, PORTFOLIO_HEADERS)

    def portfolios(self):
        return [row for row in self.all_portfolios() if str(row.get("archived") or "").lower() != "true"]

    def archived_portfolios(self):
        return [row for row in self.all_portfolios() if str(row.get("archived") or "").lower() == "true"]

    def portfolio_changes(self):
        with self._lock:
            return self._rows(self.change_tab, CHANGE_HEADERS)

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

    def entry_baselines(self):
        with self._lock:
            return self._rows(self.baseline_tab, BASELINE_HEADERS)

    def append_entry_baselines(self, rows):
        """Append audit baselines. Never updates an existing ledger cell."""
        from entry_basis import OFFICIAL
        with self._lock:
            saved = self.entry_baselines()
            known = {(row["portfolio_id"], row["ledger_id"], row["version_id"]) for row in saved}
            now = utc_now()
            fresh = []
            for row in rows:
                version = str(row.get("version_id") or "")
                key = (str(row.get("portfolio_id")), str(row.get("ledger_id")), version)
                if not version or key in known:
                    continue
                if row.get("verification") == OFFICIAL:
                    from core import money
                    money(row.get("revenue_growth"))
                    money(row.get("operating_margin"))
                record = dict.fromkeys(BASELINE_HEADERS, "")
                record.update({
                    "id": str(uuid.uuid4()),
                    "recorded_at": now,
                    "portfolio_id": str(row.get("portfolio_id") or ""),
                    "ledger_id": str(row.get("ledger_id") or ""),
                    "symbol": str(row.get("symbol") or "").upper(),
                    "trade_date": str(row.get("trade_date") or "")[:10],
                    "revenue_growth": str(row.get("revenue_growth") or ""),
                    "operating_margin": str(row.get("operating_margin") or ""),
                    "filing_period": str(row.get("filing_period") or ""),
                    "official_published_at": str(row.get("official_published_at") or "")[:10],
                    "source": str(row.get("source") or "")[:300],
                    "version_id": version,
                    "verification": str(row.get("verification") or ""),
                    "detail": str(row.get("detail") or "")[:300],
                })
                if record["verification"] not in (OFFICIAL, "UNVERIFIED"):
                    raise ValueError("建倉基準驗證狀態無效")
                fresh.append(record)
                known.add(key)
            if not fresh:
                return []
            self.baseline_tab.append_rows(
                [[record[k] for k in BASELINE_HEADERS] for record in fresh],
                value_input_option="RAW")
            return fresh

    def exit_transitions(self):
        with self._lock:
            return self._rows(self.exit_transition_tab, EXIT_TRANSITION_HEADERS)

    def append_exit_transitions(self, current):
        """Record first trigger and later resolution. Does not edit old rows."""
        from monitor import condition_transitions
        with self._lock:
            history = self.exit_transitions()
            changes = condition_transitions(current, history)
            if not changes:
                return []
            now = utc_now()
            rows = []
            for change in changes:
                record = dict.fromkeys(EXIT_TRANSITION_HEADERS, "")
                record.update(change)
                record["id"] = str(uuid.uuid4())
                record["recorded_at"] = now
                if record["state"] == "open":
                    record["first_triggered_at"] = now
                elif record["state"] == "resolved":
                    record["resolved_at"] = now
                rows.append(record)
            self.exit_transition_tab.append_rows(
                [[record[k] for k in EXIT_TRANSITION_HEADERS] for record in rows],
                value_input_option="RAW")
            return rows

    def create_portfolio(self, *, name, market, strategy, currency, note=""):
        from portfolio_admin import validate_new_portfolio
        fields = validate_new_portfolio(name=name, market=market, strategy=strategy,
                                        currency=currency, note=note)
        entry = {"id": str(uuid.uuid4()), "created_at": utc_now(), **fields}
        with self._lock:
            self.portfolios_tab.append_row([entry[k] for k in PORTFOLIO_HEADERS],
                                           value_input_option="RAW")
        return entry

    def _portfolio_record(self, portfolio_id, *, include_archived):
        found = next((row for row in self.all_portfolios() if row["id"] == str(portfolio_id)), None)
        if found is None:
            raise ValueError("找不到組合，禁止寫入")
        archived = str(found.get("archived") or "").lower() == "true"
        if archived and not include_archived:
            raise ValueError("組合已封存")
        return found

    def _apply_portfolio_changes(self, portfolio_id, changes, note):
        """Update portfolio metadata cells only. Never touches the trade ledger."""
        if not changes:
            return []
        allowed = {"name", "note", "strategy", "archived"}
        with self._lock:
            rows = self.portfolios_tab.get_all_values()
            found = None
            current = None
            for index, row in enumerate(rows[1:], start=2):
                if row and row[0] == str(portfolio_id):
                    found = index
                    current = row
                    break
            if found is None or current[0] != str(portfolio_id):
                raise ValueError("找不到組合，禁止寫入")
            for field, _before, after in changes:
                if field not in allowed:
                    raise ValueError("這個欄位不能從組合管理修改")
                column = PORTFOLIO_HEADERS.index(field) + 1
                self.portfolios_tab.update_cell(found, column, after)
            now = utc_now()
            records = []
            for field, before, after in changes:
                record = {
                    "id": str(uuid.uuid4()),
                    "recorded_at": now,
                    "portfolio_id": str(portfolio_id),
                    "field": field,
                    "before": str(before),
                    "after": str(after),
                    "note": str(note or "")[:300],
                }
                records.append(record)
            self.change_tab.append_rows(
                [[record[k] for k in CHANGE_HEADERS] for record in records],
                value_input_option="RAW")
            return records

    def update_portfolio_profile(self, portfolio_id, *, name, note):
        from portfolio_admin import plan_profile_update
        existing = self._portfolio_record(portfolio_id, include_archived=False)
        changes = plan_profile_update(existing, name=name, note=note)
        return self._apply_portfolio_changes(portfolio_id, changes, "修改名稱或備註，不改識別碼與交易")

    def update_portfolio_strategy(self, portfolio_id, new_strategy, *, confirmed):
        from portfolio_admin import plan_strategy_change
        existing = self._portfolio_record(portfolio_id, include_archived=False)
        change = plan_strategy_change(existing, new_strategy, confirmed=confirmed)
        if change is None:
            return []
        return self._apply_portfolio_changes(
            portfolio_id, [change], "策略變更不改股數、成本、交易與淨值")

    def archive_portfolio(self, portfolio_id, *, confirmed):
        from portfolio_admin import plan_archive
        existing = self._portfolio_record(portfolio_id, include_archived=False)
        change = plan_archive(existing, confirmed=confirmed)
        return self._apply_portfolio_changes(
            portfolio_id, [change], "封存，不刪除交易、淨值與建倉基準")

    def restore_portfolio(self, portfolio_id):
        from portfolio_admin import plan_restore
        existing = self._portfolio_record(portfolio_id, include_archived=True)
        change = plan_restore(existing)
        return self._apply_portfolio_changes(portfolio_id, [change], "恢復封存組合")

    def delete_portfolio(self, portfolio_id):
        from portfolio_admin import permanent_delete_refused
        permanent_delete_refused()

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
