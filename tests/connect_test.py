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

"""Test cases for the connect (Connected Accounts) flow."""

import base64
import json
import os
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

import httpx
from starlette.applications import Starlette
from starlette.testclient import TestClient

from ads_mcp import connect
from ads_mcp.token_vault import ADWORDS_SCOPE

_REAL_ASYNC_CLIENT = httpx.AsyncClient

_ENV = {
    "AUTH0_DOMAIN": "tenant.example.com",
    "AUTH0_CONNECT_CLIENT_ID": "web-client",
    "AUTH0_CONNECT_CLIENT_SECRET": "web-secret",
    "GOOGLE_ADS_MCP_BASE_URL": "https://mcp.example.com",
}


class FakeAuth0:
    """Answers the Auth0 calls made by the connect flow."""

    def __init__(self, complete_scopes=None, email="user@example.com"):
        self.calls = []
        self.email = email
        self.complete_scopes = complete_scopes or [
            "openid",
            ADWORDS_SCOPE,
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path == "/oauth/token":
            body = {"access_token": "my-account"}
            if self.email:
                claims = (
                    base64.urlsafe_b64encode(
                        json.dumps({"email": self.email}).encode()
                    )
                    .decode()
                    .rstrip("=")
                )
                body["id_token"] = f"h.{claims}.sig"
            return httpx.Response(200, json=body)
        if path == "/me/v1/connected-accounts/connect":
            return httpx.Response(
                201,
                json={
                    "auth_session": "sess-1",
                    "connect_uri": "https://tenant.example.com/connect",
                    "connect_params": {"ticket": "tick-1"},
                    "expires_in": 300,
                },
            )
        if path == "/me/v1/connected-accounts/complete":
            return httpx.Response(
                201, json={"id": "cac_1", "scopes": self.complete_scopes}
            )
        return httpx.Response(404)

    def client_factory(self):
        transport = httpx.MockTransport(self.handler)
        return lambda **kwargs: _REAL_ASYNC_CLIENT(
            transport=transport, **kwargs
        )


@mock.patch.dict(os.environ, _ENV)
class TestConnectFlow(unittest.TestCase):

    def setUp(self):
        self.auth0 = FakeAuth0()
        patcher = mock.patch.object(
            connect.httpx, "AsyncClient", self.auth0.client_factory()
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(
            Starlette(routes=connect.routes),
            base_url="https://mcp.example.com",
            follow_redirects=False,
        )

    def _start(self):
        response = self.client.get("/connect")
        self.assertEqual(response.status_code, 307)
        return parse_qs(urlparse(response.headers["location"]).query)

    def test_start_redirects_to_auth0_login(self):
        query = self._start()
        self.assertEqual(query["client_id"], ["web-client"])
        self.assertEqual(query["audience"], ["https://tenant.example.com/me/"])
        self.assertEqual(query["connection"], ["google-oauth2"])
        self.assertEqual(
            query["redirect_uri"], ["https://mcp.example.com/connect/callback"]
        )
        self.assertIn("create:me:connected_accounts", query["scope"][0])

    def test_full_flow(self):
        state = self._start()["state"][0]
        response = self.client.get(
            "/connect/callback", params={"code": "c1", "state": state}
        )
        self.assertEqual(response.status_code, 307)
        self.assertEqual(
            response.headers["location"],
            "https://tenant.example.com/connect?ticket=tick-1",
        )
        connect_body = json.loads(self.auth0.calls[1].content)
        self.assertEqual(
            connect_body["scopes"],
            ["openid", "profile", ADWORDS_SCOPE],
        )
        self.assertEqual(
            connect_body["authorization_params"],
            {"login_hint": "user@example.com"},
        )
        self.assertEqual(
            self.auth0.calls[1].headers["authorization"], "Bearer my-account"
        )

        response = self.client.get(
            "/connect/complete",
            params={"connect_code": "cc1", "state": connect_body["state"]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("connected", response.text)
        complete_body = json.loads(self.auth0.calls[2].content)
        self.assertEqual(complete_body["auth_session"], "sess-1")
        self.assertEqual(complete_body["connect_code"], "cc1")
        self.assertEqual(
            complete_body["redirect_uri"],
            "https://mcp.example.com/connect/complete",
        )

    def test_no_login_hint_without_email(self):
        self.auth0.email = None
        state = self._start()["state"][0]
        self.client.get(
            "/connect/callback", params={"code": "c1", "state": state}
        )
        connect_body = json.loads(self.auth0.calls[1].content)
        self.assertNotIn("authorization_params", connect_body)

    def test_callback_rejects_wrong_state(self):
        self._start()
        response = self.client.get(
            "/connect/callback", params={"code": "c1", "state": "forged"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.auth0.calls, [])

    def test_callback_rejects_tampered_cookie(self):
        state = self._start()["state"][0]
        self.client.cookies.set(
            "ads_connect", "e30.bad", domain="mcp.example.com", path="/connect"
        )
        response = self.client.get(
            "/connect/callback", params={"code": "c1", "state": state}
        )
        self.assertEqual(response.status_code, 400)

    def test_complete_without_code_serves_fragment_page(self):
        response = self.client.get("/connect/complete")
        self.assertEqual(response.status_code, 200)
        self.assertIn("location.hash", response.text)

    def test_complete_reports_missing_adwords_scope(self):
        self.auth0.complete_scopes = ["openid", "profile"]
        state = self._start()["state"][0]
        self.client.get(
            "/connect/callback", params={"code": "c1", "state": state}
        )
        connect_state = json.loads(self.auth0.calls[1].content)["state"]
        response = self.client.get(
            "/connect/complete",
            params={"connect_code": "cc1", "state": connect_state},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Google Ads access missing", response.text)

    def test_authorize_connects_then_resumes_sign_in(self):
        claude_request = {
            "response_type": "code",
            "client_id": "web-client",
            "redirect_uri": "https://claude.example/callback",
            "state": "claude-state",
            "code_challenge": "abc",
            "code_challenge_method": "S256",
        }
        response = self.client.get("/authorize", params=claude_request)
        self.assertEqual(response.status_code, 307)
        login = parse_qs(urlparse(response.headers["location"]).query)
        # First the connect flow's own login, not the client's request.
        self.assertEqual(
            login["redirect_uri"], ["https://mcp.example.com/connect/callback"]
        )

        self.client.get(
            "/connect/callback",
            params={"code": "c1", "state": login["state"][0]},
        )
        connect_state = json.loads(self.auth0.calls[1].content)["state"]
        response = self.client.get(
            "/connect/complete",
            params={"connect_code": "cc1", "state": connect_state},
        )
        self.assertEqual(response.status_code, 307)
        location = urlparse(response.headers["location"])
        self.assertEqual(
            f"{location.scheme}://{location.netloc}{location.path}",
            "https://tenant.example.com/authorize",
        )
        resumed = {k: v[0] for k, v in parse_qs(location.query).items()}
        self.assertEqual(
            resumed, {**claude_request, "connection": "google-oauth2"}
        )

    def test_authorize_requires_client_and_redirect(self):
        response = self.client.get("/authorize", params={"client_id": "x"})
        self.assertEqual(response.status_code, 400)

    def test_manual_connect_does_not_redirect_at_end(self):
        state = self._start()["state"][0]
        self.client.get(
            "/connect/callback", params={"code": "c1", "state": state}
        )
        connect_state = json.loads(self.auth0.calls[1].content)["state"]
        response = self.client.get(
            "/connect/complete",
            params={"connect_code": "cc1", "state": connect_state},
        )
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
