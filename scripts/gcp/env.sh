#!/usr/bin/env bash
# Shared settings for the Cloud Run deployment scripts.
# Source this file; do not execute it. Every value can be overridden from the
# environment, e.g. `REGION=europe-west1 ./scripts/gcp/01_bootstrap.sh`.

PROJECT_ID="${PROJECT_ID:-mcp-servers-513807}"
REGION="${REGION:-europe-west3}"
REPO="${REPO:-mcp-servers}"
SERVICE="${SERVICE:-google-ads-mcp}"
SA_NAME="${SA_NAME:-google-ads-mcp-run}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
# Build-only account: roles/run.builder + the regional Cloud Build bucket.
BUILD_SA_EMAIL="${BUILD_SA_EMAIL:-${SERVICE}-build@${PROJECT_ID}.iam.gserviceaccount.com}"
# The project hosts several MCP servers, so this one gets its own named
# Firestore database and prefixed secret names.
FIRESTORE_DB="${FIRESTORE_DB:-google-ads-mcp}"

# Secret Manager secret names used by the Cloud Run service.
SECRET_AUTH0_CLIENT_SECRET="${SERVICE}-auth0-client-secret"
SECRET_DEVELOPER_TOKEN="${SERVICE}-developer-token"
SECRET_JWT_SIGNING_KEY="${SERVICE}-jwt-signing-key"
SECRET_STORAGE_ENCRYPTION_KEY="${SERVICE}-storage-encryption-key"
SECRET_AUTH0_TOKEN_VAULT_CLIENT_SECRET="${SERVICE}-auth0-token-vault-client-secret"
SECRETS=(
  "${SECRET_AUTH0_CLIENT_SECRET}"
  "${SECRET_AUTH0_TOKEN_VAULT_CLIENT_SECRET}"
  "${SECRET_DEVELOPER_TOKEN}"
  "${SECRET_JWT_SIGNING_KEY}"
  "${SECRET_STORAGE_ENCRYPTION_KEY}"
)

# Cloud Run URL is deterministic: <service>-<project number>.<region>.run.app.
PROJECT_NUMBER="${PROJECT_NUMBER:-873842938799}"
SERVICE_URL="${SERVICE_URL:-https://${SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE}"

# Auth0 (shared tenant; see docs/cloud-run-auth0-plan.md). Client IDs are not
# secret; the client secrets live in Secret Manager.
AUTH0_DOMAIN="${AUTH0_DOMAIN:-dev-qcfguwv7uyogdyng.us.auth0.com}"
AUTH0_CONNECT_CLIENT_ID="${AUTH0_CONNECT_CLIENT_ID:-Mm9Kpj7Cdu0OuHHV2wYow47O4SdlVTUh}"
AUTH0_TOKEN_VAULT_CLIENT_ID="${AUTH0_TOKEN_VAULT_CLIENT_ID:-QQjCfGiYCiLsB44iHI34hHXNVmqQPZ8c}"

# All gcloud calls pass --project explicitly so the caller's default
# gcloud configuration is never changed.
GCLOUD=(gcloud --project="${PROJECT_ID}" --quiet)
