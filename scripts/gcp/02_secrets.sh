#!/usr/bin/env bash
# Phase 1: create Secret Manager secrets and grant the runtime service account
# access to each one. Idempotent: existing secrets and versions are kept.
#
# Values:
#   - mcp-jwt-signing-key, mcp-storage-encryption-key: generated randomly
#     when the secret has no version yet.
#   - auth0-client-secret, google-ads-developer-token: read from a hidden
#     prompt when the secret has no version yet. Press Enter to skip and
#     add later. Set NO_PROMPT=1 to skip both without prompting.
#
# Rotate one secret (adds a new version):
#   ./scripts/gcp/02_secrets.sh --rotate <secret-name>
# Values never appear in arguments, logs or files; they go to gcloud via stdin.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"

ROTATE=""
if [[ "${1:-}" == "--rotate" ]]; then
  ROTATE="${2:?usage: $0 --rotate <secret-name>}"
fi

has_version() {
  [[ -n "$("${GCLOUD[@]}" secrets versions list "$1" \
    --filter="state=ENABLED" --limit=1 --format="value(name)" 2>/dev/null)" ]]
}

generate_value() {
  # 48 random bytes, base64: high-entropy material for JWT signing / HKDF.
  openssl rand -base64 48 | tr -d '\n'
}

prompt_value() {
  local name="$1" value
  # NO_PROMPT=1 skips manual secrets (e.g. non-interactive runs); add them
  # later with --rotate.
  if [[ -n "${NO_PROMPT:-}" ]]; then
    return 0
  fi
  read -r -s -p "    Value for ${name} (Enter to skip): " value </dev/tty
  echo >&2
  printf '%s' "${value}"
}

add_version() {
  local name="$1" value="$2"
  printf '%s' "${value}" |
    "${GCLOUD[@]}" secrets versions add "${name}" --data-file=- >/dev/null
  echo "    new version added"
}

for name in "${SECRETS[@]}"; do
  if [[ -n "${ROTATE}" && "${name}" != "${ROTATE}" ]]; then
    continue
  fi
  echo "==> ${name}"

  if ! "${GCLOUD[@]}" secrets describe "${name}" >/dev/null 2>&1; then
    "${GCLOUD[@]}" secrets create "${name}" \
      --replication-policy=user-managed \
      --locations="${REGION}" >/dev/null
    echo "    created"
  fi

  if [[ -z "${ROTATE}" ]] && has_version "${name}"; then
    echo "    has a value; kept"
  else
    case "${name}" in
      "${SECRET_JWT_SIGNING_KEY}" | "${SECRET_STORAGE_ENCRYPTION_KEY}")
        add_version "${name}" "$(generate_value)"
        ;;
      *)
        value="$(prompt_value "${name}")"
        if [[ -n "${value}" ]]; then
          add_version "${name}" "${value}"
        else
          echo "    skipped; run with --rotate ${name} to add it later"
        fi
        unset value
        ;;
    esac
  fi

  "${GCLOUD[@]}" secrets add-iam-policy-binding "${name}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role=roles/secretmanager.secretAccessor >/dev/null
  echo "    accessor granted to ${SA_NAME}"
done

if [[ -n "${ROTATE}" ]]; then
  echo "Rotated ${ROTATE}. Redeploy the service to pick up the new version if it pins one."
else
  echo "Done."
fi
