# `otaman org` + `otaman init-org` CLI Design

> **Author**: cli-agent  
> **Date**: 2026-05-27  
> **Tasks**: multi-tenant-org-runtime 3.1 + 3.2 + 3.3  
> **Output**: Design of `otaman org list|switch|status`, `otaman init-org <slug>`,
>             and `--org` flag on `otaman onboard program-init`.

---

## Background

The multi-tenant runtime introduces an **Organisation** as a first-class entity above Programs.
Each Org has an isolated workdir at `~/orgs/<org-slug>/` (layout per `org-workdir-skeleton.md`).
These CLI subcommands let operators manage which Org is active and scaffold new Orgs.

For EE + `features.multi_tenant_experimental` deployments, multiple Orgs coexist on one host.
Mode 1 / CE deployments have exactly one Org (the implicit `_platform` context).

---

## `otaman org` subcommands

### `otaman org list`

Enumerates all known Organisations by walking `~/orgs/*/`.

```
$ otaman org list
  SLUG         DISPLAY NAME       MODE     EDITION  PROGRAMS  CONTAINER
  ─────────────────────────────────────────────────────────────────────
  acme-corp    Acme Corporation   Mode 2   EE       3         running
  beta-labs    Beta Labs          Mode 1   CE       1         stopped
* staging      Staging Env        Mode 2   EE       2         running

  * = active org
  3 organisations found.

$ otaman org list --json
[
  {"slug": "acme-corp", "display_name": "Acme Corporation", "mode": 2,
   "edition": "ee", "programs": 3, "container_status": "running", "active": false},
  ...
]
```

**Discovery logic:**
1. Walk `~/orgs/*/` — any directory containing `config/org.yaml` is an Org
2. Read `slug`, `display_name`, `runtime_mode`, `edition` from `org.yaml`
3. Count `programs/` subdirectories for program count
4. Query `docker ps` for `otaman-org-<slug>` container status (graceful — shows `unknown` if Docker unavailable)
5. Read `~/.otaman/active-org` for the active Org marker (falls back to single-Org if only one exists)

**Options:**
- `--json` — emit JSON array
- `--no-docker` — skip container status check (faster)
- `--workdir <path>` — override the `~/orgs/` root

**Role-gating:** any authenticated user.

---

### `otaman org switch <slug>`

Sets the active Org for subsequent CLI commands.

```
$ otaman org switch acme-corp
  [+] Active org: acme-corp (Acme Corporation)
      Subsequent commands will use ~/orgs/acme-corp/ as the Org context.
      To reset: otaman org switch --clear

$ otaman org switch nonexistent
  [X] Organisation 'nonexistent' not found under ~/orgs/
      Run 'otaman org list' to see available organisations.
      Run 'otaman init-org nonexistent' to create it.
```

**Persistence:** Writes `slug` to `~/.otaman/active-org` (plain text, single line). All subsequent CLI commands that are org-context-aware read this file.

**Single-Org mode:** If there is exactly one Org, `switch` is a no-op that confirms the active Org without writing.

**Options:**
- `--clear` — removes `~/.otaman/active-org`, reverting to single-Org auto-detection

---

### `otaman org status`

Reports the current active Org, its configuration, and container runtime state.

```
$ otaman org status
  Active organisation: acme-corp

  Identity
    Slug:         acme-corp
    Display name: Acme Corporation
    Contact:      ops@acme.com
    Edition:      EE
    Runtime mode: Mode 2 (Zitadel)

  Programs (3)
    ● acme-platform   (specs: ~/orgs/acme-corp/programs/acme-platform/acme-platform-specs)
    ● acme-billing    (specs: ~/orgs/acme-corp/programs/acme-billing/acme-billing-specs)
    ○ acme-mobile     (specs: not yet cloned — run: otaman clone acme-mobile)

  Container
    Image:   ghcr.io/inprimex/otaman-org-ee:1.2.0
    Status:  running  (up 3d 14h)
    Port:    4040 (bridge)  4041 (runner)

  Secrets backend: vault (https://vault.acme.com)

  Run 'otaman org list' to see all organisations.
  Run 'otaman launch acme-corp <agent>' to attach to an agent session.
```

**Options:**
- `--json` — machine-readable output
- `--org <slug>` — query a specific Org without switching

---

## `otaman init-org <slug>`

Minimal CLI scaffold for creating a new Organisation's workdir structure.
The full interactive configuration happens in the bootstrap container wizard;
this command creates the bare directory skeleton so the container can be started.

```
$ otaman init-org acme-corp
  Creating Organisation 'acme-corp'...

  [+] Created ~/orgs/acme-corp/config/
  [+] Created ~/orgs/acme-corp/config/org.yaml  (skeleton — edit before starting container)
  [+] Created ~/orgs/acme-corp/config/.gitignore
  [+] Created ~/orgs/acme-corp/state/
  [+] Created ~/orgs/acme-corp/programs/
  [+] Created ~/orgs/acme-corp/init-org.sh  (non-interactive re-provisioning script)

  Next steps:
    1. Edit ~/orgs/acme-corp/config/org.yaml to set display_name, edition, programs, etc.
    2. OR run the interactive bootstrap wizard:
         docker run -it --rm -v ~/orgs/acme-corp:/workspace ghcr.io/inprimex/otaman-bootstrap:latest
    3. Then start the Org container:
         otaman launch acme-corp --start

  Run 'otaman org status --org acme-corp' to see the current state.

$ otaman init-org acme-corp  # if already exists
  [i] Organisation 'acme-corp' already exists at ~/orgs/acme-corp/
      Use 'otaman org status --org acme-corp' to inspect it.
      Use the bootstrap wizard to edit its configuration.
```

### Scaffold layout

`init-org` creates the following minimal structure:

```
~/orgs/<slug>/
  config/
    org.yaml          ← skeleton (slug set; all other fields to fill in)
    .gitignore        ← covers secrets.env, *.jwt, *.pem, state/
  state/              ← empty (populated by bridge + runner on first start)
  programs/           ← empty (populated by otaman onboard program-init)
  init-org.sh         ← replay script for non-interactive re-provisioning
```

### Skeleton `org.yaml`

```yaml
# org.yaml — generated by `otaman init-org acme-corp`
# Edit this file or run the interactive bootstrap wizard to complete configuration.
# Reference: https://docs.otaman.dev/org-yaml-schema

schema_version: 1

identity:
  slug: acme-corp
  display_name: ""       # TODO: set display name
  contact: ""            # TODO: set contact email

edition: ce              # TODO: ce or ee
runtime_mode: 1          # TODO: 1 (local) or 2 (Mode 2+ / Zitadel)

programs: []             # TODO: add program slugs

secrets:
  backend: env-file      # TODO: configure secret backend
  env_file:
    path: config/secrets.env

features:
  multi_tenant_experimental: false

# federation:
#   trusted_peers: []   # reserved; must be empty in v1.0
```

### Options

- `--display-name "Acme Corp"` — pre-fill `identity.display_name` in skeleton
- `--edition ce|ee` — pre-fill edition
- `--mode 1|2` — pre-fill runtime_mode
- `--no-wizard-hint` — suppress "run the bootstrap wizard" guidance (for scripting)
- `--workdir <path>` — override `~/orgs/` root

**Idempotent:** Safe to re-run; does not overwrite existing `org.yaml` or state.  
**Role-gating:** `operator`.

---

## `otaman onboard program-init --org <slug>`

Adds `--org <slug>` flag to the existing `program-init` subcommand to route a new
Program into a specific Organisation rather than the default/active context.

### Behaviour

```
$ otaman onboard program-init --org acme-corp --program acme-billing
  [i] Organisation: acme-corp  (~/orgs/acme-corp/)
  [i] Program will be created under ~/orgs/acme-corp/programs/acme-billing/

  Welcome to Otaman program-init...
  ...
  [+] Generated ~/orgs/acme-corp/programs/acme-billing/acme-billing-specs/platform.yaml
  [+] Registered 'acme-billing' in ~/orgs/acme-corp/config/org.yaml
```

After init, `programs:` in `org.yaml` gains the new program slug:

```yaml
programs:
  - acme-platform
  - acme-billing   # ← newly added
```

### Implementation change to `runner.py`

```python
# runner.py additions

def _resolve_org_workdir(org_slug: str | None) -> Path | None:
    """Return ~/orgs/<slug>/ if org_slug is given and the dir exists."""
    if not org_slug:
        return None
    orgs_root = Path.home() / "orgs"
    org_dir = orgs_root / org_slug
    if not org_dir.is_dir():
        raise ValueError(f"Organisation '{org_slug}' not found at {org_dir}")
    return org_dir

def _register_program_in_org(org_dir: Path, program_slug: str) -> None:
    """Add program_slug to org.yaml programs list (idempotent)."""
    org_yaml = org_dir / "config" / "org.yaml"
    # load with ruamel.yaml to preserve comments + formatting
    ...
```

`platform.yaml` is written to `~/orgs/<slug>/programs/<program>/` instead of `~/` when `--org` is provided.

### Default Org resolution

When `--org` is omitted:
1. If `~/.otaman/active-org` exists → use that Org's workdir
2. If exactly one Org in `~/orgs/` → use it implicitly
3. If no Orgs found → fall back to single-tenant layout (existing behaviour: `~/program/program-specs/`)

This preserves full backwards compatibility with existing single-tenant `program-init` usage.

---

## Argparse wiring (sketch)

```python
# main.py additions

# org subcommand
p_org = sub.add_parser("org", help="manage Organisations (multi-tenant EE)")
org_sub = p_org.add_subparsers(dest="org_cmd", required=True)

p_org_list = org_sub.add_parser("list", help="list all Organisations")
p_org_list.add_argument("--json", action="store_true")
p_org_list.add_argument("--no-docker", action="store_true")
p_org_list.set_defaults(func=cmd_org_list)

p_org_switch = org_sub.add_parser("switch", help="set active Organisation")
p_org_switch.add_argument("slug", nargs="?", help="org slug (omit with --clear)")
p_org_switch.add_argument("--clear", action="store_true")
p_org_switch.set_defaults(func=cmd_org_switch)

p_org_status = org_sub.add_parser("status", help="show active Organisation status")
p_org_status.add_argument("--org", metavar="SLUG")
p_org_status.add_argument("--json", action="store_true")
p_org_status.set_defaults(func=cmd_org_status)

# init-org subcommand
p_init_org = sub.add_parser("init-org", help="scaffold a new Organisation workdir")
p_init_org.add_argument("slug", help="org slug (kebab-case, e.g. acme-corp)")
p_init_org.add_argument("--display-name", metavar="NAME")
p_init_org.add_argument("--edition", choices=["ce", "ee"])
p_init_org.add_argument("--mode", type=int, choices=[1, 2])
p_init_org.add_argument("--workdir", metavar="PATH")
p_init_org.add_argument("--no-wizard-hint", action="store_true")
p_init_org.set_defaults(func=cmd_init_org)

# --org flag on program-init (added to existing parser)
p_prog.add_argument(
    "--org", metavar="SLUG",
    help="Organisation to route this program into (multi-tenant EE)",
)
```

---

## Interaction with `features.multi_tenant_experimental`

- `otaman org list`, `otaman org status` work on all editions (no EE gate) — operators need visibility into their Org layout regardless.
- `otaman org switch` and multi-Org usage are EE-gated when `features.multi_tenant_experimental` is not set. On CE single-Org, `switch` simply confirms the current Org.
- `otaman init-org` always works (needed to scaffold the first Org on any edition); the gate is at the feature level in the container, not in the CLI command.
- `program-init --org` gate: if the target Org's `org.yaml` has `features.multi_tenant_experimental: false` and there would be a second program in the same Org, the CLI warns but does not block (the feature flag gating is a runtime concern, not init-time).

---

## Coordination notes

- Workdir layout per `multi-tenant-org-runtime/research/org-workdir-skeleton.md` (deploy-agent, task 4.2).
- `org.yaml` schema per core-agent task 1.2.
- `otaman org status` container status check aligns with deploy-agent's `docker-compose-template.md` container naming convention (`otaman-org-<slug>`).
- `otaman launch <org> <agent>` (in `containerized-agent-execution`) is the next step after `init-org` scaffolds the workdir and starts the container.
