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

"""Exchanges a user's Auth0 access token for their Google access token.

Uses Auth0 Token Vault (access token exchange). Auth0 stores the user's Google
refresh token and returns a short-lived Google access token, so this server
never holds a long-lived Google credential. Tokens are cached in memory per
user until shortly before they expire.

Ported from the GA4 MCP server's ``token_vault.py``. The exchange is
synchronous here because Google Ads tools run in worker threads.
"""

import logging
import os
import threading
import time

import httpx
from fastmcp.exceptions import ToolError

ADWORDS_SCOPE = "https://www.googleapis.com/auth/adwords"

_GRANT_TYPE = (
    "urn:auth0:params:oauth:grant-type:token-exchange:"
    "federated-connection-access-token"
)
_SUBJECT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
_REQUESTED_TOKEN_TYPE = (
    "http://auth0.com/oauth/token-type/federated-connection-access-token"
)

# Refresh a cached token this many seconds before it expires.
_EXPIRY_MARGIN_SECONDS = 120

logger = logging.getLogger(__name__)

# Maps Auth0 user ID (sub) -> (Google access token, expiry as time.monotonic()).
_cache: dict[str, tuple[str, float]] = {}
_cache_lock = threading.Lock()


class GoogleTokenError(ToolError):
    """The user's Google token could not be obtained.

    The message is safe to show to the user.
    """


def public_base_url() -> str:
    """Returns this server's public URL, without a trailing slash."""
    return os.environ.get("GOOGLE_ADS_MCP_BASE_URL", "").strip().rstrip("/")


def google_connection() -> str:
    """Returns the name of the Auth0 Google connection."""
    return (
        os.environ.get("AUTH0_GOOGLE_CONNECTION", "").strip() or "google-oauth2"
    )


def _connect_hint() -> str:
    return (
        f"Open {public_base_url()}/connect in your browser, sign in with the "
        "Google account that has access to your Google Ads accounts, allow "
        "Google Ads access, then try again. This is needed once per user."
    )


def _config() -> tuple[str, str, str]:
    domain = os.environ.get("AUTH0_DOMAIN", "").strip()
    client_id = os.environ.get("AUTH0_TOKEN_VAULT_CLIENT_ID", "").strip()
    client_secret = os.environ.get(
        "AUTH0_TOKEN_VAULT_CLIENT_SECRET", ""
    ).strip()
    if not (domain and client_id and client_secret):
        logger.error(
            "Token Vault is not configured: set AUTH0_DOMAIN, "
            "AUTH0_TOKEN_VAULT_CLIENT_ID and AUTH0_TOKEN_VAULT_CLIENT_SECRET."
        )
        raise GoogleTokenError(
            "The server is not configured for Google sign-in."
        )
    return domain, client_id, client_secret


def get_google_access_token(
    user_id: str,
    auth0_access_token: str,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Returns a Google access token for the signed-in user.

    Args:
      user_id: The Auth0 user ID (`sub` claim), used as the cache key.
      auth0_access_token: The user's verified Auth0 access token.
      transport: Optional HTTP transport, for tests.

    Raises:
      GoogleTokenError: if no usable Google token is available.
    """
    with _cache_lock:
        cached = _cache.get(user_id)
    if cached and cached[1] > time.monotonic():
        return cached[0]

    domain, client_id, client_secret = _config()
    try:
        with httpx.Client(transport=transport, timeout=10) as http:
            response = http.post(
                f"https://{domain}/oauth/token",
                json={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": _GRANT_TYPE,
                    "subject_token": auth0_access_token,
                    "subject_token_type": _SUBJECT_TOKEN_TYPE,
                    "requested_token_type": _REQUESTED_TOKEN_TYPE,
                    "connection": google_connection(),
                },
            )
    except httpx.HTTPError as exc:
        logger.warning("Token Vault request failed: %s", type(exc).__name__)
        raise GoogleTokenError(
            "Could not reach the sign-in service. Try again shortly."
        ) from None

    if response.status_code != 200:
        _log_auth0_error(response)
        if response.status_code in (400, 401, 403):
            raise GoogleTokenError(
                f"Your Google account is not connected. {_connect_hint()}"
            )
        raise GoogleTokenError(
            "The sign-in service returned an error. Try again shortly."
        )

    try:
        body = response.json()
    except ValueError:
        body = {}
    token = body.get("access_token")
    if not token:
        logger.error("Token Vault response had no access_token.")
        raise GoogleTokenError(
            "The sign-in service returned an error. Try again shortly."
        )

    granted = body.get("scope")
    if granted is not None and ADWORDS_SCOPE not in granted.split():
        logger.warning("Token Vault token lacks the adwords scope.")
        raise GoogleTokenError(
            f"Google Ads access was not granted. {_connect_hint()}"
        )

    ttl = int(body.get("expires_in", 0)) - _EXPIRY_MARGIN_SECONDS
    if ttl > 0:
        now = time.monotonic()
        with _cache_lock:
            # Drop expired entries so the cache doesn't grow without bound.
            for key in [k for k, (_, exp) in _cache.items() if exp <= now]:
                del _cache[key]
            _cache[user_id] = (token, now + ttl)
    return token


def _log_auth0_error(response: httpx.Response) -> None:
    """Logs Auth0's error code and description; never tokens."""
    try:
        body = response.json()
        detail = f"{body.get('error')}: {body.get('error_description')}"
    except ValueError:
        detail = "non-JSON body"
    logger.warning(
        "Token Vault exchange failed (%s): %s", response.status_code, detail
    )
