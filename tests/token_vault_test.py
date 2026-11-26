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

"""Tests for the Auth0 Token Vault exchange."""

import json
import os
import unittest
from unittest import mock

import httpx

from ads_mcp import token_vault

_ENV = {
    "AUTH0_DOMAIN": "tenant.example.com",
    "AUTH0_TOKEN_VAULT_CLIENT_ID": "vault-client",
    "AUTH0_TOKEN_VAULT_CLIENT_SECRET": "vault-secret",
    "GOOGLE_ADS_MCP_BASE_URL": "https://ads.example.com",
}


def _transport(status=200, body=None, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return httpx.Response(status, json=body or {})

    return httpx.MockTransport(handler)


def _ok_body(**overrides):
    body = {
        "access_token": "google-token",
        "expires_in": 3600,
        "scope": f"openid {token_vault.ADWORDS_SCOPE}",
    }
    body.update(overrides)
    return body


@mock.patch.dict(os.environ, _ENV)
class TestTokenVault(unittest.TestCase):

    def setUp(self):
        token_vault._cache.clear()

    def test_exchange_request_and_result(self):
        calls = []
        token = token_vault.get_google_access_token(
            "google-oauth2|1",
            "auth0-token",
            transport=_transport(body=_ok_body(), calls=calls),
        )
        self.assertEqual(token, "google-token")
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            str(calls[0].url), "https://tenant.example.com/oauth/token"
        )
        sent = json.loads(calls[0].content)
        self.assertEqual(sent["subject_token"], "auth0-token")
        self.assertEqual(sent["client_id"], "vault-client")
        self.assertEqual(sent["connection"], "google-oauth2")
        self.assertEqual(
            sent["grant_type"],
            "urn:auth0:params:oauth:grant-type:token-exchange:"
            "federated-connection-access-token",
        )

    def test_token_is_cached_per_user(self):
        calls = []
        transport = _transport(body=_ok_body(), calls=calls)
        for _ in range(2):
            token_vault.get_google_access_token(
                "user-a", "t", transport=transport
            )
        self.assertEqual(len(calls), 1)
        token_vault.get_google_access_token("user-b", "t", transport=transport)
        self.assertEqual(len(calls), 2)

    def test_short_lived_token_is_not_cached(self):
        calls = []
        transport = _transport(body=_ok_body(expires_in=60), calls=calls)
        for _ in range(2):
            token_vault.get_google_access_token(
                "user-a", "t", transport=transport
            )
        self.assertEqual(len(calls), 2)

    def test_not_connected_raises_with_connect_link(self):
        transport = _transport(
            status=401,
            body={"error": "federated_connection_refresh_token_not_found"},
        )
        with self.assertRaisesRegex(
            token_vault.GoogleTokenError, "https://ads.example.com/connect"
        ):
            token_vault.get_google_access_token(
                "user-a", "t", transport=transport
            )
        self.assertEqual(token_vault._cache, {})

    def test_server_error_raises(self):
        with self.assertRaisesRegex(
            token_vault.GoogleTokenError, "returned an error"
        ):
            token_vault.get_google_access_token(
                "user-a", "t", transport=_transport(status=500)
            )

    def test_missing_adwords_scope_raises(self):
        transport = _transport(body=_ok_body(scope="openid email"))
        with self.assertRaisesRegex(
            token_vault.GoogleTokenError, "Google Ads access"
        ):
            token_vault.get_google_access_token(
                "user-a", "t", transport=transport
            )

    def test_missing_config_raises(self):
        with mock.patch.dict(
            os.environ, {"AUTH0_TOKEN_VAULT_CLIENT_SECRET": ""}
        ):
            with self.assertRaisesRegex(
                token_vault.GoogleTokenError, "not configured"
            ):
                token_vault.get_google_access_token(
                    "user-a", "t", transport=_transport(body=_ok_body())
                )


if __name__ == "__main__":
    unittest.main()
