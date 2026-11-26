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

"""Tests for Auth0 sign-in mode (discovery routes and per-user credentials)."""

import os
import unittest
from unittest import mock

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken
from starlette.testclient import TestClient

from ads_mcp import auth0, token_vault, utils

_ENV = {
    "GOOGLE_ADS_MCP_AUTH_PROVIDER": "auth0",
    "AUTH0_DOMAIN": "tenant.example.com",
    "GOOGLE_ADS_MCP_BASE_URL": "https://ads.example.com",
    "AUTH0_CONNECT_CLIENT_ID": "web-client",
    "AUTH0_CONNECT_CLIENT_SECRET": "web-secret",
    "AUTH0_TOKEN_VAULT_CLIENT_ID": "vault-client",
    "AUTH0_TOKEN_VAULT_CLIENT_SECRET": "vault-secret",
}


class TestAuth0Provider(unittest.TestCase):

    def setUp(self):
        env = mock.patch.dict(os.environ, _ENV)
        env.start()
        self.addCleanup(env.stop)
        app = FastMCP("test", auth=auth0.create_auth0_provider()).http_app()
        self.client = TestClient(app, base_url="https://ads.example.com")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_protected_resource_names_this_server(self):
        response = self.client.get("/.well-known/oauth-protected-resource/mcp/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["resource"], "https://ads.example.com/mcp/")
        self.assertEqual(
            body["authorization_servers"], ["https://ads.example.com/"]
        )
        self.assertEqual(body["scopes_supported"], ["use:mcp"])

    def test_authorization_server_metadata(self):
        body = self.client.get("/.well-known/oauth-authorization-server").json()
        self.assertEqual(body["issuer"], "https://ads.example.com/")
        self.assertEqual(
            body["authorization_endpoint"], "https://ads.example.com/authorize"
        )
        self.assertEqual(
            body["token_endpoint"], "https://tenant.example.com/oauth/token"
        )
        self.assertEqual(
            body["registration_endpoint"],
            "https://tenant.example.com/oidc/register",
        )

    def test_mcp_requires_token(self):
        response = self.client.post("/mcp", json={})
        self.assertEqual(response.status_code, 401)
        self.assertIn(
            "https://ads.example.com/.well-known/oauth-protected-resource/mcp/",
            response.headers["www-authenticate"],
        )

    def test_connect_routes_are_public(self):
        response = self.client.get("/connect", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertTrue(
            response.headers["location"].startswith(
                "https://tenant.example.com/authorize?"
            )
        )

    def test_verifier_checks_issuer_audience_and_scope(self):
        verifier = auth0.create_auth0_provider().token_verifier
        self.assertEqual(verifier.issuer, "https://tenant.example.com/")
        self.assertEqual(verifier.audience, "https://ads.example.com/mcp/")
        self.assertEqual(verifier.required_scopes, ["use:mcp"])
        self.assertEqual(
            verifier.jwks_uri,
            "https://tenant.example.com/.well-known/jwks.json",
        )


class TestAuth0Config(unittest.TestCase):

    def test_missing_settings_are_named(self):
        env = {
            **_ENV,
            "AUTH0_TOKEN_VAULT_CLIENT_SECRET": "",
            "AUTH0_DOMAIN": "",
        }
        with mock.patch.dict(os.environ, env):
            with self.assertRaisesRegex(
                ValueError, "AUTH0_DOMAIN.*AUTH0_TOKEN_VAULT_CLIENT_SECRET"
            ):
                auth0.create_auth0_provider()

    def test_audience_override(self):
        with mock.patch.dict(
            os.environ, {**_ENV, "MCP_AUDIENCE": "https://api.example.com/"}
        ):
            provider = auth0.create_auth0_provider()
        self.assertEqual(
            provider.token_verifier.audience, "https://api.example.com/"
        )

    def test_is_auth0_mode(self):
        with mock.patch.dict(os.environ, {"GOOGLE_ADS_MCP_AUTH_PROVIDER": ""}):
            self.assertFalse(auth0.is_auth0_mode())
        with mock.patch.dict(
            os.environ, {"GOOGLE_ADS_MCP_AUTH_PROVIDER": "Auth0"}
        ):
            self.assertTrue(auth0.is_auth0_mode())
            self.assertTrue(utils.is_remote_oauth_mode())


@mock.patch.dict(os.environ, _ENV)
class TestPerUserCredentials(unittest.TestCase):

    def _token(self, sub="google-oauth2|1"):
        return AccessToken(
            token="auth0-token",
            client_id="claude",
            scopes=["use:mcp"],
            subject=sub,
        )

    @mock.patch.object(
        token_vault, "get_google_access_token", return_value="google-token"
    )
    @mock.patch("fastmcp.server.dependencies.get_access_token")
    def test_uses_users_google_token(self, get_token, exchange):
        get_token.return_value = self._token()
        credentials = utils._create_credentials()
        self.assertEqual(credentials.token, "google-token")
        exchange.assert_called_once_with(
            user_id="google-oauth2|1", auth0_access_token="auth0-token"
        )

    @mock.patch("google.auth.default")
    @mock.patch("fastmcp.server.dependencies.get_access_token")
    def test_no_fallback_without_token(self, get_token, adc):
        get_token.return_value = None
        with self.assertRaisesRegex(token_vault.GoogleTokenError, "signed in"):
            utils._create_credentials()
        adc.assert_not_called()

    @mock.patch("google.auth.default")
    @mock.patch.object(
        token_vault,
        "get_google_access_token",
        side_effect=token_vault.GoogleTokenError("not connected"),
    )
    @mock.patch("fastmcp.server.dependencies.get_access_token")
    def test_no_fallback_when_exchange_fails(self, get_token, _exchange, adc):
        get_token.return_value = self._token()
        with self.assertRaisesRegex(
            token_vault.GoogleTokenError, "not connected"
        ):
            utils._create_credentials()
        adc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
