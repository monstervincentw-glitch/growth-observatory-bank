"""Private owner-only investment dashboard (Streamlit + Google OIDC + Sheets).

Deploy this file as an independent Streamlit app from a PRIVATE GitHub repo.
It never places portfolio data or credentials in the public bank repository.
"""
from __future__ import annotations
from datetime import date, timedelta
from decimal import Decimal

import altair as alt
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
/* Compact portfolio V2.1: dense monitor grid and tri-state funding cards */
/* GPT Sites-inspired research workspace: warm paper, soft gold, charcoal ink */
:root{--portfolio-ink:#263344;--portfolio-line:#e3e4e6;--portfolio-gold:#b28b53;--portfolio-paper:#faf9f6}
[data-testid="stAppViewContainer"]{background:#faf9f6;color:#263344}
[data-testid="stHeader"]{background:rgba(250,249,246,.93)}
section[data-testid="stSidebar"]{
 width:260px!important;min-width:260px!important;
 background:#f5f4f0!important;border-right:1px solid #e7e2d9
}
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p{line-height:1.45}
section[data-testid="stSidebar"] .stButton button{
 font-size:.92rem;min-height:2.4rem;border-radius:.75rem;
 background:#fffdfa!important;color:#263344!important;
 border:1px solid #dcdedc!important
}
section[data-testid="stSidebar"] .stButton button[kind="primary"],
section[data-testid="stSidebar"] .stButton button[data-testid="stBaseButton-primary"]{
 background:#f0e1c6!important;color:#61451f!important;
 border-color:#cfaa71!important;box-shadow:inset 3px 0 0 #ad7d3e
}
.sidebar-brand{font-size:1.08rem;font-weight:770;color:#263344;margin:.15rem 0 1.05rem}
.sidebar-kicker{font-size:.74rem;letter-spacing:.04em;color:#7d7e80;margin:.9rem 0 .35rem}
.sidebar-note{font-size:.76rem;color:#687586;margin:.5rem 0}
h1,h2,h3{letter-spacing:-.014em;color:#27364a}
div[data-testid="stVerticalBlockBorderWrapper"]{border-radius:1rem}
.st-key-asset_performance [data-testid="stVerticalBlockBorderWrapper"]{border-color:#e8e2d8}
.st-key-asset_performance [data-testid="stSelectbox"]{max-width:170px}
.asset-hero{
 background:linear-gradient(120deg,#fff4e1,#fffdf8);color:#263344;
 border:1px solid #ead7b7;border-radius:.92rem;
 padding:.95rem 1.15rem;height:100%;min-height:9rem
}
.asset-hero .eyebrow{font-size:.81rem;color:#896b42;margin-bottom:.3rem;font-weight:700}
.asset-hero .amount{font-size:clamp(1.55rem,2.35vw,2.25rem);font-weight:780;letter-spacing:-.035em;line-height:1.2;overflow-wrap:anywhere}
.asset-hero .ccy{font-size:.8rem;font-weight:550;letter-spacing:0;color:#866b48}
.asset-hero .gain{font-size:.87rem;font-weight:650;margin-top:.5rem;color:#465566}
.asset-hero .stamp{font-size:.74rem;color:#7b7e82;margin-top:.46rem}
.asset-stat-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.6rem}
.asset-stat{border:1px solid #e5e2dc;border-radius:.75rem;background:#fffdfa;padding:.73rem .83rem;min-width:0}
.asset-stat .k{font-size:.75rem;color:#687586;margin-bottom:.35rem}
.asset-stat .v{font-size:clamp(.95rem,1.2vw,1.14rem);color:#263344;font-weight:720;overflow-wrap:anywhere}
.asset-stat .u{font-size:.7rem;font-weight:500;color:#7a8086}
.asset-secondary-note{font-size:.75rem;color:#7d8491;margin-top:.5rem}
.st-key-stock_monitor_selector button[kind="primary"],
.st-key-stock_monitor_selector button[data-testid="stBaseButton-primary"]{
 background:#f6ebd6!important;color:#704f23!important;
 border:1.5px solid #bc8d4f!important;
 box-shadow:0 0 0 2px rgba(188,141,79,.12)!important;
 font-weight:760!important
}
.st-key-stock_monitor_selector button[kind="secondary"],
.st-key-stock_monitor_selector button[data-testid="stBaseButton-secondary"]{
 background:#fffefa!important;border:1px solid #dfe3e6!important;color:#344255!important
}
.st-key-stock_monitor_selector button:hover{border-color:#c29a62!important}
@media(max-width:700px){
 section[data-testid="stSidebar"]{width:250px!important;min-width:250px!important}
 .asset-hero .amount{font-size:1.85rem}
 .asset-stat-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
 .asset-stat .v{font-size:1rem}
}
.funding-top{display:flex;justify-content:space-between;align-items:center;gap:0.7rem;flex-wrap:wrap;margin-bottom:0.75rem}
.funding-title{font-size:1.25rem;font-weight:750;letter-spacing:.01em}
.funding-overall{font-size:.88rem;font-weight:700;border-radius:2rem;padding:.35rem .75rem;border:1px solid #cbd5e1;background:rgba(148,163,184,.12)}
.funding-overall.red{color:#b91c1c;background:rgba(239,68,68,.10);border-color:rgba(239,68,68,.30)}
.funding-overall.neutral{color:inherit;background:rgba(148,163,184,.12)}
.funding-cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.55rem}
.funding-tile{border:1px solid #cbd5e1;border-radius:.9rem;padding:1rem 1.15rem;min-width:0;min-height:6.6rem}
.funding-tile .name{font-size:1rem;font-weight:700;letter-spacing:.015em}
.funding-tile .state{font-size:1.65rem;font-weight:800;margin-top:.33rem;white-space:nowrap}
.funding-tile.ok{background:#dcebe4;border-color:#c4ddd1;color:#155e45}
.funding-tile.ok .state{color:#155e45}
.funding-tile.unknown{background:#e3eaf5;border-color:#d4deed;color:#3e5579}
.funding-tile.unknown .state{color:#3e5579}
.funding-tile.red{background:#f7e2e3;border-color:#edc7cb;color:#a02b33}
.funding-tile.red .state{color:#a02b33}
.funding-foot{font-size:.78rem;opacity:.66;margin-top:.5rem}
.monitor-help{color:rgba(148,163,184,.9);font-size:.82rem}
div[data-testid="stButton"] button[kind="secondary"]{border-radius:.7rem}
@media (max-width:700px) {
 .funding-cards{gap:.3rem}
 .funding-tile{padding:.6rem .4rem;border-radius:.6rem;text-align:center;min-height:5rem}
 .funding-tile .name{font-size:.8rem}
 .funding-tile .state{font-size:1rem;white-space:normal}
 .funding-overall{font-size:.75rem}
}

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
    """Compact at-a-glance funding temperature; all technical details are optional."""
    from html import escape
    decision = funding_decision(macro)
    states = macro.get("categoryStates") if isinstance(macro.get("categoryStates"), dict) else {}
    score = macro.get("scoreRange")
    score_text = f"{score[0]}–{score[1]}" if isinstance(score, (list, tuple)) and len(score) == 2 else "未知"
    verdict = {"red": "紅燈｜觸發出場條件", "non_red": "非紅燈｜持續觀察", "unknown": "未知｜資料待確認"}.get(decision, "未知")
    asof = escape(str(macro.get("marketAsOf") or "未知"))

    def tile(key, title):
        raw = states.get(key)
        if raw in (1, "1", True):
            klass, label = "red", "已觸發"
        elif raw in (0, "0", False):
            klass, label = "ok", "未觸發"
        else:
            klass, label = "unknown", "未知"
        return (f'<div class="funding-tile {klass}"><div class="name">{title}</div>'
                f'<div class="state">{label}</div></div>')

    tiles = "".join(tile(key, name) for key, name in (
        ("rates", "利率"), ("conditions", "金融條件"), ("inflation", "通膨")))
    st.markdown(
        '<div class="funding-top"><span class="funding-title">全球資金溫度</span>'
        f'<span class="funding-overall {"red" if decision == "red" else "neutral"}">{verdict}</span></div>'
        f'<div class="funding-cards">{tiles}</div>'
        f'<div class="funding-foot">部分總經指標可能缺值；依三態規則判定。行情日 {asof}　·　風險觸發範圍 {score_text}／3</div>',
        unsafe_allow_html=True,
    )
    if decision == "red":
        st.error("已達主策略 A／B 資金紅燈出場條件；提醒不代表自動賣出。")
    elif decision == "unknown":
        st.warning("資金溫度無法判定，不能視為安全。")

    with st.expander("展開查看資金溫度原始數據及判斷規則", expanded=False):
        st.caption("非紅燈不等於全綠，未知不得當作未觸發。個股財報條件仍獨立評估。")
        st.caption("利率：實質殖利率 90 日升逾 0.50 個百分點，或政策利率升逾 0.25 個百分點。")
        st.caption("金融條件：NFCI 高於 0，或 90 日升逾 0.25 指數點。")
        st.caption("通膨：CPI 年增逾 4%，且近 3 個月年化高於年增率。")
        def number(value, digits=2, pct_value=False):
            if value in (None, ""):
                return "未知"
            try:
                return f"{float(value):.2%}" if pct_value else f"{float(value):.{digits}f}"
            except (TypeError, ValueError):
                return "未知"
        st.dataframe([
            {"指標": "實質殖利率", "目前值": number(macro.get("real_yield")),
             "90 日變化": number(macro.get("real_yield_90d_change"))},
            {"指標": "政策利率", "目前值": number(macro.get("fed_funds")),
             "90 日變化": number(macro.get("fed_funds_90d_change"))},
            {"指標": "金融條件 NFCI", "目前值": number(macro.get("financial_conditions"), 3),
             "90 日變化": number(macro.get("financial_conditions_90d_change"), 3)},
            {"指標": "CPI 年增", "目前值": number(macro.get("cpi_yoy"), pct_value=True), "90 日變化": "—"},
            {"指標": "CPI 近 3 月年化", "目前值": number(macro.get("cpi_3m_annualized"), pct_value=True), "90 日變化": "—"},
        ], hide_index=True, use_container_width=True)
        st.caption("觀測 " + str(macro.get("observedAt") or macro.get("calculatedAt") or "未知")
                   + "　·　規則 " + str(macro.get("ruleVersion") or "未知")
                   + ("　·　資料已過期，只供核對" if not macro.get("marketAsOf")
                      or data_too_old(macro["marketAsOf"]) else "　·　行情仍在可核對窗口"))


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
        if st.form_submit_button("建立組合", type="primary", disabled=st.session_state.get("_private_stale", False)):
            try:
                item = store.create_portfolio(name=name, market=market, strategy=strategy,
                                               currency=currency, note=note)
                st.session_state.portal_view = item["id"]
                st.success("已儲存至私人 Google Sheets：" + item["name"])
                invalidate_private_cache()
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
            if st.form_submit_button("儲存名稱與備註", disabled=st.session_state.get("_private_stale", False)):
                try:
                    changed = store.update_portfolio_profile(current["id"], name=new_name, note=new_note)
                    st.success("已更新" if changed else "沒有變更")
                    invalidate_private_cache()
                    st.rerun()
                except Exception as exc:
                    st.error("更新失敗：" + public_error(exc))
        with st.form("edit-strategy-" + current["id"]):
            new_strategy = st.selectbox("策略", list(STRATEGIES),
                                        index=list(STRATEGIES).index(current["strategy"])
                                        if current["strategy"] in STRATEGIES else 0)
            st.warning("變更策略會改變之後的出場與買入判斷。不會改股數、成本、交易紀錄、NAV 或建倉基準。")
            confirmed = st.checkbox("我確認只變更往後規則，不改既有持股資料")
            if st.form_submit_button("確認變更策略", disabled=st.session_state.get("_private_stale", False)):
                try:
                    changed = store.update_portfolio_strategy(current["id"], new_strategy, confirmed=confirmed)
                    st.success("策略已記錄變更" if changed else "策略沒有變更")
                    invalidate_private_cache()
                    st.rerun()
                except Exception as exc:
                    st.error("策略未變更：" + public_error(exc))
        st.subheader("封存投資組合")
        st.warning("有交易或歷史紀錄的組合不能永久刪除。封存後可以恢復。")
        with st.form("archive-" + current["id"]):
            archive_ok = st.checkbox("我確認封存「" + current["name"] + "」，不是刪除")
            if st.form_submit_button("封存這個組合", disabled=st.session_state.get("_private_stale", False)):
                try:
                    store.archive_portfolio(current["id"], confirmed=archive_ok)
                    st.session_state.portal_view = "manage"
                    st.success("已封存")
                    invalidate_private_cache()
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
        if right.button("恢復", key="restore-" + row["id"], disabled=st.session_state.get("_private_stale", False)):
            try:
                store.restore_portfolio(row["id"])
                st.session_state.portal_view = row["id"]
                invalidate_private_cache()
                st.rerun()
            except Exception as exc:
                st.error("無法恢復：" + public_error(exc))


@st.fragment
def render_stock_monitor(portfolio, valued, reviews, macro, transitions):
    """All details are prepared on page load; ticker selection reruns only here."""
    with st.container(border=True):
        st.subheader("個股出場監控")
        if portfolio["strategy"] not in ("A", "B"):
            st.info("非主策略 A／B：僅提供風險觀察，不套用主策略的強制出場規則。")
        if not valued["holdings"] and not valued["missing_prices"]:
            st.caption("目前沒有持股。")
        else:
            priorities = {"觸發出場條件": 0, "無法判定": 1, "資料待確認": 1, "未觸發出場條件": 2}
            all_holdings = sorted(valued["holdings"],
                                  key=lambda h: (priorities.get(reviews[h["symbol"]]["status"], 2), h["symbol"]))
            triggered = [h["symbol"] for h in all_holdings
                         if reviews[h["symbol"]]["status"] == "觸發出場條件"]
            unresolved = [h["symbol"] for h in all_holdings
                          if reviews[h["symbol"]]["status"] in ("無法判定", "資料待確認")]
            if triggered:
                st.error("⚠️ 優先檢查： " + "、".join(triggered) + " 觸發出場條件。")
            st.caption(f"觸發 {len(triggered)} 檔　·　待確認 {len(unresolved) + len(valued['missing_prices'])} 檔"
                       "　·　點選代號查看完整財報與歷史監控。")
            selection_key = "monitor_focus_" + portfolio["id"]

            def toggle_selected_symbol(key, symbol):
                # Streamlit executes callbacks BEFORE drawing the fragment.
                # This keeps the selected color and the detail panel synchronized.
                st.session_state[key] = None if st.session_state.get(key) == symbol else symbol

            focus = st.session_state.get(selection_key)
            with st.container(key="stock_monitor_selector"):
                buttons = st.columns(5)
                for i, item in enumerate(all_holdings):
                    symbol = item["symbol"]
                    status = reviews[symbol]["status"]
                    icon = "🔴" if status == "觸發出場條件" else ("🟠" if status in ("無法判定", "資料待確認") else "✓")
                    short = "出場" if status == "觸發出場條件" else ("待確認" if status in ("無法判定", "資料待確認") else "正常")
                    with buttons[i % 5]:
                        st.button(
                            f"{icon} {symbol} · {short}",
                            key="monitor-" + portfolio["id"] + "-" + symbol,
                            type="primary" if focus == symbol else "secondary",
                            use_container_width=True,
                            on_click=toggle_selected_symbol,
                            args=(selection_key, symbol),
                        )
            if valued["missing_prices"]:
                st.warning("無法確認同日收盤價：" + "、".join(valued["missing_prices"]))
            if focus in {h["symbol"] for h in all_holdings}:
                holding = next(h for h in all_holdings if h["symbol"] == focus)
                review = reviews[holding["symbol"]]
                st.markdown("#### " + holding["symbol"] + "｜" + review["status"])
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



def render_dashboard(store, portfolio, transactions, block, entry_batch, transitions, nav_cache):
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

    if portfolio["strategy"] in ("A", "B"):
        immediate = [h["symbol"] for h in valued["holdings"]
                     if reviews[h["symbol"]]["status"] == "觸發出場條件"]
        if immediate:
            st.error("🚨 持股出場警示｜" + "、".join(immediate)
                     + " 已觸發條件。請優先檢視下方個股監控（不會自動賣出）。")

    # GPT Sites-inspired structure: full-width verified NAV chart above all
    # account statistics. NAV is shown once in the lower summary block.
    with st.container(border=True, key="asset_performance"):
        from html import escape as safe_html
        st.subheader("資產與績效")
        graph_heading, period_picker = st.columns([3.5, 1], vertical_alignment="center")
        with graph_heading:
            st.markdown("**每日收盤淨值走勢**")
        with period_picker:
            period = st.selectbox(
                "績效區間", ["近 30 天", "近 90 天", "近 1 年", "全部"],
                index=3, key="nav_period_" + portfolio["id"],
                label_visibility="collapsed",
            )

        # Only plot verified dates from the initial, owner-authenticated sheet snapshot.
        nav_rows = [row for row in nav_cache if row["portfolio_id"] == portfolio["id"]]
        valid = [row for row in nav_rows if row.get("date") and row.get("nav") not in (None, "")]
        if valid:
            latest_date = max(date.fromisoformat(row["date"]) for row in valid)
            days = {"近 30 天": 30, "近 90 天": 90, "近 1 年": 365}.get(period)
            shown = (valid if days is None else [
                row for row in valid if date.fromisoformat(row["date"]) >= latest_date - timedelta(days=days)
            ])
            if shown:
                curve = pd.DataFrame([
                    {"date": pd.to_datetime(row["date"]), "nav": float(row["nav"])}
                    for row in shown
                ]).sort_values("date")
                lower = float(curve["nav"].min())
                upper = float(curve["nav"].max())
                padding = max((upper - lower) * .25, upper * .005, 1)
                chart = alt.Chart(curve).mark_line(
                    color="#b28b53", strokeWidth=3, point=alt.OverlayMarkDef(
                        color="#b28b53", filled=True, size=88)
                ).encode(
                    x=alt.X("date:T", title=None,
                            axis=alt.Axis(format="%m/%d", labelAngle=0, grid=False)),
                    y=alt.Y("nav:Q", title="淨值 (USD)",
                            scale=alt.Scale(domain=[max(0, lower - padding), upper + padding], zero=False),
                            axis=alt.Axis(format=",.0f", tickCount=5, grid=True)),
                    tooltip=[
                        alt.Tooltip("date:T", title="收盤日", format="%Y-%m-%d"),
                        alt.Tooltip("nav:Q", title="淨值", format=",.2f"),
                    ],
                ).properties(height=305)
                st.altair_chart(chart, use_container_width=True, theme=None)
                st.caption("截至 " + latest_date.isoformat()
                           + " · 僅含已核驗的交易日；縱軸為局部縮放，以利辨識日間變化。")
            gaps = weekday_gaps([row["date"] for row in valid])
            if gaps:
                st.warning("以下日期仍待核對：" + "、".join(gaps))
        else:
            st.info("尚無已核對的歷史淨值；沒有使用推測行情補值。")

        st.divider()

        def display_amount(value):
            if value is None:
                return "待確認"
            try:
                return f"{Decimal(str(value)):,.2f}"
            except (TypeError, ValueError, ArithmeticError):
                return "待確認"

        hero_col, stats_col = st.columns([1.12, 2.25], gap="medium")
        with hero_col:
            pnl_display = display_amount(cumulative)
            pnl_signed = ("待確認" if cumulative is None
                          else ("+" if cumulative > 0 else "") + pnl_display)
            return_display = "待確認" if returns is None else f"{returns:+.2%}"
            st.markdown(
                '<div class="asset-hero">'
                '<div class="eyebrow">目前帳戶總淨值</div>'
                f'<div class="amount">{safe_html(display_amount(nav_now))} '
                f'<span class="ccy">{safe_html(portfolio["currency"])}</span></div>'
                f'<div class="gain">累計損益 {safe_html(pnl_signed)}'
                f' · 報酬率 {safe_html(return_display)}</div>'
                f'<div class="stamp">最後行情日 {safe_html(market_asof or "未知")}</div>'
                '</div>', unsafe_allow_html=True
            )
        with stats_col:
            metrics = [
                ("淨投入本金", contributed),
                ("現金餘額", valued["cash"]),
                ("持股市值", None if valued["missing_prices"] else valued["market_value"]),
                ("已實現損益", valued["realized"]),
                ("未實現損益", unrealized),
            ]
            cards = "".join(
                '<div class="asset-stat">'
                f'<div class="k">{safe_html(label)}</div>'
                f'<div class="v">{safe_html(display_amount(number))} '
                f'<span class="u">{safe_html(portfolio["currency"])}</span></div>'
                '</div>'
                for label, number in metrics
            )
            st.markdown('<div class="asset-stat-grid">' + cards + '</div>', unsafe_allow_html=True)
        st.caption("最新淨值由私人帳本與已驗證收盤價計算；圖表為已保存的歷史淨值。"
                   "累計報酬率並非標準 TWR。")
        if valued["missing_prices"]:
            st.warning("這些持股尚無可驗證收盤價，資產與權重不估算："
                       + "、".join(valued["missing_prices"]))
        with st.expander("歷史淨值、資料來源與績效算法", expanded=False):
            if valid:
                linked = modified_dietz(transactions, portfolio["id"], nav_rows) or []
                if len(linked) >= 2:
                    st.caption("自 " + linked[-2]["date"] + " 至 " + linked[-1]["date"]
                               + " 的 Modified Dietz 區間估計 "
                               + f"{linked[-1]['subperiod_return']:+.2%}"
                               + " · 連乘估計 "
                               + f"{linked[-1]['modified_dietz']:+.2%}"
                               + "。不是標準 TWR。")
                st.dataframe([
                    {"日期": row["date"], "淨值": row["nav"],
                     "現金": row["cash"] or "未記錄",
                     "持股市值": row["equity_value"] or "未記錄",
                     "累計損益": row.get("cumulative_pnl") or "未記錄",
                     "來源": row["source"]}
                    for row in shown
                ], hide_index=True, use_container_width=True)
            else:
                st.caption("尚無歷史明細。")

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

    render_stock_monitor(portfolio, valued, reviews, macro, transitions)

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
            if st.form_submit_button("確認並寫入私人 Google Sheets", type="primary", disabled=st.session_state.get("_private_stale", False)):
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
                    invalidate_private_cache()
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
        if st.button("記錄本次監控變化至私人 Google Sheets", disabled=st.session_state.get("_private_stale", False)):
            try:
                current = portfolio_reviews([portfolio], transactions, {"markets": {portfolio["market"]: block}}, entry_batch)
                recorded = store.append_monitor_results(current)
                added = store.append_exit_transitions(current)
                st.success(f"已儲存 {len(recorded)} 筆狀態變化、{len(added)} 筆條件觸發或解除")
                invalidate_private_cache()
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


@st.cache_data(ttl=180, max_entries=3, show_spinner=False)
def cached_portfolio_records(research_batch):
    """One bounded private-Sheets read per cache window, not per Streamlit widget.

    Data are fetched ONLY after Google OIDC ownership verification. The result
    contains private holdings and must never be logged or published to GitHub.
    Clear this cache after a successful owner-authorized write.
    """
    store = private_store()
    rows = store.all_portfolios()
    active = [p for p in rows if str(p.get("archived") or "").lower() != "true"]
    archived = [p for p in rows if str(p.get("archived") or "").lower() == "true"]
    transactions = store.transactions()
    saved_baselines = store.entry_baselines()
    warnings = []

    # Historical SEC recovery is needed only for BUY lots that still lack a
    # verified baseline. No repeated SEC or Sheets writes on stock-detail clicks.
    official_pairs = {
        (str(b.get("portfolio_id") or ""), str(b.get("ledger_id") or ""))
        for b in saved_baselines if b.get("verification") == "OFFICIAL_FILING"
    }
    has_missing = any(
        str(tx.get("action") or "").upper() == "BUY"
        and (str(tx.get("portfolio_id") or ""), str(tx.get("id") or "")) not in official_pairs
        for tx in transactions
    )
    if has_missing:
        try:
            research_entry, _, _ = research_snapshot()
            appended = sync_entry_baselines(
                store, transactions, active, research_entry["snapshot"])
            if appended:
                saved_baselines = [*saved_baselines, *appended]
        except Exception as exc:
            warnings.append("部分建倉基準暫未補齊：" + public_error(exc))
    try:
        transactions = apply_official_baselines(transactions, saved_baselines)
    except Exception as exc:
        warnings.append("無法套用已存建倉基準：" + public_error(exc))

    return {
        "portfolios": active,
        "archived": archived,
        "transactions": transactions,
        "transitions": store.exit_transitions(),
        "nav": store.nav_snapshots(),
        "warnings": warnings,
    }


def invalidate_private_cache():
    """Call only after a successful owner-initiated mutation."""
    cached_portfolio_records.clear()
    st.session_state.pop("_last_good_private_snapshot", None)
    st.session_state["_private_stale"] = False


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
    private_data = cached_portfolio_records(str(entry["batch"]))
    st.session_state["_last_good_private_snapshot"] = private_data
    st.session_state["_private_stale"] = False
except Exception as exc:
    # 429: keep a clearly labelled read-only cached view within this session.
    if ("429" in str(exc) or "quota" in str(exc).lower()) and "_last_good_private_snapshot" in st.session_state:
        private_data = st.session_state["_last_good_private_snapshot"]
        st.session_state["_private_stale"] = True
        st.warning("Google Sheets 暫時達到讀取額度。現在顯示上次成功讀取的私人資料（僅供檢視，暫停所有寫入）。")
    else:
        st.error("讀取私人交易紀錄失敗，禁止建立建議或寫入新資料。")
        if "429" in str(exc) or "quota" in str(exc).lower():
            st.caption("Google Sheets 每分鐘讀取次數已達上限，請稍後重新整理；交易及歷史淨值沒有被刪除。")
        else:
            st.caption(public_error(exc))
        st.stop()

portfolios = private_data["portfolios"]
archived = private_data["archived"]
transactions = private_data["transactions"]
transitions = private_data["transitions"]
nav_cache = private_data["nav"]
for alert in private_data["warnings"]:
    st.warning(alert)
if st.session_state.get("_private_stale"):
    st.error("私人交易及組合變更目前暫停；僅顯示上次已成功載入的快取內容。")
live_reviews = portfolio_reviews(portfolios, transactions, snapshot, entry["batch"])

urgent = [row for row in live_reviews if row["status"] == "觸發出場條件"]
pending = [row for row in live_reviews if row["status"] in ("資料待確認", "無法判定")]

for message in research_issues:
    st.warning(message)

active_ids = {item["id"] for item in portfolios}
if "portal_view" not in st.session_state or (
        st.session_state.portal_view != "manage" and st.session_state.portal_view not in active_ids):
    st.session_state.portal_view = portfolios[0]["id"] if portfolios else "manage"

with st.sidebar:
    st.markdown('<div class="sidebar-brand">私人投資組合</div>',
                unsafe_allow_html=True)
    if st.button("編輯投資組合",
                 type="primary" if st.session_state.portal_view == "manage" else "secondary",
                 use_container_width=True):
        st.session_state.portal_view = "manage"
        st.rerun()
    st.markdown('<div class="sidebar-kicker">我的組合</div>',
                unsafe_allow_html=True)
    for item in portfolios:
        picked = st.session_state.portal_view == item["id"]
        if st.button(item["name"], key="nav-" + item["id"],
                     type="primary" if picked else "secondary",
                     use_container_width=True):
            st.session_state.portal_view = item["id"]
            st.rerun()
    if not portfolios:
        st.caption("尚未建立投資組合。")
    if urgent:
        st.error("⚠️ 出場訊號：" + "、".join(row["symbol"] for row in urgent[:8]))
    with st.expander("資料來源與帳戶", expanded=False):
        st.caption("已授權私人登入")
        st.caption(owner_email)
        st.caption("研究來源：" + research_source)
        st.caption("研究批次：" + str(entry["batch"]))
        st.caption("美股行情日：" +
                   str(snapshot["markets"]["US"]["macro"].get("marketAsOf") or "未知"))
        st.caption("台股行情日：" +
                   str(snapshot["markets"]["TW"]["macro"].get("marketAsOf") or "未知"))
        if st.button("重新讀取研究與組合資料", use_container_width=True):
            research_snapshot.clear()
            cached_portfolio_records.clear()
            st.rerun()
        if st.button("登出", use_container_width=True):
            st.logout()

if st.session_state.portal_view == "manage":
    render_manage(store, portfolios, archived)
    st.stop()

portfolio = next(item for item in portfolios if item["id"] == st.session_state.portal_view)
block = snapshot["markets"][portfolio["market"]]
try:
    render_dashboard(store, portfolio, transactions, block, entry["batch"], transitions, nav_cache)
except Exception as exc:
    st.error("這個組合目前無法完成對帳或顯示，已停止分析：" + public_error(exc))
    st.stop()
