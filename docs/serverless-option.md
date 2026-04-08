# Option B: Serverless CSR pipeline (Cloud Functions)

This repository’s **primary** path is **`scripts/validate-cert-request.sh`** in **CI** (no GCP credentials on the runner). The **`function/`** directory is a **separate alternative** for the **same problem domain** (validate CSR / request parameters, optionally call **Certificate Authority Service**), deployed as **managed serverless** instead of bash on a build agent.

---

## How it differs from the bash/CI path

| | **Bash + CI** (`scripts/`) | **Python serverless** (`function/`) |
|--|-----------------------------|--------------------------------------|
| **Where it runs** | GitHub Actions, ADO, Cloud Build | **Google Cloud Functions** (this code is written for that style: GCS-oriented handlers) |
| **Terraform in this repo** | None | **None** — you bring your own IaC or deploy by hand |
| **GCP auth** | Not required for validation | **Required** (service identity for GCS + Private CA API) |
| **CAS** | Not used | Can **issue** (or validation-mode check) via REST after your policy checks |

**Cloud Run:** The same Python can be packaged as a **container** on **Cloud Run** if you prefer (e.g. HTTP or **Eventarc** triggers). This folder does not ship a Dockerfile or Cloud Run YAML; treat that as an implementation choice on top of the same modules.

---

## What’s in `function/`

| File | Role |
|------|------|
| `main.py` | Entrypoints (e.g. GCS event flow: object created → validate → optional CAS issue → write results). |
| `validator.py` | CSR / policy validation (Python). |
| `cas_client.py` | Thin **CAS API** client (project, pool, template via env vars). |
| `requirements.txt` | Runtime dependencies. |

No **`terraform/`** in this repository: **PKI** (pools, CAs, IAM, trust configs) is **your responsibility** outside this folder.

---

## When to choose this option

- You want **event-driven** issuance (e.g. drop a CSR in a bucket) instead of a **manual pipeline** run.
- You are fine **managing IAM and function deployment** yourself.
- You may still use **`validate-cert-request.sh`** in CI for **PR gates**, and this function for **runtime** requests — policies should stay aligned intentionally.

---

## Local sanity check (no deploy)

Generate a CSR (see **`function/examples/README.md`**), then exercise **`validator.py`** from a small script or REPL if you extend tests locally. **Do not commit private keys.**

---

## See also

- **[setup-and-use.md](setup-and-use.md)** — Option A (bash / CI).

Return to [README](../README.md)
