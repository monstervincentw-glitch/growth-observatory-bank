"""Render starter: never expose portfolio content without a verified Google login.

When the owner hasn't configured the required environment variables, serve a
configuration-only landing page that contains no personal holdings or secrets.
Once configured, securely build a private Streamlit secrets.toml in the
ephemeral container and enable the owner-only app.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
import subprocess
import sys

REQUIRED = [
    "PORTFOLIO_OWNER_EMAIL", "GOOGLE_OIDC_CLIENT_ID",
    "GOOGLE_OIDC_CLIENT_SECRET", "STREAMLIT_COOKIE_SECRET",
    "GOOGLE_SERVICE_ACCOUNT_JSON", "PRIVATE_PORTFOLIO_SHEET_ID",
]
BANK_SHEET_ID = "1I9Alvyhg4UZXi-AdylJN0pjiLHSMzZUleFK0vsXG1K0"


def conf_line(k, v):
    return f"{k} = {json.dumps(str(v), ensure_ascii=False)}"


def create_auth_config():
    missing=[key for key in REQUIRED if not os.environ.get(key)]
    if missing:
        return False
    sa=json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    if sa.get("type")!="service_account":
        raise ValueError("Invalid Google service account")
    for field in ("client_email","private_key","token_uri","project_id"):
        if not sa.get(field):
            raise ValueError("Service account missing required field")
    base=os.environ.get("PORTFOLIO_PUBLIC_URL",
                       "https://growth-portfolio-private-v1.onrender.com").rstrip("/")
    if not base.startswith("https://"):
        raise ValueError("Google OIDC redirect must use HTTPS")
    private_id = os.environ["PRIVATE_PORTFOLIO_SHEET_ID"]
    if private_id == BANK_SHEET_ID:
        raise ValueError("Refusing to use the bank research sheet as the private ledger")
    target=Path(".streamlit/secrets.toml")
    target.parent.mkdir(parents=True,exist_ok=True)
    lines=[
        conf_line("owner_email",os.environ["PORTFOLIO_OWNER_EMAIL"]),
        conf_line("private_portfolio_sheet_id", private_id),
        "",
        "[auth]",
        conf_line("redirect_uri",base+"/oauth2callback"),
        conf_line("cookie_secret",os.environ["STREAMLIT_COOKIE_SECRET"]),
        conf_line("client_id",os.environ["GOOGLE_OIDC_CLIENT_ID"]),
        conf_line("client_secret",os.environ["GOOGLE_OIDC_CLIENT_SECRET"]),
        conf_line("server_metadata_url","https://accounts.google.com/.well-known/openid-configuration"),
        "",
        "[google_service_account]",
    ]
    for key,value in sa.items():
        if isinstance(value,str):
            lines.append(conf_line(key,value))
    target.write_text("\n".join(lines)+"\n",encoding="utf-8")
    os.chmod(target,0o600)
    return True


def taipei_slot(now):
    """US research lands in the Taipei morning; TW research lands in the evening."""
    minute = (now.hour, now.minute)
    if minute >= (6, 40) and now.hour < 8:
        return now.date().isoformat() + "-us"
    if minute >= (21, 40) and now.hour < 23:
        return now.date().isoformat() + "-tw"
    return None


def _safe_monitor():
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from monitor_job import run
        run()
    except Exception as exc:
        print(json.dumps({"result": "monitor_error", "error": exc.__class__.__name__}), flush=True)


def monitor_loop():
    import time
    from zoneinfo import ZoneInfo
    time.sleep(20)
    _safe_monitor()
    seen = set()
    zone = ZoneInfo("Asia/Taipei")
    while True:
        slot = taipei_slot(datetime.now(zone))
        if slot and slot not in seen:
            _safe_monitor()
            seen.add(slot)
        time.sleep(60)


def main():
    secure = create_auth_config()
    if secure:
        import threading
        threading.Thread(target=monitor_loop, daemon=True).start()
    mode = "app.py" if secure else "setup_landing.py"
    port=os.environ.get("PORT","10000")
    subprocess.run(
        [sys.executable,"-m","streamlit","run",f"portfolio_portal/{mode}",
         "--server.address","0.0.0.0","--server.port",port,
         "--server.headless","true"],
        check=True,
    )


if __name__=="__main__":
    main()
