"""Public setup status. No credentials and no personal holdings."""
import streamlit as st

REDIRECT = "https://growth-portfolio-private-v1.onrender.com/oauth2callback"

st.set_page_config(page_title="私人投資組合中心｜啟用登入", page_icon="🔐", layout="centered")
st.title("私人投資組合中心")
st.info("網站已在原本的 Render 服務上。真實持股仍鎖著，因為 Google 登入與服務帳號還沒設完。")
st.write("未完成驗證前，這裡不會顯示資產、持股、淨值或交易。")

st.subheader("還差這三個官方頁面")
st.markdown(f"""
1. [Google 驗證畫面](https://console.cloud.google.com/auth/audience)：使用者類型選外部、發布狀態維持「測試」，並把你的 Gmail 加為測試使用者。
2. [Google OAuth 用戶端](https://console.cloud.google.com/auth/clients)：建立「網頁應用程式」。已授權的重新導向 URI 只填 `{REDIRECT}`。
3. [服務帳號](https://console.cloud.google.com/iam-admin/serviceaccounts)：建立帳號並下載 JSON 金鑰。私人持股表請以「編輯者」分享給服務帳號信箱；銀行版研究表只給「檢視者」。
4. [Render Environment](https://dashboard.render.com/web/srv-dbt82tu7bikc73datrkg/env)：只在這個頁面貼上變數，然後手動重新部署。不要把金鑰貼到聊天、GitHub 或試算表。
""")
st.code("""PORTFOLIO_OWNER_EMAIL
GOOGLE_OIDC_CLIENT_ID
GOOGLE_OIDC_CLIENT_SECRET
STREAMLIT_COOKIE_SECRET
GOOGLE_SERVICE_ACCOUNT_JSON
PRIVATE_PORTFOLIO_SHEET_ID""", language="text")
st.caption("已存在的 PORTFOLIO_OWNER_EMAIL、PRIVATE_PORTFOLIO_SHEET_ID、STREAMLIT_COOKIE_SECRET 不要覆蓋。只補尚未設定的 GOOGLE_OIDC_CLIENT_ID、GOOGLE_OIDC_CLIENT_SECRET 與 GOOGLE_SERVICE_ACCOUNT_JSON。")
st.error("金鑰未設定完成前，這個頁面就是全部。登入與持股會在重新部署成功後才出現。")
