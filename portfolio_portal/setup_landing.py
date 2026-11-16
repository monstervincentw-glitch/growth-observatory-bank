"""Public setup status, no credentials and no personal holdings."""
import streamlit as st

st.set_page_config(page_title="私人投資組合中心｜啟用登入",page_icon="🔐",layout="centered")
st.title("🔐 私人投資組合中心")
st.info("獨立網站已部署。為保護真實持股，目前尚未啟用 Google 登入及私人資料連線。")
st.write("你的個人資產、持股及交易紀錄不會顯示在未完成身分驗證的網站。")
st.subheader("啟用正式版")
st.markdown("""
1. 在你自己的 Google Cloud 建立 Google OAuth 網頁應用程式。
2. 授權網址須包含這個網站網域的 `/oauth2callback`。
3. 建立 Google Sheets 服務帳號，授權它讀取銀行版研究表與私人投資組合表。
4. 在 Render 此服務的 **Environment** 頁設定：
   - `PORTFOLIO_OWNER_EMAIL`：唯一允許登入的 Google 帳號
   - `GOOGLE_OIDC_CLIENT_ID` 與 `GOOGLE_OIDC_CLIENT_SECRET`
   - `STREAMLIT_COOKIE_SECRET`：安全隨機字串
   - `GOOGLE_SERVICE_ACCOUNT_JSON`：服務帳號 JSON（只貼在 Render 的祕密環境變數，不放 GitHub）
   - `PRIVATE_PORTFOLIO_SHEET_ID`：你自己管理的私人 Google Sheet ID
5. 服務重新部署後會進入 Google 登入畫面。
""")
st.caption("Google Sheets 資料將在完成身分驗證後顯示；未授權前不提供任何個人資料。")
st.error("未完成授權前，網站不提供真實持股明細、淨值或交易功能。")
