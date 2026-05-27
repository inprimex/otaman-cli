# `otaman secret` Admin Subcommand — CLI Design

> **Author**: cli-agent  
> **Date**: 2026-05-27  
> **Tasks**: pluggable-secret-backend 3.1 + 3.3  
> **Output**: Design of the `otaman secret` admin subcommand surface.

---

## Command surface

```
otaman secret <subcommand> [options]

Subcommands:
  list                List all known secret keys in the active backend
  get <key>           Retrieve a secret value (copy to clipboard by default)
  set <key>           Store a secret value (prompt for value, no echo)
  rotate <key>        Trigger key rotation in the active backend  [EE only]
  backends            List available backends and show which is active
```

---

## Subcommand specs

### `otaman secret list`

Enumerates all key names known to the active backend.

```
$ otaman secret list
Backend: vault  (active)
  api-key/stripe-live
  api-key/stripe-test
  db/prod-password
  zitadel/admin-token
  3 secrets total

$ otaman secret list --json
{"backend": "vault", "keys": ["api-key/stripe-live", ...], "total": 3}
```

**Options:**
- `--backend <name>` — query a specific backend instead of the active one
- `--json` — emit JSON instead of table
- `--org <slug>` — restrict to the specified Organisation's secret namespace

**Role-gating:** `operator` or `developer` (read access).

---

### `otaman secret get <key>`

Retrieves a secret value. **NEVER prints the value to the terminal by default.**

#### Default behaviour (clipboard)

```
$ otaman secret get api-key/stripe-live
  [+] Copied 'api-key/stripe-live' to clipboard.  (expires from clipboard in 45 s)
```

Uses OS clipboard:
- Linux: `xclip` or `xsel` (falls back to `wl-clipboard` on Wayland)
- macOS: `pbcopy`
- Windows: `clip`

If clipboard is unavailable (no display, CI), exits with an informative error:
```
  [X] Cannot copy to clipboard: no display available.
      Use '--show' in an interactive terminal or pipe via stdin.
      Hint: not safe to run `secret get` in CI — use ${secret:...} resolver in config instead.
```

#### `--show` flag (explicit terminal display)

Requires an interactive TTY. Prompts for confirmation before printing.

```
$ otaman secret get api-key/stripe-live --show
  [!] WARNING: the secret value will be printed to your terminal.
      It may appear in shell history, screen recordings, or log capture.
  Confirm? [y/N]: y

  api-key/stripe-live = sk_live_abc123...xyz  [SENSITIVE — clear your terminal after use]
```

If non-interactive (piped/CI), `--show` exits with:
```
  [X] --show requires an interactive TTY.  Pipe use is intentionally disabled.
```

**Options:**
- `--show` — print value with confirmation (interactive TTY required)
- `--backend <name>` — query a specific backend
- `--org <slug>` — org-scoped lookup
- `--clip-timeout <seconds>` — override clipboard auto-clear timeout (default: 45s)

**Role-gating:** `operator` or `developer`.

---

### `otaman secret set <key>`

Stores a secret interactively. Value is read via a masked prompt — never from a CLI argument (prevents shell history leakage).

```
$ otaman secret set api-key/stripe-live
  Setting 'api-key/stripe-live' in backend: vault
  New value (input hidden):
  Confirm value (input hidden):
  [+] Secret stored.
```

Piped value via stdin is supported for scripting:
```bash
echo "sk_live_abc123" | otaman secret set api-key/stripe-live --stdin
```
`--stdin` is required to prevent accidental pipe use.

**Options:**
- `--stdin` — read value from stdin (scripting; suppresses masking)
- `--backend <name>` — target a specific backend
- `--org <slug>` — store in org-scoped namespace
- `--force` — overwrite existing key without confirmation prompt

**Role-gating:** `operator` only.

---

### `otaman secret rotate <key>` [EE only]

Triggers key rotation via the active backend's native rotation API. Not all backends support rotation; those that don't return a clear error.

```
$ otaman secret rotate db/prod-password
  [i] Requesting rotation of 'db/prod-password' via vault...
  [+] Rotation successful.  New version: 5  (old version 4 still valid for 24h grace period)
  [i] Dependent services may need restart to pick up the new credential.

$ otaman secret rotate db/prod-password  # on a CE deployment
  [X] 'rotate' requires EE edition.  Current edition: CE.
      Upgrade to EE or rotate manually in your backend.
```

**Options:**
- `--grace-period <duration>` — how long the old version stays valid (e.g., `24h`, `0s`)
- `--backend <name>` — target a specific backend
- `--org <slug>` — org-scoped key

**Role-gating:** `operator` only.

---

### `otaman secret backends`

Lists available and configured backends.

```
$ otaman secret backends
  Available backends:
    ● env-file      [active]  path: ~/orgs/acme/config/secrets.env
    ○ os-keyring    [configured]  namespace: otaman/acme
    ○ vault         [not configured]  — run: otaman secret backends configure vault

$ otaman secret backends --json
{
  "active": "env-file",
  "backends": [
    {"name": "env-file", "status": "active", "config": {"path": "..."}},
    ...
  ]
}
```

`otaman secret backends configure <name>` launches the per-backend interactive configuration wizard (described in `init-org-wizard-secret-step.md`).

**Role-gating:** read = anyone; configure = `operator`.

---

## Safety contract for `otaman secret get`

The full safety contract (task 3.3):

| Scenario | Behaviour |
|---|---|
| Interactive TTY, no flags | Copy to clipboard; print confirmation |
| Interactive TTY, `--show` | Prompt for confirmation; print to terminal |
| Non-interactive (pipe/CI) | Exit with error; suggest `${secret:...}` resolver |
| `--show` in non-interactive | Exit with error; no value emitted |
| Clipboard unavailable | Exit with error; suggest `--show` in interactive TTY |

**Rationale:** Secrets in CI pipelines should use the `${secret:...}` resolver in config files, not `otaman secret get`. The CLI's `get` command is for human operators doing ad-hoc inspection only.

**Shell history:** The `<key>` argument (the key name) is safe to appear in history. The secret value never appears in any argument position.

**Audit log:** Every `secret get`, `secret set`, and `secret rotate` call is written to the org's audit log (bus message type `secret-access`), including timestamp, actor, key name (NOT value), operation, and backend name.

---

## Argparse wiring (sketch)

```python
# In otaman_cli/main.py or otaman_cli/secret/cli.py

p_secret = sub.add_parser("secret", help="manage secrets in the active backend")
secret_sub = p_secret.add_subparsers(dest="secret_cmd", required=True)

# list
p_list = secret_sub.add_parser("list", help="list secret keys")
p_list.add_argument("--backend", help="target a specific backend")
p_list.add_argument("--org", metavar="SLUG", help="restrict to org namespace")
p_list.add_argument("--json", action="store_true")
p_list.set_defaults(func=cmd_secret_list)

# get
p_get = secret_sub.add_parser("get", help="retrieve a secret (clipboard by default)")
p_get.add_argument("key", help="secret key name")
p_get.add_argument("--show", action="store_true",
                   help="print value to terminal (interactive TTY + confirmation required)")
p_get.add_argument("--backend")
p_get.add_argument("--org", metavar="SLUG")
p_get.add_argument("--clip-timeout", type=int, default=45,
                   help="seconds before clipboard auto-clear (default: 45)")
p_get.set_defaults(func=cmd_secret_get)

# set
p_set = secret_sub.add_parser("set", help="store a secret (masked prompt)")
p_set.add_argument("key", help="secret key name")
p_set.add_argument("--stdin", action="store_true",
                   help="read value from stdin (scripting)")
p_set.add_argument("--backend")
p_set.add_argument("--org", metavar="SLUG")
p_set.add_argument("--force", action="store_true")
p_set.set_defaults(func=cmd_secret_set)

# rotate (EE)
p_rotate = secret_sub.add_parser("rotate", help="rotate a secret [EE only]")
p_rotate.add_argument("key")
p_rotate.add_argument("--grace-period", default="24h")
p_rotate.add_argument("--backend")
p_rotate.add_argument("--org", metavar="SLUG")
p_rotate.set_defaults(func=cmd_secret_rotate)

# backends
p_backends = secret_sub.add_parser("backends", help="list / configure backends")
p_backends.add_argument("--json", action="store_true")
backends_sub = p_backends.add_subparsers(dest="backends_action")
p_backends_cfg = backends_sub.add_parser("configure", help="configure a backend")
p_backends_cfg.add_argument("backend_name")
p_backends.set_defaults(func=cmd_secret_backends)
```

---

## Cross-cutting notes

- **`${secret:...}` resolver is the non-interactive path** — agents, CI, and automation should always use it rather than shelling out to `otaman secret get`.
- **Per-Org namespacing** — all backends MUST support a namespace prefix so secrets from different Orgs don't collide in shared backends (e.g., Vault path `otaman/<org-slug>/...`).
- **EE edition guard** — `rotate` and EE-only backends check `detect_edition()` at dispatch time and exit with a clear error on CE.
- **Implementation note** — the actual backend drivers live in `otaman-core`; this CLI is a thin adapter layer. Error messages from the driver are surfaced verbatim with an `[X]` prefix.
