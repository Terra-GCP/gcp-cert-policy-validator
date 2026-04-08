# Setup and use — GCP Certificate Policy Validator

This solution validates **client certificate request parameters** in CI using **`scripts/validate-cert-request.sh`**. It does **not** call Google Cloud or issue certificates.

---

## Prerequisites

| Requirement | Notes |
|-------------|--------|
| **Bash** | Script uses `set -euo pipefail`. |
| **GNU `date`** | Maintenance-window logic needs `date -d`. Use **Linux**, **WSL**, or GNU coreutils (`gdate` on macOS if wired in). Native macOS `date` often fails. |
| **No GCP account** | Optional for running this repo alone. |

---

## Setup (once per machine or repo)

1. **Clone** (or copy) this repository; note the path to `scripts/validate-cert-request.sh`.
2. **CI only:** Commit the workflow YAML under **`.github/workflows/`**, **`cicd/`**, and **`cloudbuild/`** as needed for your platform.

---

## Use — local

```bash
cd scripts
chmod +x validate-cert-request.sh

export WORKLOAD_ENV=dev WORKLOAD_APP=sample-app \
  COMMON_NAME=api-dev.example.internal ORGANIZATIONAL_UNIT=dev-sample-app \
  VALIDITY_DAYS=400 MIN_VALIDITY_DAYS=365 MAX_VALIDITY_DAYS=730 MAX_VALIDITY_DAYS_PROD=548 \
  MAINT_WINDOW_START_MONTH=11 MAINT_WINDOW_END_MONTH=1 MAINT_WINDOW_END_DAY=7 \
  ALLOWED_APPS=sample-app,sample-service STRICT_VALIDITY_ENVS=prod

./validate-cert-request.sh
```

- **Exit 0** — all rules passed. **Exit 1** — one or more failures (see `[fail]` lines).

Tune **`ALLOWED_APPS`**, lifetime caps, and **`STRICT_VALIDITY_ENVS`** to match your program. Rule details: [validation-deep-dive.md](validation-deep-dive.md).

---

## Use — GitHub Actions

1. In the repo, open **Actions** → workflow **“Certificate policy validation”** (or the name shown in `.github/workflows/cert-validate.yaml`).
2. **Run workflow** and fill in the **inputs** (workload app/env, CN, OU, validity, maintenance window, allowlist, strict envs).
3. **No `GCP_SA_KEY`** or other cloud secrets are required.

To gate pull requests, call **`cert-validate-reusable.yaml`** via `workflow_call` with the same inputs — see [pipeline.md](pipeline.md).

---

## Use — Azure DevOps

1. Create a pipeline from **`cicd/cert-validate-workflow.yaml`** (and ensure **`cicd/templates/cert-validate-template.yaml`** is on the default branch at the expected path).
2. Set **parameters** on the run to match the variables the template exports (same names as the script).
3. No Google service connection is required for validation-only.

---

## Use — Cloud Build

From the repository root (with `gcloud` configured for your project, only to start the build — the **build step itself does not authenticate to CAS**):

```bash
gcloud builds submit --config=cloudbuild/cert-validate.yaml \
  --substitutions=_WORKLOAD_APP=sample-app,_WORKLOAD_ENV=dev,_COMMON_NAME=api-dev.example.internal,_ORGANIZATIONAL_UNIT=dev-sample-app,_VALIDITY_DAYS=400,_MIN_VALIDITY_DAYS=365,_MAX_VALIDITY_DAYS=730,_MAX_VALIDITY_DAYS_PROD=548,_MAINT_WINDOW_START_MONTH=11,_MAINT_WINDOW_END_MONTH=1,_MAINT_WINDOW_END_DAY=7,_ALLOWED_APPS=sample-app,sample-service,_STRICT_VALIDITY_ENVS=prod .
```

Match substitution names to **`cloudbuild/cert-validate.yaml`** if you extend the config.

---

## Option B — serverless (Cloud Functions)

For **event-driven CSR handling** in GCP (Python, optional CAS), see **[serverless-option.md](serverless-option.md)**. That path **does not** ship Terraform in this repo.

Return to [README](../README.md)
