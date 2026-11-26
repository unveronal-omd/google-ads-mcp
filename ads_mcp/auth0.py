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

"""Auth0 sign-in with per-user Google tokens (GOOGLE_ADS_MCP_AUTH_PROVIDER=auth0).

Mirrors the GA4 MCP server's ``server.py``:

- `/mcp` accepts only Auth0 JWTs for this server's API (signature, issuer,
  audience) that carry the `use:mcp` scope.
- The protected-resource metadata names this server as the authorization
  server, and `/.well-known/oauth-authorization-server` points MCP clients to
  this server's `/authorize` (which runs the Google connect step in
  ``connect.py``). Tokens and client registration stay at Auth0.
"""

import os

from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ads_mcp import connect
from ads_mcp.token_vault import public_base_url

REQUIRED_SCOPE = "use:mcp"


def is_auth0_mode() -> bool:
    """Returns True when the server is configured for Auth0 sign-in."""
    return (
        os.environ.get("GOOGLE_ADS_MCP_AUTH_PROVIDER", "").strip().lower()
        == "auth0"
    )


class Auth0TokenVaultProvider(RemoteAuthProvider):
    """Verifies Auth0 tokens and serves the sign-in and connect routes."""

    def __init__(self, domain: str, base_url: str, audience: str):
        self._issuer = f"https://{domain}/"
        self._audience = audience
        super().__init__(
            token_verifier=JWTVerifier(
                jwks_uri=f"{self._issuer}.well-known/jwks.json",
                issuer=self._issuer,
                audience=audience,
                algorithm="RS256",
                required_scopes=[REQUIRED_SCOPE],
            ),
            authorization_servers=[AnyHttpUrl(base_url)],
            base_url=base_url,
            scopes_supported=[REQUIRED_SCOPE],
        )

    def _get_resource_url(self, path: str | None = None) -> AnyHttpUrl:
        # The advertised resource is exactly the Auth0 API identifier, so the
        # token MCP clients request carries the audience checked above.
        return AnyHttpUrl(self._audience)

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        return [
            *super().get_routes(mcp_path),
            Route(
                "/.well-known/oauth-authorization-server",
                endpoint=self._authorization_server_metadata,
                methods=["GET"],
            ),
            *connect.routes,
        ]

    async def _authorization_server_metadata(
        self, request: Request
    ) -> JSONResponse:
        """OAuth metadata (RFC 8414): sign-in here, tokens and clients at Auth0."""
        base = str(self.base_url).rstrip("/")
        return JSONResponse(
            {
                "issuer": f"{base}/",
                "authorization_endpoint": f"{base}/authorize",
                "token_endpoint": f"{self._issuer}oauth/token",
                "registration_endpoint": f"{self._issuer}oidc/register",
                "response_types_supported": ["code"],
                "grant_types_supported": [
                    "authorization_code",
                    "refresh_token",
                ],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": [
                    "client_secret_basic",
                    "client_secret_post",
                    "none",
                ],
                "scopes_supported": [REQUIRED_SCOPE],
            }
        )


def create_auth0_provider() -> Auth0TokenVaultProvider:
    """Builds the provider from environment variables.

    Raises:
      ValueError: if a required setting is missing.
    """
    domain = os.environ.get("AUTH0_DOMAIN", "").strip()
    base_url = public_base_url()
    missing = [
        name
        for name, value in (
            ("AUTH0_DOMAIN", domain),
            ("GOOGLE_ADS_MCP_BASE_URL", base_url),
            (
                "AUTH0_CONNECT_CLIENT_ID",
                os.environ.get("AUTH0_CONNECT_CLIENT_ID"),
            ),
            (
                "AUTH0_CONNECT_CLIENT_SECRET",
                os.environ.get("AUTH0_CONNECT_CLIENT_SECRET"),
            ),
            (
                "AUTH0_TOKEN_VAULT_CLIENT_ID",
                os.environ.get("AUTH0_TOKEN_VAULT_CLIENT_ID"),
            ),
            (
                "AUTH0_TOKEN_VAULT_CLIENT_SECRET",
                os.environ.get("AUTH0_TOKEN_VAULT_CLIENT_SECRET"),
            ),
        )
        if not (value or "").strip()
    ]
    if missing:
        raise ValueError(
            "GOOGLE_ADS_MCP_AUTH_PROVIDER=auth0 needs: " + ", ".join(missing)
        )
    audience = os.environ.get("MCP_AUDIENCE", "").strip() or f"{base_url}/mcp/"
    return Auth0TokenVaultProvider(
        domain=domain, base_url=base_url, audience=audience
    )
