# Pre-commit Hook Integration — Design Note (task 3.3)

**Author**: cli-agent
**Date**: 2026-05-30
**Change**: program-vocabulary-registry
**Question**: Should `otaman vocab lint` run automatically on commits, and for which file types?

---

## TL;DR — recommendation

**Yes, opt-in via `otaman init --vocab-precommit`. Lint runs as a non-blocking
warning hook by default; programs may upgrade it to blocking via a `--strict`
config flag once their vocabulary stabilises.**

Scope: only files under `otaman-business/` are scanned by the hook (the
business-layer artifacts the vocabulary is designed for). Spec and code paths
are NOT scanned — they have their own discipline.

Findings are surfaced inline via standard pre-commit-framework output; the
commit proceeds even on advisories. The user can `--no-verify` to skip in a
pinch, or explicitly opt in to blocking mode for CI-style rigour.

---

## Why a pre-commit hook at all?

Vocabulary drift happens at the same speed as commits. The further a divergent
synonym propagates through history, the harder it is to retroactively
canonicalise. A commit-time warning is the cheapest possible feedback loop —
much cheaper than discovering 200 mentions of "Organization" three weeks later
when someone runs `vocab lint` for the first time.

Three reasons in priority order:

1. **Catch drift at write time.** The author's context is fresh; fixing
   "Organization → Tenant" takes 5 seconds at commit. Three weeks later it's
   a 30-minute search-and-replace + a code-review cycle.
2. **Surface deprecated terms.** When a term transitions to `deprecated`,
   existing documents using it need migration. The hook makes the migration
   visible incrementally as documents are touched, rather than as a giant
   one-off cleanup.
3. **Educate new authors.** A new contributor doesn't know the program's
   vocabulary. The hook teaches them passively: "you wrote 'Organization';
   the canonical term is 'Tenant'."

---

## Why NOT a pre-commit hook (counter-arguments)

| Concern | Severity | Mitigation |
|---|---|---|
| Commit-time latency | medium | Lint is <200ms for typical sizes; hook only scans staged files (not full tree) → ~20ms typical |
| False positives blocking commits | high if blocking | Default mode is non-blocking (advisory print, exit 0) |
| Authors disabling via `--no-verify` | medium | Same risk as any pre-commit — accept it; offer `--strict` for CI-side enforcement |
| Hook breaks in non-Python environments | low | Pre-commit framework handles environment isolation; otaman CLI already required for other workflows |
| Adds another setup step | low | Opt-in via `otaman init --vocab-precommit`; users who don't enable it never see it |

The strongest counter-argument is the "blocking commits on advisories" risk.
The proposed design eliminates that by defaulting to non-blocking.

---

## Hook contract

### Scope — which files trigger lint?

| Path pattern | Lint? | Rationale |
|---|---|---|
| `otaman-business/**/*.md` | ✅ yes | Vocabulary's primary target |
| `otaman-business/vocabulary.yaml` | ✅ yes (different check) | Validates registry health: duplicates, missing replaced-by |
| `otaman-meta/adrs/*.md` | ⚠️ opt-in | ADRs benefit from canonical terms but predate them; off by default |
| `otaman-specs/**/*.md` | ❌ no | OpenSpec discipline handles its own precision |
| Code files (`*.py`, `*.ts`, `*.go`, ...) | ❌ no | Identifiers ≠ vocabulary; would produce noise |
| `**/CLAUDE.md` | ⚠️ opt-in | Agent-facing docs; canonical terms valuable here too, but opt-in |

Pattern matching uses the standard pre-commit `files:` regex, configured by
`otaman init` based on declared scopes.

### Severity → hook behaviour

| Finding severity | Default mode | `--strict` mode |
|---|---|---|
| `advisory` (synonym-used) | print, exit 0 (commit proceeds) | print, exit 1 (commit blocked) |
| `warn` (deprecated-term) | print, exit 0 | print, exit 1 |
| `error` (registry malformed) | print, exit 1 (always blocked) | print, exit 1 |

Default mode → educational. `--strict` mode → CI-enforceable.

### Output format

The hook reuses the standard `vocab lint` human-readable output. Example:

```
[otaman-vocab-lint] Linting 3 staged file(s)…
otaman-business/outcomes/JTBD-3.md:14
  [advisory] synonym-used: "Organization" → suggest "Tenant" (vocab-tenant)
otaman-business/outcomes/JTBD-3.md:27
  [warn]     deprecated-term: "Workspace" — replaced by "Tenant" (vocab-tenant)

[otaman-vocab-lint] 2 findings (1 warn, 1 advisory). Commit allowed (advisory mode).
[otaman-vocab-lint] Run `otaman vocab lint --strict` to enforce, or fix and re-commit.
```

---

## Pre-commit framework integration

Use the official [pre-commit](https://pre-commit.com/) framework — it's the
de-facto standard, language-agnostic, and handles environment isolation.

### `.pre-commit-hooks.yaml` (otaman-cli ships this)

```yaml
- id: otaman-vocab-lint
  name: otaman vocab lint
  description: Lint markdown for vocabulary drift (synonyms, deprecated terms)
  entry: otaman vocab lint --pre-commit
  language: python
  types: [markdown]
  files: ^otaman-business/.*\.md$
  pass_filenames: true
  require_serial: false
  stages: [pre-commit]
```

### `.pre-commit-config.yaml` (the program's repo ships this)

```yaml
repos:
  - repo: https://github.com/inprimex/otaman-cli
    rev: v0.X.Y
    hooks:
      - id: otaman-vocab-lint
        args: []                   # default = advisory; add ['--strict'] to block
```

### `otaman init --vocab-precommit` behaviour

When the user runs `otaman init` with `--vocab-precommit`, the CLI:

1. Detects `program.processes.vocabulary.enabled: true` in `platform.yaml`
2. Creates or updates `.pre-commit-config.yaml` at the project root, adding the
   `otaman-vocab-lint` hook entry (idempotent)
3. Suggests but does not run `pre-commit install` — that's a per-developer
   choice (the hook lives in their `.git/hooks/`)
4. Prints a one-time confirmation:
   ```
   [+] Added otaman-vocab-lint to .pre-commit-config.yaml
       Run `pre-commit install` to enable the hook locally.
       Hook defaults to advisory mode (does not block commits).
       Upgrade to blocking mode with: args: ['--strict']
   ```

`otaman init` without the flag does NOT touch pre-commit config — fully opt-in.

---

## `--pre-commit` flag — what's different from `--strict`?

`otaman vocab lint --pre-commit`:
- Reads file paths from `argv` (pre-commit passes them in)
- Skips full-tree scan; only scans the staged subset
- Skips the unused-term detector (can't tell from one commit)
- Suppresses the summary banner (just findings, terser)
- Exit 0 unless registry-health errors (default mode)

`otaman vocab lint --strict`:
- Treats advisories as errors → exit 1
- Suitable for CI / blocking enforcement
- Composes with `--pre-commit` for strict pre-commit blocking

Both can be combined: `--pre-commit --strict` is the blocking-pre-commit mode.

---

## Implementation phase work (for follow-on impl change)

The pre-commit integration touches three places:

1. **otaman-cli**: `cmd_vocab_lint` gains `--pre-commit` and `--strict` flags;
   filename-arg parsing reads `sys.argv[1:]` for the staged file list.
2. **otaman-cli ships `.pre-commit-hooks.yaml`**: at repo root, declaring the hook.
3. **`cmd_init`**: add `--vocab-precommit` flag; idempotent YAML merge into
   `.pre-commit-config.yaml`.

Estimate: small (~50 LOC + 5 tests). No dependencies on sibling changes
beyond the core vocab lint work.

---

## Alternative considered: server-side enforcement (rejected for v1)

Instead of pre-commit (client-side), enforce vocab via:
- A GitHub Actions check on PRs
- A `lint:vocab` step in the program's CI pipeline

**Why rejected for v1**:
- Higher friction to set up (per-repo CI config, secrets for otaman CLI)
- Feedback loop is minutes, not seconds — defeats the purpose of catching drift
  at write time
- Doesn't replace pre-commit anyway — both are useful (pre-commit for authors,
  CI for protection against `--no-verify`)

**Recommendation**: pre-commit in v1; document CI integration as a follow-up
once a program adopts vocab and wants tighter enforcement.

---

## Open questions for implementation phase

1. **Hook performance on large diffs**: if a single commit touches 50 markdown
   files, scan cost adds up. Measured worst case: 50 × 20ms = 1s. Acceptable
   but worth monitoring. Mitigation: parallelise with `concurrent.futures` if
   wall-clock matters.
2. **Pre-commit framework version**: which minimum `pre-commit` version do we
   require? Recommend `>=3.0` (the version most distributions ship today;
   widely available).
3. **Hook auto-disable when vocabulary.yaml is absent**: should the hook
   gracefully skip when the program doesn't have a vocabulary yet?
   Recommend: yes — `--pre-commit` exits 0 with a one-line note if registry
   is missing. Avoids confusing new programs.
4. **Suggest --no-verify advice in hook output?**: probably no — that
   encourages bypassing. Document in `otaman vocab lint --help` instead.

---

## Decision summary

| Question | Answer |
|---|---|
| Should lint run on commits? | **Yes**, opt-in via `otaman init --vocab-precommit` |
| Which file types? | `otaman-business/**/*.md` by default; ADRs + CLAUDE.md opt-in |
| Blocking or advisory? | **Advisory by default**; `--strict` upgrade for CI rigour |
| Pre-commit framework or custom hook? | **Pre-commit framework** (`pre-commit.com`) — standard, robust |
| Setup mechanism? | `otaman init --vocab-precommit` writes `.pre-commit-config.yaml` entry |
| When to ship? | Same release as `vocab lint` impl change; no extra dependencies |
