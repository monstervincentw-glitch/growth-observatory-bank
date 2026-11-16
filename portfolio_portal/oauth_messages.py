"""Short public labels for a failed Google login. No tokens and no secrets."""

ALLOWED_OAUTH_ERRORS = {
    "access_denied",
    "invalid_client",
    "unauthorized_client",
    "invalid_request",
    "redirect_uri_mismatch",
    "invalid_scope",
    "server_error",
    "temporarily_unavailable",
    "disallowed_useragent",
    "interaction_required",
    "login_required",
    "consent_required",
    "state_mismatch",
    "token_exchange",
    "unknown",
}

_MESSAGES = {
    "access_denied": "Google 拒絕這次登入。OAuth 同意畫面若仍是「測試」，請把你用來登入的同一個 Gmail 加為測試使用者，或你在 Google 畫面上按了取消。",
    "disallowed_useragent": "Google 不允許在 App 內建瀏覽器登入。請用 Safari 或 Chrome 打開這個網站網址。",
    "redirect_uri_mismatch": "重新導向網址不一致。Google 用戶端只應登記 https://growth-portfolio-private-v1.onrender.com/oauth2callback ，結尾不要加斜線。",
    "invalid_client": "Google 不接受這組用戶端。請只在 Render 核對 GOOGLE_OIDC_CLIENT_ID 與 GOOGLE_OIDC_CLIENT_SECRET，不要覆寫其他變數，也不要把密鑰貼到聊天。",
    "unauthorized_client": "這個 OAuth 用戶端不能用這個登入方式。請確認用戶端類型是「網頁應用程式」。",
    "invalid_request": "Google 認為登入要求不完整。請用 Safari 或 Chrome 重新開啟網站後再登入一次。",
    "state_mismatch": "登入狀態沒有跟著瀏覽器回來。請關掉無痕模式，用 Safari 或 Chrome 重新打開網站，不要用 App 內建瀏覽器。",
    "token_exchange": "Google 已回到網站，但沒有完成登入。最常見是 Client Secret 多了空白或與用戶端不符。請只更正 Render 上的 GOOGLE_OIDC_CLIENT_SECRET。",
    "server_error": "Google 登入服務暫時失敗。請稍後再試。",
    "temporarily_unavailable": "Google 登入服務暫時失敗。請稍後再試。",
}


def oauth_error_code(raw):
    text = str(raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    cleaned = "".join(ch for ch in text if ch.isascii() and (ch.isalnum() or ch == "_"))[:40]
    if cleaned in ALLOWED_OAUTH_ERRORS:
        return cleaned
    return "unknown"


def oauth_error_message(raw):
    code = oauth_error_code(raw)
    return _MESSAGES.get(code, "Google 登入沒有完成。請用 Safari 或 Chrome 重新開啟網站。不要把畫面以外的金鑰貼到聊天。")
