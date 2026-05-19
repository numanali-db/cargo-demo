#!/usr/bin/env bash
# Render configuration files from .env.
#
# Inputs:
#   .env at repo root (copy from .env.example)
#
# Outputs:
#   build/sql/*.sql           - rendered SQL from sql/*.sql (gitignored)
#   cargo_demo_app/app.yaml   - ${VAR} placeholders substituted in place (TRACKED)
#
# Note: cargo_demo_app/app.yaml IS committed because Databricks Apps requires a
# real app.yaml in the source repo (no template rendering at deploy time).
# The committed file uses ${VAR} placeholders. For GitHub-source deploys, set the
# actual env values via the workspace UI (Variables tab) which override app.yaml.
# For CLI deploys, render.sh substitutes values from .env — don't commit those.
#
# Re-run after editing .env.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -f .env ]]; then
  echo "ERROR: .env not found. Copy .env.example to .env and fill in values." >&2
  exit 1
fi

# Load .env (and export everything so envsubst sees it)
set -a
# shellcheck disable=SC1091
source .env
set +a

# Required variables. APP_SP_ID and GENIE_SPACE_ID are checked separately because
# they're filled in later in the deployment flow.
REQUIRED=(CATALOG WAREHOUSE_ID VS_ENDPOINT LLM_ENDPOINT YIELD_MODEL_ENDPOINT AGENT_ENDPOINT APP_NAME)
missing=()
for v in "${REQUIRED[@]}"; do
  if [[ -z "${!v:-}" ]]; then
    missing+=("$v")
  fi
done
if (( ${#missing[@]} > 0 )); then
  printf 'ERROR: missing required values in .env: %s\n' "${missing[*]}" >&2
  exit 1
fi

# Optional variables — render with empty string if unset
: "${GENIE_SPACE_ID:=}"
: "${APP_SP_ID:=}"
export GENIE_SPACE_ID APP_SP_ID

# Variables envsubst should substitute. Listing them explicitly prevents accidental
# substitution of unrelated $VAR-looking text in SQL or YAML.
SUBST_VARS='${CATALOG} ${WAREHOUSE_ID} ${VS_ENDPOINT} ${LLM_ENDPOINT} ${YIELD_MODEL_ENDPOINT} ${AGENT_ENDPOINT} ${APP_NAME} ${GENIE_SPACE_ID} ${APP_SP_ID}'

# --- Render SQL ---
mkdir -p build/sql
for src in sql/*.sql; do
  out="build/sql/$(basename "$src")"
  envsubst "$SUBST_VARS" < "$src" > "$out"
  echo "  rendered: $out"
done

# Warn if 05_grant_app_perms.sql still has empty APP_SP_ID
if [[ -z "$APP_SP_ID" ]]; then
  echo "  note: APP_SP_ID is empty in .env — 05_grant_app_perms.sql is not yet usable."
  echo "        Deploy the app first, then set APP_SP_ID and re-run scripts/render.sh."
fi

# --- Render app.yaml (substitute placeholders in place) ---
tmp="$(mktemp)"
envsubst "$SUBST_VARS" < cargo_demo_app/app.yaml > "$tmp"
mv "$tmp" cargo_demo_app/app.yaml
echo "  rendered: cargo_demo_app/app.yaml"
echo "  note: cargo_demo_app/app.yaml is tracked. Do NOT commit your local values."
echo "        After deploying, run: git checkout cargo_demo_app/app.yaml"

echo "Done."
