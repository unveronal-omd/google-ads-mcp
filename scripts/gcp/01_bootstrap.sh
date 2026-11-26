#!/usr/bin/env bash
# Phase 1: one-time GCP setup for the Google Ads MCP server on Cloud Run.
# Idempotent: safe to re-run; existing resources are left as they are.
#
# Creates in $PROJECT_ID:
#   - required APIs
#   - Artifact Registry Docker repo $REPO in $REGION
#   - runtime service account $SA_EMAIL (infrastructure only, no Google Ads access)
#   - project roles for that account: Firestore user, log writer
#   - Firestore Native database in $REGION
# Secrets and per-secret access are handled by 02_secrets.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"

echo "Project: ${PROJECT_ID}  Region: ${REGION}  Account: $(gcloud config get-value account 2>/dev/null)"

echo "==> Enabling APIs"
"${GCLOUD[@]}" services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  firestore.googleapis.com \
  iamcredentials.googleapis.com \
  logging.googleapis.com

echo "==> Artifact Registry repo ${REPO}"
if "${GCLOUD[@]}" artifacts repositories describe "${REPO}" \
  --location="${REGION}" >/dev/null 2>&1; then
  echo "    exists"
else
  "${GCLOUD[@]}" artifacts repositories create "${REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Container images for MCP servers"
fi

echo "==> Service account ${SA_EMAIL}"
if "${GCLOUD[@]}" iam service-accounts describe "${SA_EMAIL}" >/dev/null 2>&1; then
  echo "    exists"
else
  "${GCLOUD[@]}" iam service-accounts create "${SA_NAME}" \
    --display-name="Google Ads MCP (Cloud Run runtime)"
fi

echo "==> Project roles for ${SA_EMAIL}"
for role in roles/datastore.user roles/logging.logWriter; do
  "${GCLOUD[@]}" projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${role}" \
    --condition=None >/dev/null
  echo "    ${role}"
done

echo "==> Firestore database ${FIRESTORE_DB}"
if "${GCLOUD[@]}" firestore databases describe \
  --database="${FIRESTORE_DB}" >/dev/null 2>&1; then
  echo "    exists"
else
  "${GCLOUD[@]}" firestore databases create \
    --database="${FIRESTORE_DB}" \
    --location="${REGION}" \
    --type=firestore-native
fi

echo "Done. Next: ./scripts/gcp/02_secrets.sh"
