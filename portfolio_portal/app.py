"""Private owner-only investment dashboard (Streamlit + Google OIDC + Sheets).

Deploy this file as an independent Streamlit app from a PRIVATE GitHub repo.
It never places portfolio data or credentials in the public bank repository.
"""
from __future__ import annotations
from datetime import date, timedelta
from decimal import Decimal
import json

import pandas as pd
import streamlit as st

from core import buy_plan, compute_account, contributions_through, divergence_plan, modified_dietz, owner_allowed, portfolio_value
from monitor import portfolio_reviews, review_holding, data_too_old
from oauth_messages import oauth_error_message
from research import latest_research
from storage import PortfolioSheets, sheets_client

st.set_page_config(page_title="我的投資組合｜私人版", page_icon="🔐",
                   layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
@media (max-width: 700px) {
  .block-container { padding-top: 0.8rem; padding-left: 0.6rem; padding-right: 0.6rem; }
  div[data-testid=\"stHorizontalBlock\"] { flex-wrap: wrap; gap: 0.4rem; }
  div[data-testid=\"column\"] { flex: 1 1 46% !important; min-width: 140px; }
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


owner_email = auth_gate()
