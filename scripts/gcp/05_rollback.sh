#!/usr/bin/env bash
# Send all traffic back to an earlier Cloud Run revision.
#
# Usage: ./scripts/gcp/05_rollback.sh            (lists revisions)
#        ./scripts/gcp/05_rollback.sh <revision> (routes 100% to it)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"

if [[ -z "${1:-}" ]]; then
  "${GCLOUD[@]}" run revisions list --service="${SERVICE}" --region="${REGION}"
  exit 0
fi

"${GCLOUD[@]}" run services update-traffic "${SERVICE}" \
  --region="${REGION}" --to-revisions="$1=100"
