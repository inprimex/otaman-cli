# `otaman persona` — CLI Design Research (task 3.3)

**Author**: cli-agent
**Date**: 2026-05-30
**Change**: outcome-and-solution-registries
**Scope**: lightweight — personas are auxiliary to outcomes

---

## Purpose

`otaman persona <subcommand>` is the CPO surface for managing the
`<otaman-business>/personas.yaml` registry. Personas are referenced by
outcomes' `as-a` field and are intentionally kept thin: this is a glossary,
not a full user-research system.

Per design.md Q4: personas registry is **included in this proposal** as a thin
auxiliary capability — outcomes can't be authored without personas, so tight
coupling argues for co-location.

---

## Subcommand surface (intentionally minimal)

```
otaman persona <subcommand> [options]

  add <id> --name TEXT --kind KIND [options]
                          Add a new persona
  list [--kind KIND] [--status STATUS]
                          List personas
  show <id> [--with-references]
                          Show full detail (and who references this persona)
  retire <id> [--reason TEXT]
                          Move to retired status (referenced outcomes get a warning)
```

No `promote`, no `history`, no `propose`, no auto-triage — personas don't have
a lifecycle worth modeling beyond active/retired.

---

## Common flags

```
--registry PATH         Override personas.yaml path
--json                  Emit JSON output
--quiet                 Suppress informational output
```

---

## Detailed surface

### `persona add` — register a new persona

```
otaman persona add <id> --name TEXT --kind KIND [options]

Required:
  <id>                    Persona id (kebab-case slug)
  --name TEXT             Human-readable name (e.g., "End user", "Tenant admin")
  --kind KIND             One of: end-user | admin | role | external-stakeholder | system

Optional:
  --description TEXT      Free-form description (1-3 sentences)
  --domain-prefill SRC    Source tag if prefilled from a domain pack (e.g., "software-saas/v1")
  --status STATUS         Initial status. Default: active. Choices: active | retired.
```

Behavior:
- Validates `<id>` matches `^[a-z][a-z0-9-]*$`
- Validates `--kind` against fixed enum
- Sets `created-at`, `created-by`, `status: active` by default
- Emits `[+] Added persona: <id> (<name>) [active]`

Example:
```
$ otaman persona add tenant-admin \
    --name "Tenant administrator" \
    --kind admin \
    --description "Owns billing, user invites, and workspace settings for a tenant."

[+] Added persona: tenant-admin (Tenant administrator) [active]
    Kind: admin
```

---

### `persona list` — enumerate personas

```
otaman persona list [--kind KIND] [--status STATUS]
```

```
$ otaman persona list

  ID                      NAME                       KIND                    STATUS
  end-user                End user                   end-user                active
  tenant-admin            Tenant administrator       admin                   active
  workspace-owner         Workspace owner            admin                   retired
  platform-administrator  Platform administrator     role                    active

  Summary: 4 personas (3 active, 1 retired)
```

`--json` returns array of persona dicts.

---

### `persona show` — full detail

```
otaman persona show <id> [--with-references]
```

```
$ otaman persona show tenant-admin --with-references

  Persona: tenant-admin
  ──────────────────────────────────────────────────────────────────────
  Name:        Tenant administrator
  Kind:        admin
  Status:      active  (since 2026-05-15)
  Domain:      (custom — not from prefill pack)

  Description
  ──────────────────────────────────────────────────────────────────────
  Owns billing, user invites, and workspace settings for a tenant.

  Referenced by                                          [--with-references]
  ──────────────────────────────────────────────────────────────────────
  outcome:JTBD-3-invite-colleagues       (as-a: tenant-admin)
  outcome:JTBD-15-upgrade-paid-plan      (as-a: tenant-admin)
  outcome:JTBD-22-set-workspace-policy   (as-a: tenant-admin)

  3 outcomes reference this persona.
```

`--with-references` reverse-scans `outcomes.yaml` for `as-a == <id>` matches.
Cheap operation: typical registry has <100 outcomes; scan is <10ms.

---

### `persona retire` — mark retired

```
otaman persona retire <id> [--reason TEXT]
```

```
$ otaman persona retire workspace-owner \
    --reason "Consolidated into 'tenant-admin' after Q2 product rename"

[+] Retired persona: workspace-owner
    Warning: 1 active outcome still references this persona:
      outcome:JTBD-7-rename-workspace (status: Drafting)
    Action recommended: update outcome.as-a to a replacement persona, then
    re-run `otaman persona retire`.
```

Behavior:
- Sets `status: retired` (preserved, never deleted)
- Scans `outcomes.yaml` for active outcomes still referencing this persona
- Prints warnings for each; exit code 0 (advisory, not blocking)
- Idempotent (already retired → exit 0 with note)

The "warning instead of block" choice mirrors design.md's broader pattern:
state mutations are advisory; humans retain authority.

---

## In-memory data model

```python
@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    kind: str                # end-user | admin | role | external-stakeholder | system
    description: str = ""
    status: str = "active"   # active | retired
    domain_prefill_source: str | None = None    # e.g., "software-saas/v1"
    retired_reason: str | None = None
    created_at: str = ""
    created_by: str = ""

class PersonaRegistry:
    def __init__(self) -> None:
        self._personas: dict[str, Persona] = {}
    def add(self, persona: Persona) -> None: ...
    def get(self, persona_id: str) -> Persona | None: ...
    def list(self, kind: str | None = None, status: str | None = None) -> list[Persona]: ...
    def retire(self, persona_id: str, reason: str) -> None: ...
```

---

## Argparse wiring sketch

```python
def _add_persona_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("persona", help="program persona registry")
    pp = p.add_subparsers(dest="persona_cmd", required=True)

    pa = pp.add_parser("add", help="register a new persona")
    pa.add_argument("id")
    pa.add_argument("--name", required=True)
    pa.add_argument("--kind", required=True,
                    choices=["end-user","admin","role","external-stakeholder","system"])
    pa.add_argument("--description", default="")
    pa.add_argument("--domain-prefill", default=None)
    pa.set_defaults(func=cmd_persona_add)

    pl = pp.add_parser("list", help="list personas")
    pl.add_argument("--kind")
    pl.add_argument("--status", choices=["active","retired"])
    pl.set_defaults(func=cmd_persona_list)

    ps = pp.add_parser("show", help="show persona detail")
    ps.add_argument("id")
    ps.add_argument("--with-references", action="store_true")
    ps.set_defaults(func=cmd_persona_show)

    pr = pp.add_parser("retire", help="mark a persona retired")
    pr.add_argument("id")
    pr.add_argument("--reason", default="")
    pr.set_defaults(func=cmd_persona_retire)
```

---

## Validation rules

| Rule | When checked | Behaviour on violation |
|---|---|---|
| `id` matches `^[a-z][a-z0-9-]*$` | `add` | exit 2 with error |
| `id` is unique | `add` | exit 2 with error |
| `kind` is in fixed enum | `add` | argparse error (exit 2) |
| Outcome with `as-a: <id>` exists when `retire` called | `retire` | warning printed; exit 0 |
| Persona referenced by `as-a` exists | `outcome add` (cross-validation) | warning; exit 1 if `--strict` |

---

## Integration with `outcome add`

When `otaman outcome add --as-a <persona-id>` runs, it should validate the
persona exists. Pseudocode:

```python
def cmd_outcome_add(args):
    persona_registry = PersonaRegistry.load(...)
    if not persona_registry.get(args.as_a):
        UI.warn(f"Persona '{args.as_a}' not found in personas.yaml.")
        UI.muted("  Run `otaman persona add` first, or use an existing persona.")
        return 1
    # ... proceed with outcome add
```

For prefill, when a program enables outcomes + declares its domain in
`platform.yaml`:

```yaml
program:
  processes:
    outcomes:
      enabled: true
      framework: jtbd
      personas-prefill: [software-saas]   # optional; seeds personas.yaml
```

`otaman init` would seed `personas.yaml` with the canonical persona pack for
the declared domain (e.g., `end-user`, `tenant-admin`, `developer`,
`workspace-owner`). Persona packs live in `otaman-meta/persona-packs/<domain>.yaml`
(future scaffold — out of scope for this research, but a natural follow-on
to `program-vocabulary-registry`'s pack mechanism).

---

## CE/EE gating

Persona management is foundational; available on all editions. No EE gating.

---

## Out of scope (deferred)

- **Multi-language persona names** (i18n): English-only v1
- **Persona attributes beyond `kind`** (e.g., demographics, behaviour traits,
  jobs-to-be-done at persona level rather than outcome level): personas are
  intentionally thin in v1; richer modeling is a v2 conversation
- **Persona-to-persona relationships** (e.g., "tenant-admin manages
  end-user"): if useful, add as `relationships: [{kind, ref}]` later
- **Cross-program persona library**: a future federation concern (v2+)
- **Persona pack curation governance**: same as vocabulary packs — defer to
  the shared `otaman-meta/`-pack curation proposal

---

## Cross-references

- `cli-outcome-subcommand.md` — outcomes reference personas in `as-a`
- `cli-solution-subcommand.md` — solutions don't reference personas directly
  (outcomes do)
- `parser-extension-design-note.md` — annotation parser cohesion
