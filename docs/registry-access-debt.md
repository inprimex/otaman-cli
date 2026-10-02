# Registry access debt — the survivors of the contract chokepoint

**Change:** `registry-access-contract` task 1.2 (otaman-cli) · **Author:** cli-agent · 2026-10-02

Every registry **write** in otaman-cli now goes through `otaman_core.registry_access`,
reached through this repo's single door `otaman_cli.registries.access`. The capability
spec calls a direct registry-file access outside the contract a *conformance defect*, so
the files below are **debt, not design** — each one is a place that still opens a
registry file itself, with the reason it has not moved and what would move it.

`tests/registries/test_registry_access_chokepoint.py` parses the **Allowed survivors**
list below and fails on any file that opens a registry file and is not listed — and on
any listed file that no longer does. The guard and this register cannot drift: adding a
survivor means writing its justification here.

## What moved (rac 1.2)

| Site | Before | Now |
| --- | --- | --- |
| `registries/cli_outcome.py` | `yaml_load`/`yaml_dump` + in-place status writes | `load_register` / `apply_transition` / `create_record` / `save_register`, approval-bearing for `accept-cost`/`choose`/`reject-cost` |
| `registries/cli_solution.py` | same pair, `append_transition` | same contract calls; the parent-outcome and triage reads go through the contract too |
| `registries/cli_persona.py` | same pair | contract load/save (see the one documented field write below) |
| `registries/assign_annotations.py` | `yaml_load(solutions.yaml)` | `open_register`; no contract → the annotation is reported *unvalidated* rather than read around the door |
| `commands/doctor.py` (verification lint, outcome↔solution linkage) | `yaml_load` ×3 | `load_register` |

## Allowed survivors

- `src/otaman_cli/registries/loader.py` — the path resolver and this repo's raw YAML
  verbs. It resolves `outcomes`/`solutions`/`personas` paths (the contract takes a path,
  so something must produce one) and still serves non-register YAML. The verbs it
  exports are what the guard polices everywhere else; removing them is rac 1.3+ work,
  after the contract grows the read affordances (`open_register`'s absent-file case and
  schema validation) that callers still come here for.
- `src/otaman_cli/registries/outcomes.py` — `load_outcomes`, the strict pydantic
  display loader over `yaml_fast.load_file`. Read-only: it cannot corrupt a register,
  and it is the loader the console and `doctor`'s "present-but-unloadable" probe
  deliberately exercise — that check exists to fail loudly when the *console's* loader
  would fall back, so routing it through the contract would stop it testing the thing it
  is about.
- `src/otaman_cli/registries/solutions.py` — `load_solutions`, same shape, same reason.
- `src/otaman_cli/registries/personas.py` — `load_personas`, same shape, same reason.
- `src/otaman_cli/console/home.py` — the Home panel's outcome/solution counts via the
  memoised `yaml_read`. **Measured:** the contract's `load_register` is a ruamel
  round-trip at **688–803 ms** on the live register against **~0.1 ms** memoised; Home
  alone takes ~6 reads against a 500 ms frame budget, so the contract would cost ~4 s per
  paint. Read-only, so the write chokepoint is intact. Moves when the contract grows a
  cached/fast read (flagged to core).
- `src/otaman_cli/console/registry_detail.py` — the detail pane's `yaml_read`, same
  measurement, same reason.
- `src/otaman_cli/console/extra_registries.py` — the console's rows for the *extra*
  registers a program configures (flows, risks, vocabulary). Read-only display, and the
  cli has no write verb for these at all — their authors are cofounder-agent and the web
  client, so the write chokepoint this change installs does not pass through here.
  Measured with the same `load_file` fast read as the strict loaders.

## Known, outside the guard's reach

- **`src/otaman_cli/onboard/templates/business/{outcomes,solutions,personas}.yaml`** +
  `onboard/scaffold_ce.py` — scaffolding *creates* an empty register from a template
  whose text carries the schema header comments for the human who opens it next. No
  record is read or written, and `save_register` cannot author those comments from a
  dict. Intentionally left as a template copy.
- **`src/otaman_cli/llm_routes.py`** — `yaml_load`/`yaml_dump` on `llm-routes.yaml`,
  which is configuration, not a register: no `id`-keyed records, no `transitions[]`.
  Out of scope for this contract.
- **`registries/cli_persona.py:retire`** — the one field write in a rewired module that
  is not `apply_transition`. The persona schema is `extra="forbid"` with no
  `transitions[]`, and the contract's field writer always appends an audit entry, which
  this register cannot hold without failing its own validator. The file is still opened
  and written only by the contract. Reported to core and spec-agent: either personas
  grow an audit trail, or the contract grows a transitionless field write. Inventing a
  schema field is not this surface's call.

## Reported to core (contract gaps found while rewiring)

1. `load_register` raises `FileNotFoundError` for a register that does not exist yet —
   the program whose first `add` creates it. Worked around with `access.open_register`.
2. `apply_transition` records the `field`/`old`/`new` triple only for a **single**-field
   transition, so `accept-cost` (3 fields) carries no triple and `choose` needs a second
   `update-field` row to bump `updated`.
3. `old` is set unconditionally, so a first `choose` records `old: None`.
4. Appendix A.5 needs the `approval` field the contract writes (rac 1.3).
5. The contract's read is ~700 ms, so it cannot serve display paths (see above).
