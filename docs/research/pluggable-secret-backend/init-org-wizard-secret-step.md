# `otaman init-org` Wizard — Secret-Backend Selection Step

> **Author**: cli-agent  
> **Date**: 2026-05-27  
> **Task**: pluggable-secret-backend 3.2  
> **Output**: Design of the secret-backend selection step in the init-org wizard.

---

## Where this step lives in the init-org flow

The bootstrap container's `init-org` wizard (defined in `containerized-agent-execution/research/bootstrap-wizard-flow.md`) has a 9-step flow. The secret-backend selection is **Step 5 — Secret backend**.

```
Step 1  — Identity (org slug, display name, contact)
Step 2  — Programs (which programs belong to this org)
Step 3  — Edition (CE / EE)
Step 4  — License validation (EE only)
Step 5  — Secret backend  ← this document
Step 6  — Integrations (git platform, Zitadel, etc.)
Step 7  — Runtime mode (Mode 1 / Mode 2+)
Step 8  — Confirmation
Step 9  — Write + next-steps
```

---

## Step 5 — Secret backend

### Entry condition
- Always shown (CE and EE).
- EE gets additional backend options.

### Prompt sequence

#### 5a — Backend selection (select)

```
  Step 5 of 9 — Secret backend

  Which backend should store this Organisation's secrets?

    ○ env-file        Store in ~/orgs/<org>/config/secrets.env  (simple, portable)
    ○ os-keyring      Store in OS keychain  (dev laptops; macOS Keychain / Linux Secret Service)
    ○ vault           HashiCorp Vault  [EE]
    ○ aws-secrets-manager  AWS Secrets Manager  [EE]
    ○ gcp-secret-manager   GCP Secret Manager  [EE]
    ○ azure-key-vault      Azure Key Vault  [EE]
    ○ 1password-connect    1Password Connect  [EE]
    ○ doppler              Doppler  [EE]
    ○ infisical            Infisical  [EE]

  [EE] backends require an active EE license.
  Default: env-file
```

CE deployments show only `env-file` and `os-keyring` (EE options are filtered).

#### 5b — Per-backend follow-up prompts

Each backend has its own follow-up after selection:

---

**env-file** (CE + EE)
```
  secrets.env path  [~/orgs/<org-slug>/config/secrets.env]:  ___
  Auto-generate .gitignore entry?  [Y/n]:
```
No further prompts. Simplest backend.

---

**os-keyring** (CE + EE)
```
  Keychain namespace prefix  [otaman/<org-slug>]:  ___
```
The namespace prefix scopes all keys for this Org within the OS keychain.

---

**vault** (EE)
```
  Vault address  [https://vault.example.com]:  ___

  Authentication method:
    ○ token          Static token (dev/test only)
    ○ approle        AppRole (recommended for production)
    ○ kubernetes     Kubernetes service account JWT
    ○ aws-iam        AWS IAM (EC2/ECS/Lambda)

  [If token]:
    Vault token (input hidden):  ___

  [If approle]:
    Role ID:    ___
    Secret ID (input hidden):  ___

  [If kubernetes]:
    Kubernetes role name:   ___
    Service account path  [/var/run/secrets/kubernetes.io/serviceaccount/token]:  ___

  [If aws-iam]:
    Vault AWS auth role:  ___

  Vault path prefix  [otaman/<org-slug>/]:  ___
```

---

**aws-secrets-manager** (EE)
```
  AWS Region  [us-east-1]:  ___

  Authentication method:
    ○ iam-role     Instance/task IAM role (recommended)
    ○ access-key   Static access key (dev only)

  [If access-key]:
    AWS Access Key ID:         ___
    AWS Secret Access Key (hidden):  ___

  Secret name prefix  [otaman/<org-slug>/]:  ___
```

---

**gcp-secret-manager** (EE)
```
  GCP Project ID:  ___

  Authentication method:
    ○ workload-identity   Workload Identity (recommended for GKE)
    ○ service-account     Service account JSON key file

  [If service-account]:
    Path to service-account JSON  [~/orgs/<org>/config/gcp-sa.json]:  ___

  Secret name prefix  [otaman-<org-slug>-]:  ___
```

---

**azure-key-vault** (EE)
```
  Key Vault URL  [https://<vault-name>.vault.azure.net]:  ___

  Authentication method:
    ○ managed-identity   Azure Managed Identity (recommended)
    ○ client-secret      Client secret (app registration)

  [If client-secret]:
    Tenant ID:           ___
    Client ID:           ___
    Client Secret (hidden):  ___

  Secret name prefix  [otaman-<org-slug>-]:  ___
```

---

**1password-connect** (EE)
```
  Connect server URL  [https://connect.example.com]:  ___
  Connect token (hidden):  ___
  Vault name  [otaman-<org-slug>]:  ___
```

---

**doppler** (EE)
```
  Project name  [otaman-<org-slug>]:  ___
  Config (environment)  [production]:  ___
  Service token (hidden):  ___
```

---

**infisical** (EE)
```
  Site URL  [https://app.infisical.com]:  ___
  Project ID:  ___
  Environment  [production]:  ___
  Client ID:   ___
  Client Secret (hidden):  ___
```

---

#### 5c — Validation

After each backend's prompts, the wizard performs a **live connectivity test** if the backend supports it:

```
  [i] Testing connection to vault at https://vault.example.com...
  [+] Connection successful.  Vault is reachable and token is valid.
```

On failure:
```
  [!] Cannot reach vault at https://vault.example.com: connection refused
      Check the address and ensure the Vault server is running.
      You can skip this test and configure later: [s]kip / [r]etry / [q]uit
```

`env-file` and `os-keyring` skip the connectivity test (no server to test).

---

### Non-interactive mode

For automation / CI bootstrapping, all step 5 inputs can be supplied via flags:

```bash
otaman init-org acme-corp \
  --secret-backend vault \
  --secret-backend-config "address=https://vault.example.com,auth=approle,role_id=abc123,secret_id=xyz789,path_prefix=otaman/acme-corp/"
```

The `--secret-backend-config` value is a comma-separated `key=value` string matching the backend's config keys. Values containing commas must be quoted.

For sensitive values (tokens, secrets):
```bash
otaman init-org acme-corp \
  --secret-backend vault \
  --secret-backend-config "address=https://vault.example.com,auth=token" \
  --secret-backend-secret VAULT_TOKEN   # reads from env var VAULT_TOKEN
```

`--secret-backend-secret <ENV_VAR>` reads the backend's primary credential from the named environment variable rather than from a config key (prevents token leakage in shell history).

---

### Output to org.yaml

After confirmation (Step 8), the wizard writes the selected backend to `org.yaml`:

```yaml
# org.yaml (excerpt)
secrets:
  backend: vault
  vault:
    address: https://vault.example.com
    auth: approle
    role_id: "abc123"
    # secret_id is NEVER written to org.yaml — it is stored in the backend itself
    path_prefix: otaman/acme-corp/
```

Sensitive values (tokens, secret IDs, static keys) are **never written to org.yaml**. They are stored via:
- `env-file`: directly in `secrets.env`
- `os-keyring`: stored under the namespace prefix in the OS keychain
- EE backends: stored in a bootstrap secret (the backend's own secret store for its own credentials, following the vault-for-vault-creds pattern from `vault-sidecar-design.md`)

---

### CE/EE filter logic

```python
def _available_backends(edition: str) -> list[str]:
    ce_backends = ["env-file", "os-keyring"]
    ee_backends = [
        "vault", "aws-secrets-manager", "gcp-secret-manager",
        "azure-key-vault", "1password-connect", "doppler", "infisical",
    ]
    return ce_backends + (ee_backends if edition == "ee" else [])
```

The filter runs at wizard entry (Step 3 sets `edition`; Step 5 uses it). No `if EE` branches in the rendering code — the options list is computed once and passed to the select prompt.

---

## Coordination notes

- This step's design is consistent with `pluggable-secret-backend/research/backend-library-survey.md` which confirmed the CE/EE backend split.
- The `--secret-backend-config` non-interactive flag format mirrors the pattern used by `otaman session spawn --agent A --repo R` for consistent CLI ergonomics.
- The `${secret:...}` resolver (core-agent task 1.4) handles runtime consumption; this step is one-time configuration only.
