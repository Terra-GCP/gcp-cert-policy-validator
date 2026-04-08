# Deployment options in this repository

**Option A** (bash + CI) vs **Option B** (`function/` serverless). More on Option B: **[serverless-option.md](serverless-option.md)**.

---

## Feature comparison

| Capability | Option A — `scripts/` + CI | Option B — `function/` |
|------------|---------------------------|------------------------|
| Pre-issuance validation | ✅ `validate-cert-request.sh` env contract | ✅ Python `validator.py` + CSR |
| CAS / Private CA API | ❌ | ✅ Optional issue or validation-mode |
| GCP credentials on runner | ❌ Not needed | ✅ Required |
| Multi-CI (ADO / GHA / Cloud Build) | ✅ Wired in this repo | ❌ You deploy Functions / triggers |
| Terraform in this repo | ❌ | ❌ |

---

## Adoption path

```mermaid
flowchart LR
  A[Option A PR checks] --> B[Tune ALLOWED_APPS and caps]
  B --> C[Option B if event-driven CSR fits]
```

---

Return to [README](../README.md)
