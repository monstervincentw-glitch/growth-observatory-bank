"""Render starter: never expose portfolio content without a verified Google login.

When the owner hasn't configured the required environment variables, serve a
configuration-only landing page that contains no personal holdings or secrets.
Once configured, securely build a private Streamlit secrets.toml in the
ephemeral container and enable the owner-only app.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

REQUIRED = [
    "PORTFOLIO_OWNER_EMAIL", "GOOGLE_OIDC_CLIENT_ID",
    "GOOGLE_OIDC_CLIENT_SECRET", "STREAMLIT_COOKIE_SECRET",
    "GOOGLE_SERVICE_ACCOUNT_JSON", "PRIVATE_PORTFOLIO_SHEET_ID",
]
PRIVATE_SHEET_ID = os.environ.get("PRIVATE_PORTFOLIO_SHEET_ID", "")


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
    target=Path(".streamlit/secrets.toml")
    target.parent.mkdir(parents=True,exist_ok=True)
    # Safe mode: failure generating secrets means the private app never starts.
    lines=[
        conf_line("owner_email",os.environ["PORTFOLIO_OWNER_EMAIL"]),
        conf_line("private_portfolio_sheet_id",PRIVATE_SHEET_ID),
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


def main():
    secure = create_auth_config()
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
