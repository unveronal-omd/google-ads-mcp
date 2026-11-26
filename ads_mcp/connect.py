# Copyright 2026 Google LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""One-time "Connect Google" flow (Auth0 Connected Accounts for Token Vault).

Signing in to the MCP server does not store the user's Google token in Token
Vault, so the MCP client's sign-in runs this flow first:

0. /authorize        -> the MCP client's OAuth request (this server is the
                        advertised authorization server). The request is
                        saved and the flow below starts. /connect starts the
                        same flow without an MCP client.
1. /connect          -> Auth0 login (Google) for a My Account API token.
2. /connect/callback -> exchange the code, start a connected-accounts session
                        requesting the adwords scope, redirect to Google.
3. /connect/complete -> finish the session; Auth0 stores the Google tokens.
                        If started from /authorize, continue to Auth0's
                        /authorize with the saved request, which completes
                        silently (the user is already signed in).

Flow state lives in a short-lived signed cookie, so any instance can handle
any step. Ported from the GA4 MCP server's ``connect.py``.
"""

import base64
import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import time
from urllib.parse import urlencode

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from ads_mcp.token_vault import (
    ADWORDS_SCOPE,
    google_connection,
    public_base_url,
)

_COOKIE = "ads_connect"
_COOKIE_MAX_AGE = 600
_MY_ACCOUNT_SCOPES = "openid email create:me:connected_accounts"

logger = logging.getLogger(__name__)

# Moves a connect_code delivered in the URL fragment into the query string.
_FRAGMENT_PAGE = """<!doctype html><meta charset="utf-8"><title>Connecting…</title>
<script>
var p = new URLSearchParams(location.hash.slice(1));
if (p.get("connect_code")) { location.replace(location.pathname + "?" + p.toString()); }
else { document.write("Missing connect_code. Start again at /connect."); }
</script>"""


def _settings() -> dict:
    # Fixed (not taken from the Host header) so it always matches the
    # callback URLs registered in Auth0.
    base = public_base_url()
    return {
        "domain": os.environ.get("AUTH0_DOMAIN", "").strip(),
        "client_id": os.environ.get("AUTH0_CONNECT_CLIENT_ID", "").strip(),
        "client_secret": os.environ.get(
            "AUTH0_CONNECT_CLIENT_SECRET", ""
        ).strip(),
        "connection": google_connection(),
        "callback_url": f"{base}/connect/callback",
        "complete_url": f"{base}/connect/complete",
    }


def _configured(s: dict) -> bool:
    return bool(s["domain"] and s["client_id"] and s["client_secret"])


def _page(title: str, message: str, status: int = 200) -> HTMLResponse:
    body = (
        '<!doctype html><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width">'
        f"<title>{html.escape(title)}</title>"
        '<body style="font-family:system-ui;max-width:32rem;margin:3rem auto;'
        'padding:0 1rem">'
        f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>"
    )
    return HTMLResponse(body, status_code=status)


def _fail(reason: str) -> HTMLResponse:
    logger.warning("Connect flow failed: %s", reason)
    response = _page(
        "Could not connect Google",
        "Something went wrong. Close this tab and open the connect link "
        "again. If it keeps failing, contact the server administrator.",
        status=400,
    )
    response.delete_cookie(_COOKIE, path="/connect")
    return response


def _sign(data: dict, key: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    mac = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{mac}"


def _unsign(value: str | None, key: str) -> dict | None:
    if not value or "." not in value:
        return None
    payload, mac = value.rsplit(".", 1)
    expected = hmac.new(
        key.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("exp", 0) < time.time():
        return None
    return data


def _with_cookie(response: Response, data: dict, key: str) -> Response:
    data = {**data, "exp": int(time.time()) + _COOKIE_MAX_AGE}
    response.set_cookie(
        _COOKIE,
        _sign(data, key),
        max_age=_COOKIE_MAX_AGE,
        path="/connect",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _begin(s: dict, resume: dict | None = None) -> Response:
    """Redirects to Auth0 login for a My Account API token."""
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": s["client_id"],
            "redirect_uri": s["callback_url"],
            "scope": _MY_ACCOUNT_SCOPES,
            "audience": f"https://{s['domain']}/me/",
            "connection": s["connection"],
            "state": state,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    response = RedirectResponse(f"https://{s['domain']}/authorize?{query}")
    flow = {"state": state, "verifier": verifier}
    if resume:
        flow["resume"] = resume
    return _with_cookie(response, flow, s["client_secret"])


async def connect_start(request: Request) -> Response:
    """Step 1 (manual): sign the user in (Google) for a My Account token."""
    s = _settings()
    if not _configured(s):
        return _fail("AUTH0_DOMAIN or AUTH0_CONNECT_CLIENT_ID/SECRET not set")
    return _begin(s)


async def authorize(request: Request) -> Response:
    """Step 0: an MCP client's sign-in; connect Google first, then resume.

    The request is forwarded to Auth0 unchanged at the end, so Auth0 still
    validates the client ID, redirect URI and PKCE.
    """
    s = _settings()
    if not _configured(s):
        return _fail("AUTH0_DOMAIN or AUTH0_CONNECT_CLIENT_ID/SECRET not set")
    resume = dict(request.query_params)
    if not (resume.get("client_id") and resume.get("redirect_uri")):
        return _fail("authorize request without client_id or redirect_uri")
    return _begin(s, resume)


async def connect_callback(request: Request) -> Response:
    """Step 2: get the My Account token and start connecting Google."""
    s = _settings()
    if not _configured(s):
        return _fail("AUTH0_DOMAIN or AUTH0_CONNECT_CLIENT_ID/SECRET not set")
    flow = _unsign(request.cookies.get(_COOKIE), s["client_secret"])
    params = request.query_params
    if flow is None or params.get("state") != flow.get("state"):
        return _fail("missing, expired or mismatched state at callback")
    if "error" in params:
        return _fail(f"Auth0 login error: {params.get('error')}")

    async with httpx.AsyncClient(timeout=10) as http:
        token_response = await http.post(
            f"https://{s['domain']}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": s["client_id"],
                "client_secret": s["client_secret"],
                "code": params.get("code", ""),
                "redirect_uri": s["callback_url"],
                "code_verifier": flow["verifier"],
            },
        )
        if token_response.status_code != 200:
            return _fail(f"code exchange {_auth0_error(token_response)}")
        tokens = token_response.json()
        my_account_token = tokens["access_token"]

        connect_state = secrets.token_urlsafe(24)
        connect_body = {
            "connection": s["connection"],
            "redirect_uri": s["complete_url"],
            "state": connect_state,
            "scopes": ["openid", "profile", ADWORDS_SCOPE],
        }
        # Pre-select the Google account just used to sign in, so Google
        # doesn't show its account chooser a second time.
        email = _id_token_email(tokens.get("id_token"))
        if email:
            connect_body["authorization_params"] = {"login_hint": email}
        connect_response = await http.post(
            f"https://{s['domain']}/me/v1/connected-accounts/connect",
            headers={"Authorization": f"Bearer {my_account_token}"},
            json=connect_body,
        )
    if connect_response.status_code not in (200, 201):
        return _fail(f"connect {_auth0_error(connect_response)}")
    body = connect_response.json()
    ticket = body["connect_params"]["ticket"]
    response = RedirectResponse(
        f"{body['connect_uri']}?{urlencode({'ticket': ticket})}"
    )
    next_flow = {
        "connect_state": connect_state,
        "auth_session": body["auth_session"],
        "token": my_account_token,
    }
    if flow.get("resume"):
        next_flow["resume"] = flow["resume"]
    return _with_cookie(response, next_flow, s["client_secret"])


async def connect_complete(request: Request) -> Response:
    """Step 3: finish connecting; Auth0 stores the user's Google tokens."""
    params = request.query_params
    if "connect_code" not in params and "error" not in params:
        return HTMLResponse(_FRAGMENT_PAGE)
    s = _settings()
    if not _configured(s):
        return _fail("AUTH0_DOMAIN or AUTH0_CONNECT_CLIENT_ID/SECRET not set")
    flow = _unsign(request.cookies.get(_COOKIE), s["client_secret"])
    if flow is None or "auth_session" not in flow:
        return _fail("missing or expired session at complete")
    if params.get("state") and params.get("state") != flow["connect_state"]:
        return _fail("state mismatch at complete")
    if "error" in params:
        return _fail(f"Google/Auth0 error: {params.get('error')}")

    async with httpx.AsyncClient(timeout=10) as http:
        complete_response = await http.post(
            f"https://{s['domain']}/me/v1/connected-accounts/complete",
            headers={"Authorization": f"Bearer {flow['token']}"},
            json={
                "auth_session": flow["auth_session"],
                "connect_code": params["connect_code"],
                "redirect_uri": s["complete_url"],
            },
        )
    if complete_response.status_code not in (200, 201):
        return _fail(f"complete {_auth0_error(complete_response)}")
    scopes = complete_response.json().get("scopes") or []
    if ADWORDS_SCOPE not in scopes:
        logger.warning("Connected account lacks the adwords scope.")
        response = _page(
            "Google Ads access missing",
            "Your Google account was connected, but Google Ads access was "
            "not allowed. Open the connect link again and tick the Google "
            "Ads permission.",
            status=400,
        )
    elif flow.get("resume"):
        logger.info("Connected account created; resuming sign-in.")
        resume = {"connection": s["connection"], **flow["resume"]}
        response = RedirectResponse(
            f"https://{s['domain']}/authorize?{urlencode(resume)}"
        )
    else:
        logger.info("Connected account created.")
        response = _page(
            "Google account connected",
            "You can close this tab and go back to Claude.",
        )
    response.delete_cookie(_COOKIE, path="/connect")
    return response


def _id_token_email(id_token: str | None) -> str | None:
    """Reads the email claim of an ID token.

    The signature is not checked: the token comes straight from Auth0's token
    endpoint over TLS, and the email is only used as a login hint.
    """
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "==="))
        email = claims.get("email")
        return email if isinstance(email, str) and email else None
    except (AttributeError, IndexError, ValueError):
        return None


def _auth0_error(response: httpx.Response) -> str:
    """Describes an Auth0 error by status and error code; never tokens."""
    try:
        body = response.json()
        detail = body.get("error") or body.get("title") or "unknown"
        description = body.get("error_description") or body.get("detail") or ""
    except ValueError:
        detail, description = "non-JSON body", ""
    return f"failed ({response.status_code}): {detail} {description}".strip()


routes = [
    Route("/authorize", endpoint=authorize, methods=["GET"]),
    Route("/connect", endpoint=connect_start, methods=["GET"]),
    Route("/connect/callback", endpoint=connect_callback, methods=["GET"]),
    Route("/connect/complete", endpoint=connect_complete, methods=["GET"]),
]
