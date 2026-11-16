"""Private owner-only investment dashboard (Streamlit + Google OIDC + Sheets).

Deploy this file as an independent Streamlit app from a PRIVATE GitHub repo.
It never places portfolio data or credentials in the public bank repository.
"""
from __future__ import annotations
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import streamlit as st

from core import (
    buy_plan, compute_account, contributions_through, divergence_plan,
    funding_decision, modified_dietz, owner_allowed, portfolio_value,
)
from entry_basis import apply_official_baselines, resolve_buy_baseline, sync_entry_baselines
from monitor import portfolio_reviews, review_holding, data_too_old
from oauth_messages import oauth_error_message
from portfolio_admin import STRATEGIES, condition_timeline, weekday_gaps
from research import latest_research
from storage import PortfolioSheets, sheets_client

st.set_page_config(page_title="我的投資組合｜私人版", page_icon="🔐",
                   layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
section[data-testid="stSidebar"] button { width: 100%; text-align: left; }
@media (max-width: 700px) {
  .block-container { padding-top: 0.6rem; padding-left: 0.45rem; padding-right: 0.45rem; max-width: 100%; }
  div[data-testid="stHorizontalBlock"] { flex-wrap: wrap; gap: 0.35rem; }
  div[data-testid="column"] { flex: 1 1 46% !important; min-width: 140px; }
  button { min-height: 2.6rem; }
}
</style>
""", unsafe_allow_html=True)


def public_error(exc):
    text = str(exc)
    lowered = text.lower()
    if any(word in lowered for word in ("private_key", "client_secret", "begin private", "token", "secret")):
        return "設定無法使用。請只在 Render 的 Environment 修正，不要把密鑰貼到聊天或 GitHub。"
    return text[:300]


def pct(value):
    if value in (None, ""):
        return "無法判定"
    try:
        return f"{float(value):.2%}"
    except (TypeError, ValueError):
        return "無法判定"


def points(value):
    if value in (None, ""):
        return "無法判定"
    try:
        return f"{float(value):+.2f} 個百分點"
    except (TypeError, ValueError):
        return "無法判定"


def money_text(value, currency=""):
    if value is None:
        return "待確認"
    suffix = f" {currency}" if currency else ""
    return f"{value:,.2f}{suffix}"


def _cik(symbol, block):
    for stock in block.get("stocks") or []:
        if str(stock.get("symbol") or "").upper() == str(symbol or "").upper():
            cik = str(stock.get("cik") or "").strip()
            if cik.isdigit():
                return cik.zfill(10)
    return ""


def _buy_facts(symbol, block):
    from entry_basis import fetch_company_facts
    cik = _cik(symbol, block)
    if not cik:
        return None
    return fetch_company_facts(cik)


def shown_review(holding, strategy, macro, market_asof):
    review = review_holding(holding, strategy, macro)
    if strategy in ("A", "B") and data_too_old(market_asof):
        review = {**review, "status": "無法判定", "reasons": [],
                  "unknown": ["無法判定：研究行情日過期，暫停買賣判斷"]}
    if review.get("status") == "資料待確認":
        review = {**review, "status": "無法判定"}
    return review


def show_funding(macro):
    decision = funding_decision(macro)
    labels = {"red": "紅燈", "non_red": "非紅燈", "unknown": "未知"}
    names = (("rates", "利率", "90 日實質利率升逾 0.50 個百分點，或政策利率升逾 0.25 個百分點。"),
             ("conditions", "金融條件", "NFCI 高於 0，或 90 日升逾 0.25 指數點。"),
             ("inflation", "通膨", "CPI 年增逾 4%，且近 3 個月年化高於年增率。"))
    states = macro.get("categoryStates") if isinstance(macro.get("categoryStates"), dict) else {}

    def flag(raw):
        if raw in (1, "1", True):
            return "已觸發"
        if raw in (0, "0", False):
            return "未觸發"
        return "未知"

    st.subheader("全球資金溫度")
    cols = st.columns(3)
    for col, (key, label, rule) in zip(cols, names):
        col.metric(label, flag(states.get(key)))
        col.caption(rule)
    score = macro.get("scoreRange")
    score_text = f"{score[0]}–{score[1]}" if isinstance(score, (list, tuple)) and len(score) == 2 else "無法計算"
    st.write(f"綜合判斷：**{labels[decision]}** · 目前風險數量範圍 {score_text}")
    if decision == "red":
        st.error("紅燈已達主策略出場條件。這不會自動賣出。")
    elif decision == "unknown":
        st.warning("未知不是綠燈，也不等於未觸發出場。")
    else:
        st.caption("非紅燈只代表已判定的觸發數到不了兩項，不是全綠。個股財報出場仍分開檢查。")
    stale_market = not macro.get("marketAsOf") or data_too_old(macro["marketAsOf"])
    st.caption("行情日 " + str(macro.get("marketAsOf") or "未知")
               + " · 觀測 " + str(macro.get("observedAt") or macro.get("calculatedAt") or "未知")
               + " · 規則 " + str(macro.get("ruleVersion") or "未知")
               + " · 新鮮度 " + ("過期，只供核對" if stale_market else "仍在可核對窗口"))
    impact = {
        "red": "對持股：主策略 A／B 將此紅燈列入出場條件。",
        "non_red": "對持股：資金溫度這一項未構成出場。營收與營益率仍依建倉基準單獨判斷。",
        "unknown": "對持股：資金溫度無法判定。此項不計為未觸發，其他已證實的出場條件仍有效。",
    }[decision]
    st.caption(impact)

    def level(value, digits):
        if value in (None, ""):
            return "未知"
        try:
            return f"{float(value):.{digits}f}"
        except (TypeError, ValueError):
            return "未知"

    readings = [
        ("實質殖利率", level(macro.get("real_yield"), 2), level(macro.get("real_yield_90d_change"), 2)),
        ("政策利率", level(macro.get("fed_funds"), 2), level(macro.get("fed_funds_90d_change"), 2)),
        ("NFCI", level(macro.get("financial_conditions"), 3), level(macro.get("financial_conditions_90d_change"), 3)),
        ("CPI 年增", pct(macro.get("cpi_yoy")), "—"),
        ("CPI 近 3 個月年化", pct(macro.get("cpi_3m_annualized")), "—"),
    ]
    st.dataframe([{"項目": name, "讀數": value, "90 日變化": change} for name, value, change in readings],
                 hide_index=True, use_container_width=True)


def auth_gate():
    st.title("🔐 我的投資組合")
    oauth_error = str(st.query_params.get("oauth_error", ""))
    if oauth_error:
        st.error(oauth_error_message(oauth_error))
    if not st.user.is_logged_in:
        st.caption("此網站需要你的 Google 帳號登入。未登入者無法讀取任何持股資料。")
        st.caption("請用 Safari 或 Chrome 開啟這個網址。Google 會拒絕 App 內建瀏覽器的登入。")
        if st.button("使用 Google 帳號登入", type="primary"):
            st.login()
        st.stop()
    allowed = str(st.secrets.get("owner_email", ""))
    email = str(st.user.get("email", ""))
    verified = st.user.get("email_verified", False)
    if not owner_allowed(email, verified, allowed):
        st.error("已登入，但不是經授權且已驗證的擁有者。")
        if email:
            st.caption("這次 Google 登入的信箱是 " + email.strip() + " 。它必須與 Render 的 PORTFOLIO_OWNER_EMAIL 是同一個已驗證信箱。")
        else:
            st.caption("Google 沒有提供信箱。請改用已驗證的 Gmail 重新登入。")
        if email and verified is not True and str(verified).strip().lower() not in {"true", "1"}:
            st.caption("這個信箱尚未被 Google 標成已驗證。")
        if st.button("登出"):
            st.logout()
        st.stop()
    return email.strip().casefold()


def render_manage(store, active, archived):
    st.title("投資組合管理")
    st.caption("新增的組合沒有任何預設持股。封存只會從左側清單隱藏，不刪除交易、現金、淨值或建倉基準。")
    st.subheader("新增投資組合")
    with st.form("create-portfolio"):
        name = st.text_input("名稱", placeholder="自訂組合名稱")
        c1, c2, c3 = st.columns(3)
        market = c1.selectbox("市場", ["US", "TW"], format_func=lambda x: "美股" if x == "US" else "台股")
        strategy = c2.selectbox("策略", list(STRATEGIES))
        currency = c3.selectbox("幣別", ["USD", "TWD"])
        note = st.text_input("備註（可空白）")
        st.caption("幣別必須配合市場：美股 USD，台股 TWD。")
        if st.form_submit_button("建立組合", type="primary"):
            try:
                item = store.create_portfolio(name=name, market=market, strategy=strategy,
                                               currency=currency, note=note)
                st.session_state.portal_view = item["id"]
                st.success("已儲存至私人 Google Sheets：" + item["name"])
                st.rerun()
            except Exception as exc:
                st.error("建立失敗：" + public_error(exc))
    st.subheader("編輯投資組合")
    if not active:
        st.info("尚無未封存的組合。")
    else:
        labels = {p["id"]: p["name"] for p in active}
        selected = st.selectbox("選擇組合", list(labels), format_func=lambda pid: labels[pid])
        current = next(p for p in active if p["id"] == selected)
        st.caption("portfolio_id " + current["id"] + " 不會改變。市場與幣別維持 "
                   + current["market"] + " / " + current["currency"] + "。")
        with st.form("edit-profile-" + current["id"]):
            new_name = st.text_input("名稱", value=current["name"], key="name-" + current["id"])
            new_note = st.text_input("備註", value=current.get("note") or "", key="note-" + current["id"])
            if st.form_submit_button("儲存名稱與備註"):
                try:
                    changed = store.update_portfolio_profile(current["id"], name=new_name, note=new_note)
                    st.success("已更新" if changed else "沒有變更")
                    st.rerun()
                except Exception as exc:
                    st.error("更新失敗：" + public_error(exc))
        with st.form("edit-strategy-" + current["id"]):
            new_strategy = st.selectbox("策略", list(STRATEGIES),
                                        index=list(STRATEGIES).index(current["strategy"])
                                        if current["strategy"] in STRATEGIES else 0)
            st.warning("變更策略會改變之後的出場與買入判斷。不會改股數、成本、交易紀錄、NAV 或建倉基準。")
            confirmed = st.checkbox("我確認只變更往後規則，不改既有持股資料")
            if st.form_submit_button("確認變更策略"):
                try:
                    changed = store.update_portfolio_strategy(current["id"], new_strategy, confirmed=confirmed)
                    st.success("策略已記錄變更" if changed else "策略沒有變更")
                    st.rerun()
                except Exception as exc:
                    st.error("策略未變更：" + public_error(exc))
        st.subheader("封存投資組合")
        st.warning("有交易或歷史紀錄的組合不能永久刪除。封存後可以恢復。")
        with st.form("archive-" + current["id"]):
            archive_ok = st.checkbox("我確認封存「" + current["name"] + "」，不是刪除")
            if st.form_submit_button("封存這個組合"):
                try:
                    store.archive_portfolio(current["id"], confirmed=archive_ok)
                    st.session_state.portal_view = "manage"
                    st.success("已封存")
                    st.rerun()
                except Exception as exc:
                    st.error("未封存：" + public_error(exc))
    st.subheader("已封存的組合")
    if not archived:
        st.caption("目前沒有封存組合。")
    for row in archived:
        left, right = st.columns([4, 1])
        left.write(row["name"] + " · " + row["market"] + " · " + row["strategy"])
        left.caption(row["id"])
        if right.button("恢復", key="restore-" + row["id"]):
            try:
                store.restore_portfolio(row["id"])
                st.session_state.portal_view = row["id"]
                st.rerun()
            except Exception as exc:
                st.error("無法恢復：" + public_error(exc))


def render_dashboard(store, portfolio, transactions, block, entry_batch, transitions):
    account = compute_account(transactions, portfolio["id"])
    valued = portfolio_value(account, block)
    macro = block["macro"]
    market_asof = macro.get("marketAsOf") or ""
    contributed = contributions_through(transactions, portfolio["id"], date.today().isoformat())
    nav_now = valued["nav"]
    unrealized = None
    if not valued["missing_prices"]:
        unrealized = sum((holding["pnl"] for holding in valued["holdings"]), Decimal("0"))
    combined = None if unrealized is None else valued["realized"] + unrealized
    cumulative = None if nav_now is None else nav_now - contributed
    returns = None
    if nav_now is not None and contributed > 0:
        returns = (nav_now - contributed) / contributed
    reviews = {}
    for holding in valued["holdings"]:
        reviews[holding["symbol"]] = shown_review(holding, portfolio["strategy"], macro, market_asof)
    for symbol in valued["missing_prices"]:
        reviews[symbol] = {"status": "無法判定", "reasons": [],
                           "unknown": ["無法判定：沒有同日可驗證收盤價"],
                           "baseline": {}, "latest": {}, "change": {}}

    st.title(portfolio["name"])
    st.caption(portfolio["market"] + " · 策略 " + portfolio["strategy"] + " · " + portfolio["currency"]
               + " · " + (portfolio.get("note") or "無備註"))

    with st.container(border=True):
        show_funding(macro)

    with st.container(border=True):
        st.subheader("帳戶資產總覽")
        cols = st.columns(4)
        cols[0].metric("帳戶總淨值", "無法完整估值" if nav_now is None else money_text(nav_now, portfolio["currency"]))
        cols[1].metric("累計投入本金", money_text(contributed, portfolio["currency"]))
        cols[2].metric("現金餘額", money_text(valued["cash"], portfolio["currency"]))
        cols[3].metric("持股總市值", "待確認" if valued["missing_prices"] else money_text(valued["market_value"], portfolio["currency"]))
        more = st.columns(3)
        more[0].metric("已實現損益", money_text(valued["realized"], portfolio["currency"]))
        more[1].metric("未實現損益", "待確認" if unrealized is None else money_text(unrealized, portfolio["currency"]))
        more[2].metric("累計報酬率", "待確認" if returns is None else f"{returns:+.2%}")
        st.caption("累計損益 " + ("待確認" if cumulative is None else money_text(cumulative, portfolio["currency"]))
                   + "，是淨值減淨投入。累計報酬率不是標準 TWR。"
                   + " 已實現加未實現 " + ("待確認" if combined is None else money_text(combined, portfolio["currency"]))
                   + "。最後行情日 " + (market_asof or "未知") + "。")
        if valued["missing_prices"]:
            st.warning("以下持股沒有可核對收盤價，淨值與權重不予計算：" + "、".join(valued["missing_prices"]))

    with st.container(border=True):
        st.subheader("目前持股")
        if not valued["holdings"] and not valued["missing_prices"]:
            st.info("這個組合尚未登錄持股。")
        else:
            nav_for_weight = nav_now if nav_now not in (None, 0) else None
            rows = []
            for holding in valued["holdings"]:
                avg = holding["cost"] / holding["quantity"] if holding["quantity"] else None
                ret = holding["pnl"] / holding["cost"] if holding["cost"] else None
                weight = holding["value"] / nav_for_weight if nav_for_weight else None
                rows.append({
                    "股票代號": holding["symbol"],
                    "公司名稱": (holding["stock"].get("name") or holding["symbol"]),
                    "股數": f"{holding['quantity']:.4f}",
                    "平均成本": money_text(avg),
                    "最新收盤價": money_text(holding["price"]),
                    "市值": money_text(holding["value"]),
                    "未實現損益": money_text(holding["pnl"]),
                    "報酬率": "待確認" if ret is None else f"{ret:+.2%}",
                    "組合權重": "待確認" if weight is None else f"{weight:.2%}",
                    "監控狀態": reviews[holding["symbol"]]["status"],
                })
            for symbol in valued["missing_prices"]:
                rows.append({
                    "股票代號": symbol, "公司名稱": symbol, "股數": "待確認", "平均成本": "待確認",
                    "最新收盤價": "待確認", "市值": "待確認", "未實現損益": "待確認",
                    "報酬率": "待確認", "組合權重": "待確認", "監控狀態": "無法判定",
                })
            st.dataframe(rows, hide_index=True, use_container_width=True)
            st.caption("手機可左右滑動表格。點下方個股可看建倉基準。")

    with st.container(border=True):
        st.subheader("持股出場監控")
        if portfolio["strategy"] not in ("A", "B"):
            st.info("這個組合不是主策略 A／B，不套用銀行版主策略的強制出場規則。資金與研究觀察仍會列出。")
        if not valued["holdings"] and not valued["missing_prices"]:
            st.caption("沒有持股可監控。")
        for holding in valued["holdings"]:
            review = reviews[holding["symbol"]]
            with st.expander(holding["symbol"] + "｜" + review["status"], expanded=False):
                base = review.get("baseline") or {}
                latest = review.get("latest") or {}
                change = review.get("change") or {}
                st.write("建倉營收成長 " + pct(base.get("growth")) + " · 最新 " + pct(latest.get("growth"))
                         + " · 變化 " + points(change.get("growth_pp")))
                st.write("建倉營益率 " + pct(base.get("margin")) + " · 最新 " + pct(latest.get("margin"))
                         + " · 變化 " + points(change.get("margin_pp")))
                st.caption("財報期 " + (base.get("period") or "無法判定")
                           + " · 官方申報日 " + (base.get("filed") or "無法判定")
                           + " · 來源 " + (base.get("source") or "無法判定")
                           + " · 版本 " + ((base.get("version") or "")[:16] or "無法判定")
                           + " · 驗證 " + (base.get("verification") or "無法判定"))
                if base.get("reason"):
                    st.caption(base["reason"])
                funding = funding_decision(macro)
                if funding == "red" and portfolio["strategy"] in ("A", "B"):
                    st.error("資金紅燈影響出場：已觸發。這不會自動賣出。")
                elif funding == "unknown":
                    st.warning("資金紅燈無法判定，不當成沒有出場訊號。")
                else:
                    st.caption("資金紅燈：未觸發。非紅燈不是全面安全。")
                st.write("出場判斷：" + review["status"])
                for reason in review.get("reasons") or []:
                    st.error("觸發：" + reason)
                for reason in review.get("unknown") or []:
                    st.warning(reason if str(reason).startswith("無法判定") else "無法判定：" + reason)
                history = condition_timeline(transitions, portfolio["id"], holding["symbol"])
                if history:
                    st.dataframe([
                        {"條件": item["condition"], "狀態": "仍觸發" if item["open"] else "已解除",
                         "首次觸發": item["first"] or "尚未記錄", "解除": item["resolved"] or "尚未解除"}
                        for item in history
                    ], hide_index=True, use_container_width=True)
                else:
                    st.caption("尚無已保存的首次觸發或解除時間。按下方監控紀錄才會寫入私人試算表。")
                first_lot = min(holding["lots"], key=lambda lot: (str(lot.get("at") or ""), str(lot.get("entry_filing_period") or "")))
                if (first_lot.get("ledger_entry_growth") or first_lot.get("ledger_entry_margin")) and base.get("verification") != "OFFICIAL_FILING":
                    st.caption("原始帳本另有人工填寫的進場數字。出場判斷不採用，也沒有改寫那一列。")

    with st.container(border=True):
        st.subheader("淨值與績效")
        try:
            nav_rows = store.nav_snapshots(portfolio["id"])
        except Exception as exc:
            st.warning("無法讀取帳戶淨值歷史；本次不顯示推估走勢：" + public_error(exc))
            nav_rows = []
        period = st.selectbox("區間", ["近 30 天", "近 90 天", "近 1 年", "全部"], index=3)
        valid = [row for row in nav_rows if row.get("date") and row.get("nav") not in (None, "")]
        gaps = weekday_gaps([row["date"] for row in valid])
        if gaps:
            st.warning("以下交易日沒有已核對淨值，標為待核對，不補造：" + "、".join(gaps))
        else:
            st.caption("已保存的淨值日期之間，沒有其他平日缺口。缺日不會用後來的股價回填。")
        if valid:
            last_date = max(date.fromisoformat(row["date"]) for row in valid)
            days = {"近 30 天": 30, "近 90 天": 90, "近 1 年": 365}.get(period)
            shown = valid
            if days is not None:
                shown = [row for row in valid if date.fromisoformat(row["date"]) >= last_date - timedelta(days=days)]
            if shown:
                curve = pd.DataFrame([
                    {"date": pd.to_datetime(row["date"]), "nav": float(row["nav"])} for row in shown
                ]).sort_values("date")
                latest = max(shown, key=lambda row: row["date"])
                st.metric("最新已記錄的收盤淨值", money_text(Decimal(str(latest["nav"])), portfolio["currency"]))
                st.caption("最近有效收盤：" + latest["date"] + " · 來源 " + (latest.get("source") or "未記錄")
                           + " · 只畫已核驗的日期")
                st.line_chart(curve, x="date", y="nav", height=230, use_container_width=True)
                linked = modified_dietz(transactions, portfolio["id"], nav_rows) or []
                if len(linked) >= 2:
                    st.caption("自 " + linked[-2]["date"] + " 至 " + linked[-1]["date"]
                               + " 的 Modified Dietz 區間估計 " + f"{linked[-1]['subperiod_return']:+.2%}"
                               + " · 連乘估計 " + f"{linked[-1]['modified_dietz']:+.2%}"
                               + "。這不是標準 TWR。")
                with st.expander("交易日逐日淨值明細"):
                    st.dataframe([
                        {"日期": row["date"], "淨值": row["nav"], "現金": row["cash"] or "未記錄",
                         "持股市值": row["equity_value"] or "未記錄", "累計損益": row.get("cumulative_pnl") or "未記錄",
                         "來源": row["source"]}
                        for row in shown
                    ], hide_index=True, use_container_width=True)
            else:
                st.info("這個區間沒有已核對的歷史淨值。")
        else:
            st.info("尚未建立歷史淨值紀錄。排程只在每一檔都有同日可核對收盤價時才新增一列，已存在的日期不會覆蓋。")

    with st.container(border=True):
        st.subheader("交易與現金")
        with st.form("record-transaction"):
            action = st.selectbox("動作", ["DEPOSIT", "BUY", "SELL", "WITHDRAW"],
                                  format_func=lambda x: {"DEPOSIT": "存入現金", "BUY": "買入",
                                                         "SELL": "賣出", "WITHDRAW": "提領現金"}[x])
            tdate = st.date_input("實際交易日期", value=date.today(), max_value=date.today())
            symbol = st.text_input("股票代號（現金異動可留空）").upper().strip()
            c1, c2, c3 = st.columns(3)
            qty = c1.text_input("股數（買／賣）", value="")
            price = c2.text_input("成交價（買／賣）", value="")
            fee = c3.text_input("費用", value="0")
            cash_amount = st.text_input("現金異動金額（存／提）", value="")
            role = st.selectbox("持股類別", ["core", "extra"] if portfolio["strategy"] == "B" else ["core"],
                                format_func=lambda x: "核心" if x == "core" else "額外")
            st.caption("新的買入會把建倉當日已正式申報的財報基準寫進稽核表。寫不進去就標無法判定，不會回填今天的財報，也不改舊交易列。")
            note = st.text_input("交易備註", value="")
            if st.form_submit_button("確認並寫入私人 Google Sheets", type="primary"):
                try:
                    record = store.append_transaction(
                        portfolio_id=portfolio["id"], action=action, trade_date=tdate.isoformat(),
                        symbol=symbol, quantity=qty, price=price, fee=fee,
                        cash_amount=cash_amount, role=role, note=note)
                    if action == "BUY":
                        basis = resolve_buy_baseline(
                            symbol, tdate.isoformat(), market=portfolio["market"],
                            payload=_buy_facts(symbol, block), cik=_cik(symbol, block))
                        store.append_entry_baselines([{**basis, "portfolio_id": portfolio["id"],
                                                        "ledger_id": record["id"]}])
                    st.success("已寫入交易流水帳。建倉基準寫在稽核表，原始舊列沒有被改寫。")
                    st.rerun()
                except Exception as exc:
                    st.error("未完成寫入：" + public_error(exc))
        history = [row for row in transactions if row["portfolio_id"] == portfolio["id"]]
        st.dataframe(history[::-1], hide_index=True, use_container_width=True)
        st.subheader("A／B 與背離研究")
        st.caption("這裡只比較研究候選，不會改你的組合，也不會下單。")
        if portfolio["strategy"] in ("A", "B"):
            for variant in ("A", "B"):
                if data_too_old(market_asof):
                    result = {"status": "市場研究資料已過期，暫停新增買入", "candidates": []}
                else:
                    result = buy_plan(account, valued, block, variant)
                st.write("主策略 " + variant + "｜" + result["status"])
                if result["candidates"]:
                    st.dataframe([
                        {"代號": item["symbol"], "名稱": item["name"], "分數": item["score"],
                         "建議金額": f"{item['target']:,.2f}", "角色": "核心" if item["role"] == "core" else "額外",
                         "行情日": item["price_date"]}
                        for item in result["candidates"]
                    ], hide_index=True, use_container_width=True)
        else:
            st.caption("A／B 加碼規則不套用在這個策略。")
        if data_too_old(market_asof):
            divergence = {"status": "市場研究資料已過期，暫停背離買入", "candidates": [], "hold_rule": ""}
        else:
            divergence = divergence_plan(account, valued, block)
        st.write("背離長抱｜" + divergence["status"])
        st.caption(divergence.get("hold_rule") or "")
        if divergence["candidates"]:
            st.dataframe([
                {"代號": item["symbol"], "名稱": item["name"], "分數": item["score"],
                 "建議金額": "觀望" if item["target"] is None else f"{item['target']:,.2f}",
                 "行情日": item["price_date"]}
                for item in divergence["candidates"]
            ], hide_index=True, use_container_width=True)

    with st.container(border=True):
        st.subheader("監控與事件歷史")
        st.caption("只在狀態變化時追加一列。研究批次 " + str(entry_batch) + "。")
        mine = [row for row in transitions if row.get("portfolio_id") == portfolio["id"]]
        if st.button("記錄本次監控變化至私人 Google Sheets"):
            try:
                current = portfolio_reviews([portfolio], transactions, {"markets": {portfolio["market"]: block}}, entry_batch)
                recorded = store.append_monitor_results(current)
                added = store.append_exit_transitions(current)
                st.success(f"已儲存 {len(recorded)} 筆狀態變化、{len(added)} 筆條件觸發或解除")
                st.rerun()
            except Exception as exc:
                st.error("寫入監控紀錄失敗：" + public_error(exc))
        if mine:
            st.dataframe(mine[::-1][:100], hide_index=True, use_container_width=True)
        else:
            st.caption("這個組合尚無出場條件的首次觸發或解除紀錄。")


owner_email = auth_gate()


@st.cache_resource
def private_store():
    private_id = st.secrets.get("private_portfolio_sheet_id", "")
    info = dict(st.secrets["google_service_account"])
    return PortfolioSheets(sheets_client(info), private_id)


@st.cache_data(ttl=180, show_spinner=False)
def research_snapshot():
    client = sheets_client(dict(st.secrets["google_service_account"]))
    return latest_research(client)


try:
    store = private_store()
except Exception as exc:
    st.error("私人 Google Sheets 尚未完成授權或設定。")
    st.caption("請先在 Render Environment 設定擁有者信箱、私人試算表與服務帳號，並把表分享給該服務帳號。")
    st.caption(public_error(exc))
    st.stop()

try:
    entry, research_source, research_issues = research_snapshot()
except Exception as exc:
    st.error("銀行版研究資料無法讀取或驗證；不會依過期行情產生新交易建議。")
    st.caption(public_error(exc))
    st.stop()

snapshot = entry["snapshot"]
try:
    portfolios = store.portfolios()
    archived = store.archived_portfolios()
    transactions = store.transactions()
    try:
        sync_entry_baselines(store, transactions, portfolios, snapshot)
    except Exception as exc:
        st.warning("建倉財報基準這次沒有補齊，出場比較不會猜數字：" + public_error(exc))
    try:
        transactions = apply_official_baselines(transactions, store.entry_baselines())
    except Exception as exc:
        st.warning("無法讀取建倉基準稽核表，這次不套用補登：" + public_error(exc))
    transitions = store.exit_transitions()
    live_reviews = portfolio_reviews(portfolios, transactions, snapshot, entry["batch"])
except Exception as exc:
    st.error("讀取私人交易紀錄失敗，禁止建立建議或寫入新資料。")
    st.caption(public_error(exc))
    st.stop()

urgent = [row for row in live_reviews if row["status"] == "觸發出場條件"]
pending = [row for row in live_reviews if row["status"] in ("資料待確認", "無法判定")]

for message in research_issues:
    st.warning(message)

active_ids = {item["id"] for item in portfolios}
if "portal_view" not in st.session_state or (
        st.session_state.portal_view != "manage" and st.session_state.portal_view not in active_ids):
    st.session_state.portal_view = portfolios[0]["id"] if portfolios else "manage"

with st.sidebar:
    st.caption("只有經授權的 Google 帳號能存取")
    st.write("**" + owner_email + "**")
    if st.button("編輯投資組合", type="primary" if st.session_state.portal_view == "manage" else "secondary"):
        st.session_state.portal_view = "manage"
        st.rerun()
    st.caption("投資組合")
    for item in portfolios:
        picked = st.session_state.portal_view == item["id"]
        if st.button(item["name"], key="nav-" + item["id"], type="primary" if picked else "secondary"):
            st.session_state.portal_view = item["id"]
            st.rerun()
    if not portfolios:
        st.caption("尚未建立組合。")
    st.caption("出場條件 " + str(len(urgent)) + " · 無法判定 " + str(len(pending)))
    if urgent:
        st.caption("出場：" + "、".join(row["symbol"] for row in urgent[:8]))
    st.caption("資料來源：" + research_source)
    st.caption("研究批次：" + str(entry["batch"]))
    for market_code, market_name in (("US", "美股"), ("TW", "台股")):
        st.caption(market_name + "：" + str(snapshot["markets"][market_code]["macro"]["marketAsOf"]))
    st.caption("手機請用左上角控制項展開或收合選單。")
    if st.button("重新讀取最新研究"):
        research_snapshot.clear()
        st.rerun()
    if st.button("登出"):
        st.logout()

if st.session_state.portal_view == "manage":
    render_manage(store, portfolios, archived)
    st.stop()

portfolio = next(item for item in portfolios if item["id"] == st.session_state.portal_view)
block = snapshot["markets"][portfolio["market"]]
try:
    render_dashboard(store, portfolio, transactions, block, entry["batch"], transitions)
except Exception as exc:
    st.error("這個組合目前無法完成對帳或顯示，已停止分析：" + public_error(exc))
    st.stop()
