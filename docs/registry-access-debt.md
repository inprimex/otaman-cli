# Registry access debt — the survivors of the contract chokepoint

**Change:** `registry-access-contract` tasks 1.2 + 2.1 (otaman-cli) · **Author:** cli-agent
**Written** 2026-10-02 · **Debt paid down the same day** against core #116

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

Two, down from six — core #116 shipped the read affordances the measurements below
asked for, so every display path now reads THROUGH the contract (see **Paid**).

- `src/otaman_cli/registries/loader.py` — the register path resolver, and the file
  that DEFINES this repo's raw YAML verbs (`yaml_load` / `yaml_dump`, still used for
  non-register YAML like `llm-routes.yaml`). The guard cannot tell a definition from
  a call, and it should not have to: this is the module whose verbs it polices
  everywhere else. `yaml_read` is gone — the display reader it existed for is the
  contract's now.
- `src/otaman_cli/console/extra_registries.py` — the console's rows for the *extra*
  registers a program may configure (flows, risks, vocabulary), whose schemas are
  spec-agent's to define and are not yet written. It reads a file of UNKNOWN shape:
  a top-level list, or a mapping whose first list-of-mappings value is the entries.
  The contract's reader takes a `records_key` and normalises anything else to
  `{key: []}`, so routing this through it would silently drop a top-level list. The
  cli has no write verb for these registers at all — their authors are
  cofounder-agent and the web client — so the write chokepoint does not pass here.
  Moves when those schemas land (rac 1.3+ or the registers' own change).

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

## Paid (core #116, same day)

core shipped three of the five findings within hours of the report, and the debt they
justified is gone rather than merely re-measured:

- **`load_register(..., missing_ok=True)`** (finding 1) — the create-fresh case is the
  contract's answer now; `access.open_register` consumes it and the local `Register`
  construction is gone.
- **`read_register_fast`** (finding 5) — the lossy display read, with the result marked
  `read_only` so `save_register` REFUSES it. That is stronger than the reader it
  replaced: `yaml_read`'s only defence against writing a comment-stripped register back
  was a docstring saying "NEVER".
- **`old` omitted when the field was absent** (finding 3) — `"old" in entry` now means
  "there was a previous value" instead of recording `old: None` for a first choice.

What stays on cli's side is the MEMOISATION, and the measurement says why:

| read of the live 251KB register | cost |
| --- | --- |
| `load_register` (ruamel round-trip) | 666 ms |
| `read_register_fast` (core, C-safe loader) | 34 ms |
| `access.read_fast` (core's reader, memoised) warm | 0.03 ms |
| Home's six reads, unmemoised | 159 ms, against a 500 ms frame budget |

So `access.read_fast` is core's reader with cli's cache in front of it, keyed on
(path, mtime, size) so a human's edit re-parses, and cleared by the console's refresh
alongside the other two caches. The display paths that were survivors —
`console/home.py`, `console/registry_detail.py`, and the three validating loaders in
`registries/{outcomes,solutions,personas}.py` — are no longer in the list above.

The loaders read with `strict=True`: a present-but-unloadable register must RAISE,
because the console's loud fallback notice and doctor's unloadable-register check are
both read off that failure. A panel reads with the default and renders empty.

## Still open with core

1. `apply_transition` records the `field`/`old`/`new` triple only for a **single**-field
   transition, so `accept-cost` (3 fields) carries no triple and `choose` needs a second
   `update-field` row to bump `updated`. core proposes `transition.changes` (a list of
   per-field triples); confirmed, awaiting the emit.
2. Appendix A.5 needs the `approval` field the contract writes, with an optional
   structured `hat` — rac 2.1's founder-mode log carries the hat as prose in `note`
   today because `via: hat` says a hat was used and not which. spec-agent authors it.
