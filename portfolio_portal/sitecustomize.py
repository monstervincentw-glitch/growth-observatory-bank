"""Loaded by the portfolio process. Surface OAuth failures without logging secrets."""
from urllib.parse import quote

from oauth_messages import oauth_error_code


def _sets_user_cookie(response):
    for key, value in getattr(response, "raw_headers", []) or []:
        if key.lower() != b"set-cookie":
            continue
        text = value.decode("latin1", "ignore")
        name, _, rest = text.partition("=")
        if name.strip() != "_streamlit_user":
            continue
        cookie_value = rest.split(";", 1)[0]
        if cookie_value and "Max-Age=0" not in text:
            return True
    return False


def _install():
    try:
        from streamlit.web.server.starlette import starlette_auth_routes as routes
        from starlette.responses import RedirectResponse
    except Exception:
        return
    if getattr(routes, "_portfolio_oauth_hook", False):
        return
    routes._portfolio_oauth_hook = True
    original = routes._auth_callback

    def fail(raw):
        code = quote(oauth_error_code(raw))
        print('{"oauth_error":"' + code + '"}', flush=True)
        return RedirectResponse("/?oauth_error=" + code, status_code=302)

    async def wrapped(request, base_url):
        provider_error = request.query_params.get("error")
        if provider_error:
            return fail(provider_error)
        response = await original(request, base_url)
        if not request.query_params.get("code"):
            return response
        if _sets_user_cookie(response):
            return response
        if request.cookies.get("_streamlit_session"):
            return fail("token_exchange")
        return fail("state_mismatch")

    routes._auth_callback = wrapped


_install()
