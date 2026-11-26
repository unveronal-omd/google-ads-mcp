#!/usr/bin/env bash
# Phase 5: build the container image with Cloud Build and push it to Artifact
# Registry, tagged with the current git commit.
#
# Builds what is committed: refuses to run with uncommitted changes, so the
# tag always matches the code in the image. Set ALLOW_DIRTY=1 to override
# (the tag then gets a "-dirty" suffix).
# Prints the image digest to deploy with 04_deploy.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"
cd "${SCRIPT_DIR}/../.."

TAG="$(git rev-parse --short HEAD)"
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  if [[ -z "${ALLOW_DIRTY:-}" ]]; then
    echo "Uncommitted changes; commit first or set ALLOW_DIRTY=1." >&2
    exit 1
  fi
  TAG="${TAG}-dirty"
fi

echo "Project: ${PROJECT_ID}  Image: ${IMAGE}:${TAG}  Account: $(gcloud config get-value account 2>/dev/null)"
# Builds run as a dedicated account (roles/run.builder plus access to the
# regional Cloud Build bucket only), like the DV360 server in this project.
"${GCLOUD[@]}" builds submit --region="${REGION}" --tag="${IMAGE}:${TAG}" \
  --service-account="projects/${PROJECT_ID}/serviceAccounts/${BUILD_SA_EMAIL}" \
  --default-buckets-behavior=REGIONAL_USER_OWNED_BUCKET .

DIGEST="$("${GCLOUD[@]}" artifacts docker images describe "${IMAGE}:${TAG}" \
  --format="value(image_summary.digest)")"
echo "Built ${IMAGE}@${DIGEST}"
echo "Next: ./scripts/gcp/04_deploy.sh ${IMAGE}@${DIGEST}"
