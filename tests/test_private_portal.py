"""Owner-portfolio rules. No network, no sheets, no secrets, no seeded trades."""
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portfolio_portal"))

from core import (  # noqa: E402
    buy_plan, compute_account, exit_review, modified_dietz, nav_extension, owner_allowed, portfolio_value,
)
from monitor import changed_events, data_too_old  # noqa: E402
from render_entrypoint import BANK_SHEET_ID, create_auth_config, taipei_slot  # noqa: E402


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
    fresh = exit_review(holding, {"decision": "non_red"})
    assert fresh["status"] == "觸發出場條件"
    assert "營收成長較進場時下降至少 10 個百分點" not in fresh["reasons"]
    holding["lots"][0]["entry_growth"] = "0.31"
    dropped = exit_review(holding, {"decision": "non_red"})
    assert "營收成長較進場時下降至少 10 個百分點" in dropped["reasons"]
    holding["stock"]["financial"]["financial_age_days"] = 200
    stale = exit_review(holding, {"decision": "non_red"})
    assert stale["status"] == "資料待確認"
    assert not stale["reasons"]
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
    monkeypatch.setenv("GOOGLE_OIDC_CLIENT_SECRET", "secret-value")
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
    assert BANK_SHEET_ID not in written
    monkeypatch.setenv("PRIVATE_PORTFOLIO_SHEET_ID", BANK_SHEET_ID)
    try:
        create_auth_config()
    except ValueError:
        pass
    else:
        raise AssertionError("bank sheet was accepted as the private ledger")


def test_sources_do_not_seed_a_live_book():
    banned = ("NVDA", "AVGO", "MKSI", "VIAV")
    for name in ("app.py", "core.py", "storage.py", "monitor.py", "monitor_job.py", "setup_landing.py"):
        text = (ROOT / "portfolio_portal" / name).read_text()
        assert not any(symbol in text for symbol in banned)
    storage = (ROOT / "portfolio_portal" / "storage.py").read_text()
    assert BANK_SHEET_ID in storage
    assert "安全防護" in storage
