# Diagram gallery — GCP Certificate Policy Validator

---

## 1. Scope (validation only)

```mermaid
flowchart TB
  Req[Certificate request parameters] --> Val[validate-cert-request.sh]
  Val -->|0| OK[Pipeline success]
  Val -->|1| BAD[Pipeline failure]
```

---

## 2. CI parity (three surfaces)

```mermaid
flowchart LR
  ADO[Azure DevOps] --> S[validate-cert-request.sh]
  GHA[GitHub Actions] --> S
  CB[Cloud Build] --> S
```

---

## 3. Option A and Option B (same repo)

```mermaid
flowchart LR
  A[Option A — bash on CI runner]
  B[Option B — Python on Cloud Functions]
  A -.->|align rules if both used| B
```

---

## 4. No-credentials boundary

```mermaid
flowchart TB
  Runner[Hosted runner / Cloud Build worker]
  Script[Validation script]
  Runner --> Script
  GCP[Google Cloud APIs]
  Script -.->|no calls| GCP
```

---

## 5. Merge request gate (typical use)

```mermaid
sequenceDiagram
  participant Dev as Developer
  participant PR as Pull Request
  participant WF as cert-validate-reusable
  participant Pol as validate-cert-request.sh
  Dev->>PR: Proposes cert parameters or config change
  PR->>WF: workflow_call with inputs
  WF->>Pol: Run checks
  Pol-->>WF: exit code
  WF-->>PR: Pass or fail check
```

---

Return to [README](../README.md)
