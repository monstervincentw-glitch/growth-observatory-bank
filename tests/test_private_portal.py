"""Owner-portfolio rules. No network, no sheets, no secrets, no seeded trades."""
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portfolio_portal"))

from core import (  # noqa: E402
    buy_plan, compute_account, exit_review, funding_decision, modified_dietz, nav_extension, owner_allowed, portfolio_value,
)
from entry_basis import (  # noqa: E402
    apply_official_baselines, late_observation_reason, missing_baseline_rows, sec_financial,
)
from monitor import changed_events, condition_transitions, data_too_old  # noqa: E402
from oauth_messages import oauth_error_code, oauth_error_message  # noqa: E402
from portfolio_admin import (  # noqa: E402
    condition_timeline, permanent_delete_refused, plan_archive, plan_profile_update,
    plan_strategy_change, validate_new_portfolio, weekday_gaps,
)
from render_entrypoint import BANK_SHEET_ID, create_auth_config, streamlit_env, taipei_slot  # noqa: E402


def tx(action, **extra):
    row = {"id": extra.pop("id", action), "portfolio_id": "p", "at": extra.pop("at", "2026-11-11T00:00:00+00:00"),
           "trade_date": "2026-11-11", "action": action, "symbol": "", "quantity": "", "price": "",
           "fee": "0", "cash_amount": "", "role": "core", "entry_growth": "0.40", "entry_margin": "0.20"}
    row.update(extra)
    return row


def stock(symbol, score, rank, *, primary=True, buy_hold=False, reasons=None, growth="0.40",
          margin="0.20", age=30, close="100"):
    return {
        "symbol": symbol, "name": symbol, "integrity": True, "sourceError": None, "marketEvent": None,
        "close": close, "priceDate": "2026-11-13",
        "financial": {"financial_status": "ok", "revenue_growth": growth, "operating_margin": margin,
                      "financial_age_days": age, "latest_period": "2026-09-30"},
        "financialObservedAt": "2026-11-01",
        "evaluation": {"fundamental": True, "tradable": True, "fresh": True, "filingVerified": True,
                       "funding": True, "primary": primary, "buy_hold": buy_hold,
                       "fundamentalReasons": reasons or []},
        "researchRank": {"score": score, "rank": rank},
    }


def market(stocks, decision="non_red"):
    return {"macro": {"decision": decision, "marketAsOf": "2026-11-13", "status": "vintage_verified"},
            "stocks": stocks}


def valued_from(cash, nav_extra=0):
    nav = Decimal(cash) + Decimal(nav_extra)
    return {"cash": Decimal(cash), "nav": nav, "market_value": Decimal(nav_extra), "missing_prices": []}


def test_owner_gate_rejects_unverified_and_other_accounts():
    assert owner_allowed("Me@Gmail.com", True, "me@gmail.com")
    assert owner_allowed("me@gmail.com", "true", "me@gmail.com")
    assert not owner_allowed("me@gmail.com", False, "me@gmail.com")
    assert not owner_allowed("other@gmail.com", True, "me@gmail.com")
    assert not owner_allowed("", True, "me@gmail.com")
    assert owner_allowed("second@gmail.com", True, "me@gmail.com, second@gmail.com")


def test_account_identity_uses_a_synthetic_book():
    deposit = tx("DEPOSIT", cash_amount="1000")
    buy = tx("BUY", id="b", symbol="TEST", quantity="10", price="40", at="2026-11-11T01:00:00+00:00")
    account = compute_account([deposit, buy], "p")
    assert account["cash"] == Decimal("600")
    valued = portfolio_value(account, market([stock("TEST", 90, 1, close="30")]))
    assert valued["nav"] == Decimal("900")
    assert valued["nav"] - Decimal("1000") == Decimal("-100")


def test_partial_sell_and_negative_cash_rejected():
    rows = [tx("DEPOSIT", cash_amount="1000"),
            tx("BUY", id="b", symbol="AAA", quantity="10", price="50", at="2026-11-11T01:00:00+00:00")]
    sold = compute_account([*rows, tx("SELL", id="s", symbol="AAA", quantity="4", price="40",
                                      at="2026-11-13T00:00:00+00:00", trade_date="2026-11-13")], "p")
    assert sold["positions"]["AAA"] == Decimal("6")
    try:
        compute_account([*rows, tx("SELL", id="s", symbol="AAA", quantity="11", price="40")], "p")
    except ValueError as exc:
        assert "sell" in str(exc).lower()
    else:
        raise AssertionError("oversell was accepted")


def test_nav_links_known_closes_and_does_not_invent_the_gap():
    rows = [tx("DEPOSIT", cash_amount="1000")]
    points = [{"date": "2026-11-11", "nav": "990"}, {"date": "2026-11-13", "nav": "950"}]
    series = modified_dietz(rows, "p", points)
    assert [p["date"] for p in series] == ["2026-11-11", "2026-11-13"]
    assert "2026-11-12" not in {p["date"] for p in series}
    assert series[-1]["modified_dietz"] == Decimal("950") / Decimal("1000") - 1
    again = nav_extension(rows, "p", points, "2026-11-13", "950", "600", "350")
    assert again is None
    added = nav_extension(rows, "p", points, "2026-11-16", "900", "600", "300")
    assert added["cumulative_pnl"] == "-100"
    assert added["twr"] == ""
    assert "Modified Dietz" in added["note"]
    assert "不是標準 TWR" in added["note"]


def test_deposit_between_closes_is_not_counted_as_profit():
    rows = [tx("DEPOSIT", cash_amount="100", trade_date="2026-11-11"),
            tx("DEPOSIT", id="d2", cash_amount="50", trade_date="2026-11-13", at="2026-11-13T00:00:00+00:00")]
    points = [{"date": "2026-11-11", "nav": "100"}, {"date": "2026-11-13", "nav": "150"}]
    series = modified_dietz(rows, "p", points)
    assert series[-1]["modified_dietz"] == 0


def test_a_stops_at_ten_and_b_needs_a_strictly_higher_score():
    held = {f"C{i}": [{"role": "core", "quantity": Decimal("1")}] for i in range(10)}
    account = {"lots": held, "cash": Decimal("5000")}
    universe = [stock(f"C{i}", 50, i + 1) for i in range(10)]
    universe.append(stock("NEW", 50, 11))
    universe.append(stock("BETTER", 51, 0))
    # rank 0 sorts first; give BETTER a real better rank too
    universe[-1]["researchRank"]["rank"] = 1
    valued = valued_from("5000", "20000")
    plan_a = buy_plan(account, valued, market(universe), "A")
    assert plan_a["candidates"] == []
    assert "10" in plan_a["status"]
    tied = buy_plan(account, valued, market(universe), "B")
    assert all(c["symbol"] != "NEW" for c in tied["candidates"])
    assert [c["symbol"] for c in tied["candidates"]] == ["BETTER"]
    short = dict(valued)
    short["cash"] = valued["nav"] * Decimal("0.09")
    assert buy_plan(account, short, market(universe), "B")["candidates"] == []


def test_core_can_use_cash_between_two_and_ten_percent():
    account = {"lots": {}, "cash": Decimal("500")}
    valued = valued_from("500", "9500")  # nav 10000, cash is 5%
    plan = buy_plan(account, valued, market([stock("AAA", 80, 1), stock("BBB", 70, 2)]), "A")
    assert [c["symbol"] for c in plan["candidates"]] == ["AAA"]
    assert plan["candidates"][0]["target"] == Decimal("500")
    poor = dict(valued)
    poor["cash"] = Decimal("100")  # 1% of NAV
    assert buy_plan(account, poor, market([stock("AAA", 80, 1)]), "A")["candidates"] == []


def test_exit_uses_first_lot_and_blocks_stale_financials():
    holding = {
        "stock": stock("AAA", 10, 1, reasons=["TTM營收成長低於10%"], growth="0.20", margin="0.20"),
        "lots": [
            {"at": "2026-11-11T00:00:00+00:00", "entry_growth": "0.25", "entry_margin": "0.20", "quantity": 1},
            {"at": "2026-11-12T00:00:00+00:00", "entry_growth": "0.50", "entry_margin": "0.20", "quantity": 1},
        ],
    }
    clear = {"decision": "non_red", "categoryStates": {"rates": 0, "conditions": 0, "inflation": 0}}
    fresh = exit_review(holding, clear)
    assert fresh["status"] == "觸發出場條件"
    assert "營收成長較進場時下降至少 10 個百分點" not in fresh["reasons"]
    holding["lots"][0]["entry_growth"] = "0.31"
    dropped = exit_review(holding, clear)
    assert "營收成長較進場時下降至少 10 個百分點" in dropped["reasons"]
    holding["stock"]["financial"]["financial_age_days"] = 200
    stale = exit_review(holding, clear)
    assert stale["status"] == "無法判定"
    assert not stale["reasons"]
    assert any("無法判定" in item for item in stale["unknown"])
    red = exit_review(holding, {"decision": "red"})
    assert "全球資金溫度已達紅燈門檻" in red["reasons"]
    assert red["status"] == "觸發出場條件"


def test_monitor_does_not_repeat_an_unchanged_event():
    current = [{"portfolio_id": "p", "market": "US", "symbol": "AAA", "status": "未觸發出場條件",
                "reasons": "", "unknown": ""}]
    assert changed_events(current, []) == current
    assert changed_events(current, current) == []
    changed = dict(current[0])
    changed["status"] = "觸發出場條件"
    assert len(changed_events([changed], current)) == 1


def test_stale_market_blocks_after_more_than_one_business_day():
    assert data_too_old("2026-11-13", today=datetime(2026, 11, 16).date()) is False
    assert data_too_old("2026-11-13", today=datetime(2026, 11, 17).date()) is True


def test_scheduler_slots_and_secret_file_round_trip(tmp_path, monkeypatch):
    assert taipei_slot(datetime(2026, 11, 16, 6, 40)).endswith("-us")
    assert taipei_slot(datetime(2026, 11, 16, 12, 0)) is None
    assert taipei_slot(datetime(2026, 11, 16, 21, 40)).endswith("-tw")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PORTFOLIO_OWNER_EMAIL", "owner@example.com")
    monkeypatch.setenv("GOOGLE_OIDC_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_OIDC_CLIENT_SECRET", "secret-value\n")
    monkeypatch.setenv("STREAMLIT_COOKIE_SECRET", "cookie")
    monkeypatch.setenv("PRIVATE_PORTFOLIO_SHEET_ID", "sheet-private")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON",
                       '{"type":"service_account","client_email":"a@b.iam.gserviceaccount.com",'
                       '"private_key":"unit-test-key-material",'
                       '"token_uri":"https://oauth2.googleapis.com/token","project_id":"p"}')
    assert create_auth_config() is True
    written = (tmp_path / ".streamlit" / "secrets.toml").read_text()
    assert "unit-test-key-material" in written
    assert "secret-value" in written
    assert "secret-value\\n" not in written
    assert BANK_SHEET_ID not in written
    monkeypatch.setenv("PRIVATE_PORTFOLIO_SHEET_ID", BANK_SHEET_ID)
    try:
        create_auth_config()
    except ValueError:
        pass
    else:
        raise AssertionError("bank sheet was accepted as the private ledger")


def test_oauth_failure_codes_stay_public_and_env_path_includes_portal():
    assert oauth_error_code("access_denied") == "access_denied"
    assert oauth_error_code("redirect_uri_mismatch") == "redirect_uri_mismatch"
    assert oauth_error_code("client_secret=super-secret") == "unknown"
    assert "super-secret" not in oauth_error_message("client_secret=super-secret")
    assert "測試使用者" in oauth_error_message("access_denied")
    assert "portfolio_portal" in streamlit_env()["PYTHONPATH"]


def _fact(start, end, filed, val):
    return {"start": start, "end": end, "filed": filed, "form": "10-Q", "val": val, "fy": 2025, "fp": "Q1"}


def _facts(rows, tag="Revenues"):
    return {"facts": {"us-gaap": {tag: {"units": {"USD": rows}}}}}


def _eight_quarters():
    spans = [
        ("2024-01-01", "2024-03-31", "2024-05-01", 100),
        ("2024-04-01", "2024-06-30", "2024-08-01", 100),
        ("2024-07-01", "2024-09-30", "2024-11-01", 100),
        ("2024-10-01", "2024-12-31", "2025-02-01", 100),
        ("2025-01-01", "2025-03-31", "2025-05-01", 150),
        ("2025-04-01", "2025-06-30", "2025-08-01", 150),
        ("2025-07-01", "2025-09-30", "2025-11-01", 150),
        ("2025-10-01", "2025-12-31", "2026-02-01", 150),
    ]
    revenue, operating, cash = [], [], []
    for start, end, filed, val in spans:
        revenue.append(_fact(start, end, filed, val))
        operating.append(_fact(start, end, filed, 30 if val == 150 else 20))
        cash.append(_fact(start, end, filed, 15))
    payload = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": revenue}},
        "OperatingIncomeLoss": {"units": {"USD": operating}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": cash}},
    }}}
    return payload


def test_sec_baseline_uses_only_filings_public_two_days_before_the_trade():
    payload = _eight_quarters()
    ready = sec_financial(payload, "2026-11-11")
    assert ready["ok"] is True
    assert ready["verification"] == "OFFICIAL_FILING"
    assert abs(float(ready["revenue_growth"]) - 0.5) < 1e-9
    assert abs(float(ready["operating_margin"]) - 0.2) < 1e-9
    assert ready["filing_period"] == "2025-12-31"
    assert ready["official_published_at"] == "2026-02-01"
    assert ready["available_on"] <= "2026-11-11"
    too_late = _eight_quarters()
    too_late["facts"]["us-gaap"]["Revenues"]["units"]["USD"][-1]["filed"] = "2026-11-10"
    too_late["facts"]["us-gaap"]["OperatingIncomeLoss"]["units"]["USD"][-1]["filed"] = "2026-11-10"
    too_late["facts"]["us-gaap"]["NetCashProvidedByUsedInOperatingActivities"]["units"]["USD"][-1]["filed"] = "2026-11-10"
    assert sec_financial(too_late, "2026-11-11")["ok"] is False


def test_ytd_filing_is_split_into_the_quarter_that_was_already_public():
    payload = _eight_quarters()
    revenue = payload["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    revenue[-1] = _fact("2025-01-01", "2025-12-31", "2026-02-01", 600)
    revenue.append(_fact("2025-01-01", "2025-09-30", "2025-11-01", 450))
    derived = sec_financial(payload, "2026-11-11")
    assert derived["ok"] is True
    assert abs(float(derived["revenue_growth"]) - 0.5) < 1e-9


def test_later_financial_version_is_not_an_entry_baseline():
    reason = late_observation_reason(
        {"verification": "VERIFIED_OBSERVATION", "firstVerifiedAt": "2026-11-16T01:00:00+00:00"},
        "2026-11-11")
    assert "首次驗證晚於建倉日" in reason
    original = {"id": "buy-1", "portfolio_id": "p", "action": "BUY", "symbol": "AAA",
                "entry_growth": "", "entry_margin": "0.99"}
    saved = [{"portfolio_id": "p", "ledger_id": "buy-1", "verification": "OFFICIAL_FILING",
              "revenue_growth": "0.50", "operating_margin": "0.20", "filing_period": "2025-12-31",
              "official_published_at": "2026-02-01", "source": "SEC Company Facts",
              "version_id": "abc", "detail": ""}]
    copied = apply_official_baselines([original], saved)
    assert original["entry_growth"] == ""
    assert original["entry_margin"] == "0.99"
    assert copied[0]["entry_growth"] == "0.50"
    assert copied[0]["entry_margin"] == "0.20"
    assert copied[0]["baseline_verification"] == "OFFICIAL_FILING"


def test_missing_baseline_is_undetermined_and_red_funding_still_triggers():
    holding = {
        "stock": stock("AAA", 10, 1, reasons=[], growth="0.40", margin="0.20"),
        "lots": [{"at": "2026-11-11T00:00:00+00:00", "entry_growth": "", "entry_margin": "",
                  "baseline_reason": "沒有官方申報日可證明的建倉財報基準", "quantity": 1}],
    }
    clear = {"decision": "non_red", "categoryStates": {"rates": 0, "conditions": 0, "inflation": 0}}
    missing = exit_review(holding, clear)
    assert missing["status"] == "無法判定"
    assert missing["reasons"] == []
    assert any("建倉財報基準" in item for item in missing["unknown"])
    red = exit_review(holding, {"categoryStates": {"rates": 1, "conditions": 1, "inflation": 0},
                                "decision": "non_red"})
    assert "全球資金溫度已達紅燈門檻" in red["reasons"]
    assert red["status"] == "觸發出場條件"


def test_unknown_funding_is_not_a_green_light():
    assert funding_decision({"decision": "non_red"}) == "unknown"
    assert funding_decision({"decision": "non_red", "categoryStates": {
        "rates": 1, "conditions": None, "inflation": 0}}) == "unknown"
    assert funding_decision({"categoryStates": {"rates": 0, "conditions": 0, "inflation": None}}) == "non_red"
    assert funding_decision({"categoryStates": {"rates": 1, "conditions": 1, "inflation": None}}) == "red"


def test_entry_audit_does_not_invent_a_number_without_a_cik():
    txs = [{"id": "buy-1", "portfolio_id": "p", "action": "BUY", "symbol": "AAA",
            "trade_date": "2026-11-11", "market": "US"}]
    rows = missing_baseline_rows(txs, [], [{"symbol": "OTHER", "cik": "1"}], lambda cik: {})
    assert rows[0]["verification"] == "UNVERIFIED"
    assert rows[0]["revenue_growth"] == ""
    assert "CIK" in rows[0]["detail"]


def test_exit_transition_records_first_trigger_and_resolution():
    current = [{"portfolio_id": "p", "market": "US", "symbol": "AAA", "signal_date": "2026-11-13",
                "batch": "b", "status": "觸發出場條件", "reasons": "全球資金溫度已達紅燈門檻", "unknown": ""}]
    opened = condition_transitions(current, [])
    assert opened[0]["state"] == "open"
    assert opened[0]["condition"] == "全球資金溫度已達紅燈門檻"
    history = [{**opened[0], "recorded_at": "2026-11-13T00:00:00+00:00", "first_triggered_at": "2026-11-13T00:00:00+00:00"}]
    assert condition_transitions(current, history) == []
    cleared = [{**current[0], "status": "未觸發出場條件", "reasons": "", "unknown": ""}]
    resolved = condition_transitions(cleared, history)
    assert resolved[0]["state"] == "resolved"
    assert resolved[0]["first_triggered_at"] == "2026-11-13T00:00:00+00:00"


def test_v2_portfolio_edits_keep_identity_and_refuse_deletion():
    existing = {"id": "p", "name": "主組合", "strategy": "A", "note": "", "archived": ""}
    renamed = plan_profile_update(existing, name="成長組合", note="只改備註")
    assert ("name", "主組合", "成長組合") in renamed
    assert all(change[0] != "id" for change in renamed)
    try:
        plan_strategy_change(existing, "B", confirmed=False)
    except ValueError as exc:
        assert "確認" in str(exc)
    else:
        raise AssertionError("strategy change without confirmation was accepted")
    assert plan_strategy_change(existing, "B", confirmed=True) == ("strategy", "A", "B")
    assert plan_strategy_change(existing, "A", confirmed=True) is None
    assert plan_archive(existing, confirmed=True) == ("archived", "", "true")
    try:
        permanent_delete_refused()
    except ValueError as exc:
        assert "封存" in str(exc)
    else:
        raise AssertionError("permanent delete was allowed")
    created = validate_new_portfolio(name="新組合", market="US", strategy="A", currency="USD", note="")
    assert created["archived"] == ""
    assert "quantity" not in created
    try:
        validate_new_portfolio(name="新組合", market="US", strategy="A", currency="TWD")
    except ValueError as exc:
        assert "幣別" in str(exc)
    else:
        raise AssertionError("mismatched currency was accepted")


def test_nav_gaps_are_labeled_and_exit_times_survive_resolution():
    assert weekday_gaps(["2026-11-11", "2026-11-13"]) == ["2026-11-12"]
    assert weekday_gaps(["2026-11-13"]) == []
    timeline = condition_timeline([
        {"portfolio_id": "p", "symbol": "AAA", "condition": "資金", "state": "open",
         "first_triggered_at": "2026-11-13T00:00:00+00:00"},
        {"portfolio_id": "p", "symbol": "AAA", "condition": "資金", "state": "resolved",
         "first_triggered_at": "2026-11-13T00:00:00+00:00", "resolved_at": "2026-11-16T00:00:00+00:00"},
    ], "p", "AAA")
    assert timeline[0]["open"] is False
    assert timeline[0]["first"].startswith("2026-11-13")
    assert timeline[0]["resolved"].startswith("2026-11-16")


def test_v2_page_keeps_baseline_monitor_and_is_not_truncated():
    text = (ROOT / "portfolio_portal" / "app.py").read_text()
    compile(text, "app.py", "exec")
    for marker in ("def show_funding", "def render_dashboard", "def render_manage",
                   "編輯投資組合", "entry_baselines", "無法判定", "weekday_gaps",
                   "封存這個組合", "portfolio_id"):
        assert marker in text
    assert text.rstrip().endswith("st.stop()")
    assert len(text) > 25000
    storage = (ROOT / "portfolio_portal" / "storage.py").read_text()
    assert "def append_transaction" in storage
    assert "def append_entry_baselines" in storage
    assert "portfolio_changes" in storage
    assert "def delete_portfolio" in storage
    assert "permanent_delete_refused" in storage


def test_sources_do_not_seed_a_live_book():
    banned = ("NVDA", "AVGO", "MKSI", "VIAV")
    for name in ("app.py", "core.py", "storage.py", "monitor.py", "monitor_job.py",
                 "setup_landing.py", "entry_basis.py", "portfolio_admin.py"):
        text = (ROOT / "portfolio_portal" / name).read_text()
        assert not any(symbol in text for symbol in banned)
    storage = (ROOT / "portfolio_portal" / "storage.py").read_text()
    assert BANK_SHEET_ID in storage
    assert "安全防護" in storage
