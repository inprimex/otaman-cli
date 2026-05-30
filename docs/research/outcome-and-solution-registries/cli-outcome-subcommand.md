# `otaman outcome` — CLI Design Research (task 3.1)

**Author**: cli-agent
**Date**: 2026-05-30
**Change**: outcome-and-solution-registries
**Implementation status**: skeleton-only (no real persistence)

---

## Purpose

`otaman outcome <subcommand>` is the CEO/CPO + CTO authoring surface for the
program's outcome registry at `<otaman-business>/outcomes.yaml`. It covers the
full JTBD lifecycle: author → triage → estimate-request → cost-accept/reject →
deploy → retire.

The surface is role-aware (different subcommands write CEO-owned vs CTO-owned
fields) but does NOT enforce role gating in v0 — that's deferred to the
follow-on impl change once `role-assignments` in `platform.yaml` is wired in.

---

## Subcommand surface

```
otaman outcome <subcommand> [options]

Lifecycle:
  add <id> --as-a P --i-want-to T --so-i-can U [options]
                          Create a new outcome (status: Drafting)
  list [--status S] [--priority P] [--category C] [--persona P]
                          List outcomes, optionally filtered
  show <id> [--with-solutions] [--with-tasks]
                          Show full detail for an outcome
  history <id>            Show transitions + edits for this outcome

Status transitions:
  promote <id>            Drafting → Considering (CPO ready for CTO)
  demote <id>             Considering → Drafting (back to authoring)
  request-estimate <id>   Mark estimate-requested: Yes (signal to CTO)
  accept-cost <id> --solution SOL_ID
                          CEO accepts cost; promotes solution to Complete
  reject-cost <id> [--reason TEXT]
                          CEO rejects cost; emits outcome-cost-rejected bus msg
  retire <id> [--reason TEXT]
                          Move to Discard status (preserved for audit)
```

---

## Common flags (all subcommands)

```
--registry PATH         Override outcomes.yaml path (default: <otaman-business>/outcomes.yaml)
--json                  Emit JSON output
--quiet                 Suppress informational output
--dry-run               Show what would happen; don't write
```

---

## Detailed surface

### `outcome add` — author a new outcome

```
otaman outcome add <id> --as-a PERSONA_ID --i-want-to TEXT --so-i-can TEXT [options]

Required:
  <id>                    Outcome id (slug: JTBD-N-short-description)
  --as-a PERSONA_ID       Persona reference (must exist in personas.yaml)
  --i-want-to TEXT        Incremental outcome (smallest shippable change)
  --so-i-can TEXT         Durable user benefit

Optional:
  --category C            Free-form category (e.g., "Onboarding")
  --impact TIER           Business impact: XS|S|M|L|XL. Default: M.
  --priority P            P0|P1|P2|P3. Default: P2.
  --product-notes TEXT    CPO context for CTO (free-form)
  --status STATUS         Initial status. Default: Drafting.
                          Choices: Drafting | Considering | Done | Discard
```

Behavior:
- Validates `<id>` matches `^JTBD-\d+-[a-z][a-z0-9-]*$`
- Validates `--as-a` against `personas.yaml`; warns if persona is `retired`
- Auto-sets `created-at`, `created-by` from current identity
- Emits `[+] Added outcome: <id> (status: Drafting)` and prints a one-line summary
- The synonym `--statement-as-a/--statement-i-want-to/--statement-so-i-can` accepted (verbose form for scripted authoring)

Example:
```
$ otaman outcome add JTBD-1-create-account-with-email \
    --as-a end-user \
    --i-want-to "Create an account with my email" \
    --so-i-can "Start using TaskFlow without invitation" \
    --category Onboarding \
    --impact L \
    --priority P0

[+] Added outcome: JTBD-1-create-account-with-email
    Status:   Drafting
    Impact:   L
    Priority: P0
    Persona:  end-user
```

---

### `outcome list` — enumerate outcomes

```
otaman outcome list [--status S] [--priority P] [--category C] [--persona P] [--sort-by FIELD]

--sort-by         Sort by: priority (default) | impact | last-updated | id
```

Default output (table):

```
$ otaman outcome list --status Considering

  ID                                 PRIORITY  IMPACT  ESTIMATE   PERSONA      CATEGORY
  JTBD-1-create-account-with-email   P0        L       requested  end-user     Onboarding
  JTBD-3-invite-colleagues           P1        M       requested  tenant-admin Onboarding
  JTBD-15-upgrade-paid-plan          P2        L       requested  tenant-admin Billing

  Summary: 3 outcomes (3 Considering, 0 Done)
```

`--json` returns array of outcome dicts:

```json
[
  {
    "id": "JTBD-1-create-account-with-email",
    "status": "Considering",
    "as-a": "end-user",
    "priority": "P0",
    "impact": "L",
    "estimate-requested": true
  }
]
```

---

### `outcome show` — full detail

```
otaman outcome show <id> [--with-solutions] [--with-tasks]
```

```
$ otaman outcome show JTBD-1-create-account-with-email --with-solutions

  Outcome: JTBD-1-create-account-with-email
  ──────────────────────────────────────────────────────────────────────
  Status:     Considering        (since 2026-05-15 by cpo-agent)
  Category:   Onboarding
  Priority:   P0
  Impact:     L                  (value-rate: L / effort)
  Persona:    end-user

  JTBD statement
  ──────────────────────────────────────────────────────────────────────
  As a       end-user
  I want to  Create an account with my email
  So I can   Start using TaskFlow without invitation

  Product notes (CPO)
  ──────────────────────────────────────────────────────────────────────
  Needs to handle email verification + recovery flow. See competitor analysis
  in docs/research/competitors.md.

  Handoff
  ──────────────────────────────────────────────────────────────────────
  Estimate-requested:  Yes  (since 2026-05-17)
  Chosen-solution:     —
  Cost-accepted:       Pending

  Solutions                          [--with-solutions]
  ──────────────────────────────────────────────────────────────────────
  SOL-1-bcrypt-email-verify   Considering   Medium  10 days  release: MVP
  SOL-2-magic-link-only       Considering   Small    3 days  release: MVP
  SOL-3-oauth-google          Considering   Small    3 days  release: post-MVP

  Auto-triage recommendation: SOL-2-magic-link-only (value-rate: 5/3 = 1.67)
                              tiebreaker (same value-rate): SOL-3 (P2 < SOL-2 P2 — equal)
```

`--with-tasks` adds a section listing tasks annotated `@solution:<chosen-id>`
(see `parser-extension-design-note.md`).

---

### `outcome history` — audit trail

```
otaman outcome history <id>
```

```
$ otaman outcome history JTBD-1-create-account-with-email

  History: JTBD-1-create-account-with-email
  ──────────────────────────────────────────────────────────────────────
  2026-05-15 10:00  added            roman (cpo)     status: Drafting
  2026-05-16 14:30  edited           roman (cpo)     priority P1 → P0
  2026-05-17 09:00  promoted         roman (cpo)     Drafting → Considering
  2026-05-17 09:01  request-estimate roman (cpo)     estimate-requested: Yes
  2026-05-18 11:00  estimate-arrived bridge-agent    3 solutions proposed by CTO
```

Source: the `transitions: []` array embedded on each outcome. The git history
is the secondary source of truth (per design.md); bus messages are the
tertiary signal.

---

### `outcome promote` / `demote` — status transitions

```
outcome promote <id>       # Drafting → Considering
outcome demote <id>        # Considering → Drafting
```

Records a transition entry and emits a one-line confirmation. Idempotent if
the outcome is already in the target status (warns but exits 0).

`promote` validates required fields before transition:
- All three JTBD sub-fields present + non-empty
- `as-a` resolves to a persona in `personas.yaml`
- `priority` set (not null)

If validation fails, prints fixes and exits 1.

---

### `outcome request-estimate` — CPO → CTO handoff

```
otaman outcome request-estimate <id>
```

```
$ otaman outcome request-estimate JTBD-1-create-account-with-email

[+] Marked outcome.estimate-requested: Yes
    Outcome: JTBD-1-create-account-with-email
    Bus signal: outcome-estimate-requested → cto-advisor
```

Behavior:
- Sets `estimate-requested: Yes`
- Emits an `outcome-estimate-requested` bus message to `cto-agent` (or to all
  agents with `role: cto` per `platform.yaml.role-assignments`)
- Records a transition entry
- Idempotent (already requested → exit 0 with a note)

---

### `outcome accept-cost` — CEO accepts a solution's cost

```
otaman outcome accept-cost <id> --solution SOL_ID [--sprint TEXT]
```

```
$ otaman outcome accept-cost JTBD-1-create-account-with-email \
    --solution SOL-2-magic-link-only \
    --sprint "Sprint 1"

[+] Accepted cost for SOL-2-magic-link-only
    Outcome: JTBD-1-create-account-with-email
    Solution promoted: Considering → Complete
    Sprint: Sprint 1
    Bus signal: outcome-cost-accepted → bridge-agent (begin task generation)
```

Behavior:
- Validates `--solution` references an existing solution belonging to this outcome
- Sets `chosen-solution-id`, `cost-accepted: Yes`, optional `sprint`
- Promotes the solution's status to `Complete` (cross-registry write — see solution doc)
- Emits an `outcome-cost-accepted` bus message
- Records transition entries on both outcome and solution

---

### `outcome reject-cost` — CEO rejects all proposed costs

```
otaman outcome reject-cost <id> [--reason TEXT]
```

```
$ otaman outcome reject-cost JTBD-1-create-account-with-email \
    --reason "All options exceed Q3 budget; need a smaller approach"

[!] Rejected cost for JTBD-1-create-account-with-email
    Reason: All options exceed Q3 budget; need a smaller approach
    Bus signal: outcome-cost-rejected → cto-agent
```

Behavior:
- Sets `cost-accepted: No`; records reason in transitions
- Emits an `outcome-cost-rejected` bus message to `cto-agent`
- Does NOT auto-create a "propose-cheaper-solution" task (per design.md Q5)
- Records transition entry

---

### `outcome retire` — move to Discard

```
otaman outcome retire <id> [--reason TEXT]
```

Sets `status: Discard`. The outcome is preserved (not deleted) for audit.
`list` excludes Discard by default; `list --status Discard` shows them.

---

## In-memory data model (spike skeleton)

```python
@dataclass(frozen=True)
class JTBDStatement:
    as_a: str               # persona id
    i_want_to: str
    so_i_can: str

@dataclass(frozen=True)
class Transition:
    at: str                 # ISO-8601
    by: str                 # agent / human id
    from_state: str
    to_state: str
    reason: str | None = None

@dataclass(frozen=True)
class Outcome:
    id: str
    category: str
    statement: JTBDStatement
    status: str             # Drafting | Considering | Done | Discard
    priority: str           # P0 | P1 | P2 | P3
    impact: str             # XS | S | M | L | XL
    estimate_requested: bool
    product_notes: str = ""
    tech_notes: str = ""
    chosen_solution_id: str | None = None
    cost_accepted: str = "Pending"   # Yes | No | Pending
    sprint: str = ""
    deploy_status: str = ""
    last_updated: str = ""
    created_at: str = ""
    created_by: str = ""
    transitions: tuple[Transition, ...] = ()

class OutcomeRegistry:
    """In-memory; persistence + role-gating deferred to impl change."""
    def __init__(self) -> None:
        self._outcomes: dict[str, Outcome] = {}
    def add(self, outcome: Outcome) -> None: ...
    def get(self, outcome_id: str) -> Outcome | None: ...
    def list(self, **filters) -> list[Outcome]: ...
    def transition(self, outcome_id: str, **field_updates) -> Outcome: ...
```

---

## Argparse wiring sketch

The `outcome` parser follows the same nested-subparser pattern as `route` and
`vocab`:

```python
def _add_outcome_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("outcome", help="program outcome registry (JTBD)")
    op = p.add_subparsers(dest="outcome_cmd", required=True)

    pa = op.add_parser("add", help="author a new outcome")
    pa.add_argument("id")
    pa.add_argument("--as-a", required=True, metavar="PERSONA_ID")
    pa.add_argument("--i-want-to", required=True, metavar="TEXT")
    pa.add_argument("--so-i-can", required=True, metavar="TEXT")
    pa.add_argument("--category", default="")
    pa.add_argument("--impact", choices=["XS","S","M","L","XL"], default="M")
    pa.add_argument("--priority", choices=["P0","P1","P2","P3"], default="P2")
    pa.add_argument("--product-notes", default="")
    pa.set_defaults(func=cmd_outcome_add)
    # list / show / promote / demote / request-estimate / accept-cost /
    # reject-cost / retire / history follow the same pattern
```

---

## Integration with auto-triage

The auto-triage logic (design.md Q3-bis) is a pure-Python function in
`otaman_router`-style: deterministic, no LLM. Used by `outcome show
--with-solutions` to surface the recommended solution:

```python
def value_rate(outcome: Outcome, solution: Solution, weights: dict[str, float]) -> float:
    impact_weight = weights.get(outcome.impact, 1.0)
    if solution.effort_days <= 0:
        return float("inf")
    return impact_weight / solution.effort_days

def recommend(outcome: Outcome, solutions: list[Solution], weights: dict[str, float]) -> Solution:
    sorted_solutions = sorted(
        solutions,
        key=lambda s: (
            -value_rate(outcome, s, weights),
            outcome.priority,        # tiebreaker
        ),
    )
    return sorted_solutions[0]
```

Weights default from `platform.yaml.program.processes.outcomes.triage.impact-weights`
with the canonical fallback `{XS:1, S:2, M:3, L:5, XL:8}`.

---

## Role-aware behavior (deferred to impl change)

In v0 (this spike), all subcommands work for any identity. In the impl change,
role-gating wires in as:

- `add`, `promote`, `demote`, `request-estimate`, `retire` → CEO/CPO only
- `accept-cost`, `reject-cost` → CEO only
- `tech-notes` field updates (via a future `outcome edit-tech-notes` or
  inline-flag on `show --tech-notes-from-stdin`) → CTO only

Role resolution: `platform.yaml.role-assignments.<role>` → current
`OTAMAN_AGENT` identity. Mismatched role → warning + exit 1.

---

## CE/EE gating

Outcome management is a foundational program-level capability — **available on
all editions**. Auto-triage is open-source; no EE gating proposed in v0.
Possible future EE: enterprise audit-trail features (e.g., approver
signatures, immutable audit log to S3). Not in v0 scope.

---

## Open questions for implementation phase

1. **Multi-CPO programs**: if two humans hold the CPO role, who is the
   authoritative editor? Recommend: any CPO can edit; transition records the
   specific human id; bus messages CC all CPOs.
2. **Outcome deletion**: should `Discard` be the terminal state, or do we
   allow hard-delete after a grace period? Recommend: terminal; never
   hard-delete (audit trail forever).
3. **Sprint field type**: free-form text (`"Sprint 1"`) or structured
   reference to a future sprints registry? Recommend: free-form v1; sprints
   registry is a future proposal.
4. **`estimate-arrived` signal**: who emits the bus message when CTO has
   proposed solutions? Recommend: `cto-agent` emits on first `solution add`
   for that outcome; bridge-agent maintains a watermark.

These are NOT blockers — they're follow-on design decisions.

---

## Cross-references

- `cli-solution-subcommand.md` — solution surface (companion document)
- `cli-persona-subcommand.md` — persona surface (referenced by `as-a`)
- `parser-extension-design-note.md` — `@solution:<id>` annotation parser
