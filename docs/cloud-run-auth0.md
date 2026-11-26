# Cloud Run with Auth0 sign-in and per-user Google tokens

This fork can run the server on Cloud Run so that each person signs in with
their own Google account and every Google Ads API call runs with **that
person's** Google token. Each user sees only the Google Ads accounts their
Google login can already access. There is no shared Google identity and no
fallback to Application Default Credentials.

Enable it with `GOOGLE_ADS_MCP_AUTH_PROVIDER=auth0`. Local (stdio) mode and the
built-in Google OAuth mode are unchanged.

## How it works

1. **Sign-in starts at this server.** The protected-resource metadata names
   this server as the authorization server, and
   `/.well-known/oauth-authorization-server` points MCP clients to its
   `/authorize`. Auth0 still issues every token and registers clients.
2. **One-time Google connect step.** `/authorize` runs the Auth0 *Connected
   Accounts* flow (`/connect/callback`, `/connect/complete`) for the
   `https://www.googleapis.com/auth/adwords` scope, so Auth0 Token Vault stores
   the user's Google refresh token, then resumes the client's sign-in at
   Auth0. A plain Google login does not fill Token Vault. `/connect` runs the
   same step by hand.
3. **Every `/mcp` request** needs an Auth0 JWT for this server's API
   (signature, issuer, audience) with the `use:mcp` scope.
4. **Every tool call** exchanges that token for the user's Google access token
   through Token Vault (cached per user until shortly before it expires) and
   calls Google Ads as that user.

Code: `ads_mcp/auth0.py` (token check and discovery), `ads_mcp/connect.py`
(connect step), `ads_mcp/token_vault.py` (exchange),
`ads_mcp/utils.py` `_create_credentials` (per-call credentials).

## Google requirements

- **API access level belongs to a Google Cloud project.** Since September 9,
  2026, developer tokens are ignored; the access level (Test, Explorer, Basic,
  Standard) comes from the project that **owns the Google OAuth client** used
  by the Auth0 Google connection. Enable the Google Ads API there and apply for
  Explorer or Basic on its Google Ads API Overview page. See
  [Access levels](https://developers.google.com/google-ads/api/docs/api-policy/access-levels).
- **Passkey.** Google requires every user to authenticate with a passkey when
  authorizing Google Ads access. A new passkey can take up to 7 days to become
  usable. See
  [Security requirements](https://developers.google.com/google-ads/api/docs/oauth/security-requirements).
- **Consent screen.** `adwords` is a sensitive scope: in Testing mode only
  listed test users can connect (at most 100). Google verification is needed
  beyond that.

## Setup

### Google Cloud (project that owns the OAuth client)

- Consent screen (External), with the `adwords` scope under Data Access and
  the pilot users as test users.
- A Web OAuth client with the redirect URI
  `https://<AUTH0_DOMAIN>/login/callback`; its ID and secret go only into the
  Auth0 Google connection.
- Google Ads API enabled; Explorer or higher access level.

### Auth0

- **Google connection** (`google-oauth2`): your own Google client; purpose
  "Authentication and Connected Accounts for Token Vault"; Offline Access on.
  Activate the **My Account API**.
- **API**: identifier `https://<SERVICE_URL>/mcp/` (trailing slash); permission
  `use:mcp`; RBAC on; add permissions in the access token; allow skipping user
  consent; offline access off.
- **Role** with `use:mcp` on that API, assigned to each user.
- **Regular Web Application**: only the Google connection; allowed callback
  URLs `https://claude.ai/api/mcp/auth_callback`,
  `https://<SERVICE_URL>/connect/callback`,
  `https://<SERVICE_URL>/connect/complete`; user-delegated access to the API
  (`use:mcp`) and to the My Account API (`create:me:connected_accounts`,
  `read:me:connected_accounts`). MCP clients use this client ID and secret.
- **Custom API Client** created from the API's *Add Application* button: only
  the Google connection; grant type Token Vault only.

### Environment variables

| Variable | Secret | Value |
| --- | --- | --- |
| `GOOGLE_ADS_MCP_AUTH_PROVIDER` | No | `auth0` |
| `GOOGLE_ADS_MCP_BASE_URL` | No | Public URL of the service, e.g. `https://<service>-<number>.<region>.run.app` |
| `AUTH0_DOMAIN` | No | Auth0 tenant domain |
| `AUTH0_CONNECT_CLIENT_ID` | No | Regular Web Application client ID |
| `AUTH0_CONNECT_CLIENT_SECRET` | Yes | Its client secret |
| `AUTH0_TOKEN_VAULT_CLIENT_ID` | No | Custom API Client ID |
| `AUTH0_TOKEN_VAULT_CLIENT_SECRET` | Yes | Its client secret |
| `AUTH0_GOOGLE_CONNECTION` | No | Optional; default `google-oauth2` |
| `MCP_AUDIENCE` | No | Optional; default `<GOOGLE_ADS_MCP_BASE_URL>/mcp/` |

The server refuses to start in Auth0 mode when a required value is missing.

### Deploy scripts

`scripts/gcp/` holds idempotent setup, build, deploy and rollback scripts.
Copy `scripts/gcp/env.local.example.sh` to `scripts/gcp/env.local.sh`
(git-ignored), fill it in, then:

```sh
./scripts/gcp/01_bootstrap.sh   # APIs, Artifact Registry, service account, Firestore
./scripts/gcp/02_secrets.sh     # Secret Manager secrets + accessor for the runtime account
./scripts/gcp/03_build.sh       # Cloud Build, tagged with the git commit
./scripts/gcp/04_deploy.sh <image>@<digest>
./scripts/gcp/05_rollback.sh [<revision>]
```

Builds run as a dedicated `<service>-build` service account with
`roles/run.builder` and access to the regional Cloud Build logs bucket. The
service is public at the network level (`--no-invoker-iam-check`); the app
rejects `/mcp` requests without a valid Auth0 token.

### Connect in Claude

Add a custom connector with the URL `https://<SERVICE_URL>/mcp` and, under the
advanced settings, the Regular Web Application's client ID and secret.
