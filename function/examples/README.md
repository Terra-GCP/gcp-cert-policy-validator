# Examples (generate your own — nothing committed)

Test CSRs and keys are **not** stored in git. Generate a throwaway CSR locally:

```bash
openssl req -new -newkey rsa:2048 -nodes \
  -keyout /tmp/example.key \
  -subj "/CN=api-dev.example.internal/OU=dev-sample-app/O=Example Inc/C=US" \
  -out /tmp/example.csr
```

Use **`/tmp/example.key`** only on your machine; **do not** commit `*.key` files.

Optional metadata JSON shape for your integration (fields depend on your `main.py` contract), e.g.:

```json
{
  "validity_days": 400,
  "requested_by": "operator@example.com",
  "purpose": "integration-test"
}
```

Remove `example.csr` after testing or keep it outside the repo.
