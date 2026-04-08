#!/usr/bin/env bash
# Called from CI with env vars set. Exits 1 if any policy fails.
# Uses GNU date (-d). On macOS, run it on Linux CI or use GNU coreutils (gdate).
# Policy rules for client cert request parameters; align with any parallel validators under your change process.
set -euo pipefail

: "${WORKLOAD_ENV:?}"
: "${WORKLOAD_APP:?}"
: "${COMMON_NAME:?}"
: "${ORGANIZATIONAL_UNIT:?}"
: "${VALIDITY_DAYS:?}"
: "${MIN_VALIDITY_DAYS:?}"
: "${MAX_VALIDITY_DAYS:?}"
: "${MAX_VALIDITY_DAYS_PROD:?}"
: "${MAINT_WINDOW_START_MONTH:?}"
: "${MAINT_WINDOW_END_MONTH:?}"
: "${MAINT_WINDOW_END_DAY:?}"
: "${ALLOWED_APPS:?}"

ERRORS=()
ok() { echo "[ok] $1"; }

fail() {
  echo "[fail] $1"
  ERRORS+=("$1")
}

IFS=',' read -r -a APP_LIST <<< "${ALLOWED_APPS}"
ALLOWED=false
for a in "${APP_LIST[@]}"; do
  [[ "${a}" == "${WORKLOAD_APP}" ]] && ALLOWED=true && break
done
if [[ "${ALLOWED}" != true ]]; then
  fail "workload_app '${WORKLOAD_APP}' not in allow-list (${ALLOWED_APPS})"
else
  ok "workload allow-list"
fi

EXPECTED_OU="${WORKLOAD_ENV}-${WORKLOAD_APP}"
if [[ "${ORGANIZATIONAL_UNIT}" != "${EXPECTED_OU}" ]]; then
  fail "OU must be '${EXPECTED_OU}' (got '${ORGANIZATIONAL_UNIT}')"
else
  ok "OU format env-app"
fi

if [[ "${COMMON_NAME}" != *"${WORKLOAD_ENV}"* ]]; then
  fail "Common Name must contain environment token '${WORKLOAD_ENV}'"
else
  ok "CN contains environment"
fi

if (( ${#COMMON_NAME} > 64 )); then
  fail "Common Name length must be <= 64 (RFC 5280 typical limit for ubiquity)"
else
  ok "CN length"
fi

if (( ${#ORGANIZATIONAL_UNIT} > 64 )); then
  fail "OU length must be <= 64"
else
  ok "OU length"
fi

if [[ ! "${COMMON_NAME}" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$ ]] && [[ ! "${COMMON_NAME}" =~ ^[A-Za-z0-9]$ ]]; then
  fail "Common Name must be alphanumeric/label style (hyphen/dot allowed internally)"
else
  ok "CN character set"
fi

if (( VALIDITY_DAYS < MIN_VALIDITY_DAYS )); then
  fail "Validity ${VALIDITY_DAYS}d below minimum ${MIN_VALIDITY_DAYS}d"
else
  ok "minimum lifetime"
fi

STRICT_VALIDITY_ENVS="${STRICT_VALIDITY_ENVS:-prod}"
USE_STRICT_CAP=false
IFS=',' read -r -a _STRICT_ARR <<< "${STRICT_VALIDITY_ENVS}"
for e in "${_STRICT_ARR[@]}"; do
  e="$(echo "${e}" | tr -d '[:space:]')"
  [[ -z "${e}" ]] && continue
  if [[ "${WORKLOAD_ENV}" == "${e}" ]]; then
    USE_STRICT_CAP=true
    break
  fi
done

MAX_FOR_ENV="${MAX_VALIDITY_DAYS}"
if [[ "${USE_STRICT_CAP}" == true ]]; then
  MAX_FOR_ENV="${MAX_VALIDITY_DAYS_PROD}"
fi

if (( VALIDITY_DAYS > MAX_FOR_ENV )); then
  fail "Validity ${VALIDITY_DAYS}d above cap for ${WORKLOAD_ENV} (${MAX_FOR_ENV}d)"
else
  ok "maximum lifetime for env"
fi

EXPIRY_DATE=$(date -u -d "+${VALIDITY_DAYS} days" +%F)
EXPIRY_MONTH=$(date -u -d "+${VALIDITY_DAYS} days" +%-m)
EXPIRY_DAY=$(date -u -d "+${VALIDITY_DAYS} days" +%-d)

IN_WINDOW=false
if (( EXPIRY_MONTH >= MAINT_WINDOW_START_MONTH )); then
  IN_WINDOW=true
fi
if (( EXPIRY_MONTH == MAINT_WINDOW_END_MONTH )) && (( EXPIRY_DAY <= MAINT_WINDOW_END_DAY )); then
  IN_WINDOW=true
fi

if [[ "${IN_WINDOW}" == true ]]; then
  fail "Certificate not-after ${EXPIRY_DATE} falls inside configured maintenance window (adjust template vars or validity)"
else
  ok "not-after outside maintenance window"
fi

if (( ${#ERRORS[@]} > 0 )); then
  echo "--- validation failed ---"
  printf ' - %s\n' "${ERRORS[@]}"
  exit 1
fi

echo "--- validation passed (not-after ${EXPIRY_DATE}) ---"
