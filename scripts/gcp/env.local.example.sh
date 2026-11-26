# Deployment-specific settings for scripts/gcp/*.sh.
# Copy to env.local.sh (git-ignored) and fill in. These are identifiers, not
# secrets; client secrets and the developer token live in Secret Manager.

PROJECT_ID="your-gcp-project-id"
PROJECT_NUMBER="123456789012"   # gcloud projects describe <id> --format="value(projectNumber)"

# Auth0 tenant and the two clients from docs/cloud-run-auth0.md.
AUTH0_DOMAIN="your-tenant.us.auth0.com"
AUTH0_CONNECT_CLIENT_ID="regular-web-application-client-id"
AUTH0_TOKEN_VAULT_CLIENT_ID="custom-api-client-client-id"
