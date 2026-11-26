#!/usr/bin/env bash
# Phase 6: deploy the image to Cloud Run in Auth0 (per-user Google token) mode.
#
# Usage: ./scripts/gcp/04_deploy.sh <image>@<digest>
#   (the line printed by 03_build.sh)
#
# The service is public at the network level (no Cloud Run invoker check);
# every /mcp request must carry a valid Auth0 token, checked by the app.
# Secrets are mounted from Secret Manager as env vars (latest version).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"

IMAGE_REF="${1:?usage: $0 <image>@<digest>}"
if [[ "${IMAGE_REF}" != *@sha256:* ]]; then
  echo "Deploy by digest (<image>@sha256:...), as printed by 03_build.sh." >&2
  exit 1
fi

ENV_VARS=(
  "GOOGLE_ADS_MCP_AUTH_PROVIDER=auth0"
  "GOOGLE_ADS_MCP_BASE_URL=${SERVICE_URL}"
  "AUTH0_DOMAIN=${AUTH0_DOMAIN}"
  "AUTH0_CONNECT_CLIENT_ID=${AUTH0_CONNECT_CLIENT_ID}"
  "AUTH0_TOKEN_VAULT_CLIENT_ID=${AUTH0_TOKEN_VAULT_CLIENT_ID}"
)
SECRET_VARS=(
  "AUTH0_CONNECT_CLIENT_SECRET=${SECRET_AUTH0_CLIENT_SECRET}:latest"
  "AUTH0_TOKEN_VAULT_CLIENT_SECRET=${SECRET_AUTH0_TOKEN_VAULT_CLIENT_SECRET}:latest"
  "GOOGLE_ADS_DEVELOPER_TOKEN=${SECRET_DEVELOPER_TOKEN}:latest"
)

join() { local IFS=","; echo "$*"; }

echo "Project: ${PROJECT_ID}  Service: ${SERVICE}  URL: ${SERVICE_URL}"
echo "Image: ${IMAGE_REF}  Account: $(gcloud config get-value account 2>/dev/null)"
"${GCLOUD[@]}" run deploy "${SERVICE}" \
  --region="${REGION}" \
  --image="${IMAGE_REF}" \
  --service-account="${SA_EMAIL}" \
  --no-invoker-iam-check \
  --ingress=all \
  --port=8080 \
  --min-instances=0 \
  --max-instances=5 \
  --set-env-vars="$(join "${ENV_VARS[@]}")" \
  --set-secrets="$(join "${SECRET_VARS[@]}")"

ACTUAL_URL="$("${GCLOUD[@]}" run services describe "${SERVICE}" \
  --region="${REGION}" --format="value(metadata.annotations.'run.googleapis.com/urls')" 2>/dev/null || true)"
echo "Deployed. Expected URL: ${SERVICE_URL}"
echo "Cloud Run URLs: ${ACTUAL_URL}"
echo "Check: curl -s ${SERVICE_URL}/.well-known/oauth-protected-resource/mcp/"
