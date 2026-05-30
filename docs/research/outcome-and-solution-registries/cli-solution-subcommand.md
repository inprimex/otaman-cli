# `otaman solution` — CLI Design Research (task 3.2)

**Author**: cli-agent
**Date**: 2026-05-30
**Change**: outcome-and-solution-registries
**Implementation status**: skeleton-only (no real persistence)

---

## Purpose

`otaman solution <subcommand>` is the CTO authoring surface for the
program's solution registry at `<otaman-business>/solutions.yaml`. CTO uses it
to propose 1-N candidate solutions per outcome, sized in T-shirts, with
structured dependencies + pros/cons. The auto-triage formula (design.md
Q3-bis) consumes these to recommend the cheapest-per-impact solution to the
CEO. The CEO promotes the winner to `Complete` via
`otaman outcome accept-cost --solution <id>`.

---

## Subcommand surface

```
otaman solution <subcommand> [options]

Lifecycle:
  add <id> --outcome OID --description T --t-shirt-size S [options]
                          Add a new candidate solution
  propose <outcome-id> [--from-template T]
                          Interactive wizard: walks the CTO through proposing
                          1-N solutions for an outcome (calls `add` repeatedly)
  list [--outcome OID] [--status S] [--release R]
                          List solutions, optionally filtered
  show <id> [--tasks-status]
                          Show full detail for a solution
  history <id>            Show transitions + edits for this solution

Status transitions:
  promote-to-complete <id>
                          Considering → Complete  (typically called via
                          `otaman outcome accept-cost`, but exposed standalone
                          for direct CTO use)
  discard <id> [--reason TEXT]
                          Considering → Discard (e.g., approach rejected)
```

---

## Common flags (all subcommands)

```
--registry PATH         Override solutions.yaml path
--json                  Emit JSON output
--quiet                 Suppress informational output
--dry-run               Show what would happen; don't write
```

---

## Detailed surface

### `solution add` — propose a candidate

```
otaman solution add <id> --outcome OID --description T --t-shirt-size S [options]

Required:
  <id>                    Solution id (slug: SOL-N-short-description)
  --outcome OID           Outcome this solution serves
  --description T         The "how" (1-3 sentences)
  --t-shirt-size S        Tiny | X-Small | Small | Small-Medium | Medium | Large | X-Large

Optional:
  --release R             Release tag (must exist in platform.yaml.program.releases)
                          Default: post-MVP
  --pro TEXT              Add a pro bullet (repeatable)
  --con TEXT              Add a con bullet (repeatable)
  --depends-on REF        Structured dependency (repeatable). Format:
                            outcome:JTBD-3-invite-colleagues
                            external:Email service provider
                            infrastructure:Identity provider
  --cto-notes TEXT        Free-form CTO context
```

Behavior:
- Validates `<id>` matches `^SOL-\d+-[a-z][a-z0-9-]*$`
- Validates `--outcome` exists in outcomes.yaml and is in `Considering` or `Done`
- Validates `--release` exists in `platform.yaml.program.releases`
- Computes `effort-days` from `t-shirt-size` × `program.t-shirt-scale` map
- Auto-sets `created-at`, `created-by`
- Status defaults to `Considering`
- Validates each `--depends-on` follows the `kind:ref` shape

Example:
```
$ otaman solution add SOL-2-magic-link-only \
    --outcome JTBD-1-create-account-with-email \
    --description "Email-only signup with magic links; no password storage." \
    --t-shirt-size Small \
    --release MVP \
    --pro "Simpler implementation; no password reset flow needed" \
    --pro "Better UX than password-based" \
    --con "Requires reliable email delivery" \
    --depends-on "external:Email service provider"

[+] Added solution: SOL-2-magic-link-only
    Outcome:   JTBD-1-create-account-with-email
    Size:      Small (3 effort-days)
    Release:   MVP
    Status:    Considering
    Bus signal: outcome-estimate-arrived → cpo-agent  (first solution for this outcome)
```

The bus emission only fires on the FIRST solution proposed for a given
outcome — subsequent additions are silent additions to the candidate set.

---

### `solution propose` — interactive multi-solution wizard

```
otaman solution propose <outcome-id> [--from-template T]
```

Interactive wizard that:
1. Loads the outcome and displays the JTBD statement + product-notes
2. Prompts: "How many candidate solutions? (1-5)"
3. For each candidate, walks through:
   - id (suggested: `SOL-<N>-<slug>`)
   - description
   - t-shirt-size (with effort-days preview)
   - release (with autocomplete from `platform.yaml.program.releases`)
   - pros (multi-entry; empty line to end)
   - cons (multi-entry)
   - dependencies
4. Shows a comparison table of the proposed solutions
5. Confirms before writing all entries atomically

Non-interactive (`--from-template`): reads YAML template from stdin or path;
useful for scripts or LLM-generated proposals via the cto-advisor skill.

This is a UX shortcut over repeated `solution add` invocations — same data
written, just easier to author 3+ solutions in one session.

---

### `solution list` — enumerate solutions

```
otaman solution list [--outcome OID] [--status S] [--release R] [--sort-by FIELD]

--sort-by  Sort by: value-rate (default; needs outcome context) | effort-days | created-at
```

Default output:

```
$ otaman solution list --outcome JTBD-1-create-account-with-email --sort-by value-rate

  ID                          STATUS        SIZE          DAYS  RELEASE    VALUE-RATE
  SOL-2-magic-link-only       Considering   Small         3.0   MVP        1.67
  SOL-3-oauth-google          Considering   Small         3.0   post-MVP   1.67
  SOL-1-bcrypt-email-verify   Considering   Medium       10.0   MVP        0.50

  Outcome: JTBD-1-create-account-with-email (impact: L → weight 5)
  Recommendation: SOL-2-magic-link-only (value-rate 1.67, MVP release)
                  tiebreaker: same value-rate as SOL-3, MVP release wins over post-MVP
```

The `value-rate` column only appears when filtered by `--outcome` (the
formula needs the outcome's impact to compute).

`--json` returns array of solution dicts.

---

### `solution show` — full detail

```
otaman solution show <id> [--tasks-status]
```

```
$ otaman solution show SOL-2-magic-link-only --tasks-status

  Solution: SOL-2-magic-link-only
  ──────────────────────────────────────────────────────────────────────
  Outcome:        JTBD-1-create-account-with-email
  Status:         Considering        (since 2026-05-18 by cto-agent)
  Release:        MVP
  T-shirt size:   Small  (3 effort-days)

  Description
  ──────────────────────────────────────────────────────────────────────
  Email-only signup with magic links; no password storage.

  Pros
  ──────────────────────────────────────────────────────────────────────
  • Simpler implementation; no password reset flow needed
  • Better UX than password-based

  Cons
  ──────────────────────────────────────────────────────────────────────
  • Requires reliable email delivery

  Dependencies
  ──────────────────────────────────────────────────────────────────────
  external      Email service provider

  CTO notes
  ──────────────────────────────────────────────────────────────────────
  Recommend Postmark for transactional email; ~$0.001/email at expected volume.

  Tasks (annotated `@solution:SOL-2-magic-link-only`)     [--tasks-status]
  ──────────────────────────────────────────────────────────────────────
  [done] 1.1 @otaman-auth-service @solution:SOL-2-magic-link-only Magic-link generator
  [    ] 1.2 @otaman-web @solution:SOL-2-magic-link-only Signup form
  [    ] 1.3 @otaman-email-svc @solution:SOL-2-magic-link-only Postmark integration

  Progress: 1/3 done (33%)
```

The `--tasks-status` section reverse-walks the bus + tasks.md files (via
bridge-agent's derived link index per design.md Q2) to count completion.

---

### `solution promote-to-complete` — promote to Complete

```
otaman solution promote-to-complete <id>
```

Transitions Considering → Complete. Typically called by `outcome accept-cost`
internally, but exposed for direct CTO use (e.g., when the CTO confirms the
solution independent of the CEO cost-acceptance flow).

Validates:
- Status currently `Considering`
- `t-shirt-size` set
- `effort-days` derived
- Outcome exists + is `Considering`

Side effects:
- Records transition entry
- Does NOT change outcome's `chosen-solution-id` (that's the CEO's authority
  via `outcome accept-cost`)

This separation lets the CTO "lock in" an estimate while waiting for CEO
budget approval.

---

### `solution discard` — reject an approach

```
otaman solution discard <id> [--reason TEXT]
```

```
$ otaman solution discard SOL-3-oauth-google \
    --reason "post-MVP scope; not pursuing for v1 launch"

[+] Discarded solution: SOL-3-oauth-google
    Reason: post-MVP scope; not pursuing for v1 launch
    Recorded in transitions; preserved for audit.
```

Preserves the solution (no hard-delete; consistent with outcome `retire`).
`list` excludes Discard by default.

---

### `solution history` — audit trail

```
otaman solution history <id>
```

```
$ otaman solution history SOL-2-magic-link-only

  History: SOL-2-magic-link-only
  ──────────────────────────────────────────────────────────────────────
  2026-05-18 09:00  added            cto-agent       size: Small (3d)
  2026-05-18 09:00  estimate-emitted bridge-agent    outcome-estimate-arrived
  2026-05-22 10:30  edited           cto-agent       added con: "Requires email delivery"
  2026-05-25 14:00  promoted         cpo-agent       Considering → Complete (via outcome accept-cost)
```

---

## In-memory data model

```python
@dataclass(frozen=True)
class Dependency:
    kind: str        # outcome | external | infrastructure | risk | assumption
    ref: str         # the target id or external name

@dataclass(frozen=True)
class Solution:
    id: str
    outcome_id: str
    status: str               # Considering | Complete | Discard
    release: str
    description: str
    t_shirt_size: str         # Tiny | X-Small | Small | Small-Medium | Medium | Large | X-Large
    effort_days: float        # derived from t-shirt-size × program scale
    pros: tuple[str, ...] = ()
    cons: tuple[str, ...] = ()
    dependencies: tuple[Dependency, ...] = ()
    cto_notes: str = ""
    discard_reason: str | None = None
    created_at: str = ""
    created_by: str = ""
    transitions: tuple[Transition, ...] = ()

class SolutionRegistry:
    def __init__(self, t_shirt_scale: dict[str, float]) -> None:
        self._solutions: dict[str, Solution] = {}
        self._scale = t_shirt_scale     # from platform.yaml
    def add(self, solution: Solution) -> None: ...
    def get(self, solution_id: str) -> Solution | None: ...
    def by_outcome(self, outcome_id: str) -> list[Solution]: ...
    def derive_effort_days(self, t_shirt_size: str) -> float:
        return self._scale.get(t_shirt_size, 1.0)
```

---

## Argparse wiring sketch

```python
def _add_solution_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("solution", help="program solution registry (CTO candidates)")
    sp = p.add_subparsers(dest="solution_cmd", required=True)

    pa = sp.add_parser("add", help="propose a candidate solution")
    pa.add_argument("id")
    pa.add_argument("--outcome", required=True, metavar="OID")
    pa.add_argument("--description", required=True, metavar="TEXT")
    pa.add_argument("--t-shirt-size", required=True,
                    choices=["Tiny","X-Small","Small","Small-Medium","Medium","Large","X-Large"])
    pa.add_argument("--release", default="post-MVP")
    pa.add_argument("--pro", action="append", default=[])
    pa.add_argument("--con", action="append", default=[])
    pa.add_argument("--depends-on", action="append", default=[])
    pa.add_argument("--cto-notes", default="")
    pa.set_defaults(func=cmd_solution_add)
```

---

## T-shirt scale resolution

Default scale (matches Roman's TaskFlow demo):

```yaml
# Built into otaman-cli; programs override in platform.yaml
default_t_shirt_scale:
  Tiny:          1.0
  X-Small:       2.0
  Small:         3.0
  Small-Medium:  5.0
  Medium:       10.0
  Large:        15.0
  X-Large:      30.0
```

Per-program override:

```yaml
# platform.yaml
program:
  t-shirt-scale:
    Tiny: 0.5     # this team is faster than industry default
    X-Small: 1.5
    # ... rest inherits from default
```

On load, the registry merges `default_t_shirt_scale` ← `program.t-shirt-scale`.
Validation: all seven keys must resolve; missing keys raise on first
`add`/`list` invocation.

---

## CE/EE gating

Solution management is **all editions**. Auto-triage is part of the open
solution surface — not gated.

---

## Coordination with outcome accept-cost

The `outcome accept-cost --solution <SOL_ID>` flow is the primary trigger for
`Considering → Complete`. Internally:

1. `outcome accept-cost` validates `--solution` belongs to the outcome
2. Calls `solution.promote-to-complete(SOL_ID)` (writes transition entry)
3. Sets `outcome.chosen-solution-id = SOL_ID`, `outcome.cost-accepted = Yes`
4. Emits a single combined `outcome-cost-accepted` bus message (not two)

This atomicity is important — the outcome and solution must stay consistent.
Implementation note for the impl change: hold a file-lock across both writes.

---

## Open questions for implementation phase

1. **Multi-CTO programs**: same as outcomes — record specific human id;
   any-CTO-can-edit.
2. **Discard vs retire terminology**: outcomes use `retire`, solutions use
   `discard`. Roman's demo uses both — outcomes retire (CPO decision),
   solutions discard (CTO decision after CEO rejection). Naming kept consistent
   with the demo.
3. **Effort-days re-derivation on scale change**: when a program changes
   `t-shirt-scale`, existing solutions' `effort-days` is stale. Recommend:
   re-derive on next load; old value preserved in transitions for audit.
4. **Bulk discard on outcome retire**: when an outcome is retired, do its
   solutions auto-discard? Recommend: no — preserve as orphans with a note;
   manual cleanup via `solution discard`.

---

## Cross-references

- `cli-outcome-subcommand.md` — outcome surface (companion document)
- `cli-persona-subcommand.md` — persona surface
- `parser-extension-design-note.md` — `@solution:<id>` annotation parser
