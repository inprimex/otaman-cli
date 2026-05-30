# `otaman vocab` — CLI Design Research (task 3.1)

**Author**: cli-agent
**Date**: 2026-05-30
**Change**: program-vocabulary-registry
**Validates**: design.md Q6 (CLI surface)
**Implementation status**: skeleton-only (in-memory dict; persistence deferred to follow-on impl change)

---

## Purpose

`otaman vocab <subcommand>` is the operator + agent surface for the per-program
vocabulary registry stored at `<otaman-business>/vocabulary.yaml`. It covers
the full lifecycle: add → list/show/search → deprecate → lint → pull domain-pack
updates → audit history → export.

This research doc validates the design.md Q6 surface by sketching argparse
wiring, command behaviour, output formats, and the in-memory data model used
by the spike.

---

## Subcommand surface (per design.md Q6)

```
otaman vocab <subcommand> [options]

Subcommands:
  add <id> --canonical TEXT --definition TEXT [options]
                          Add a new term to the registry
  list [--domain D] [--status S]
                          List terms, optionally filtered
  show <id> [--with-references]
                          Show full detail for a term
  search <query>          Fuzzy search canonical + synonyms + definitions
  deprecate <id> [--replaced-by ID] [--reason TEXT]
                          Mark a term deprecated; optional successor pointer
  lint [<file-or-dir>]    Scan docs for undefined / synonym / unused terms
  pull-updates [--domain D] [--dry-run]
                          Refresh prefill packs from otaman-meta/vocabulary-packs/
  history <id>            Show edit history + transitions for a term
  export --format <json|csv|markdown>
                          Emit the registry in a portable format
```

---

## Common flags (all subcommands)

```
--registry PATH         Override vocabulary.yaml path (default: from platform.yaml)
--json                  Emit JSON output for machine consumption
--quiet                 Suppress informational output (errors still printed)
```

The `--registry` flag is for testing + scripts; the canonical path resolves via
`platform.yaml` → `program.processes.vocabulary.registry-path` (default
`<otaman-business>/vocabulary.yaml`).

---

## Detailed surface

### `vocab add` — register a new term

```
otaman vocab add <id> --canonical TEXT --definition TEXT [options]

Required:
  <id>                    Term id (kebab-case slug; vocab- prefix recommended)
  --canonical TEXT        Human-readable canonical form
  --definition TEXT       Short definition (1-3 sentences)

Optional:
  --domain D              Domain tag (repeatable). Default: program's primary domain.
  --synonym TEXT          Synonym (repeatable)
  --example TEXT          Example usage (repeatable)
  --reference REF         Reference to another artifact (repeatable; e.g. outcome:JTBD-3)
  --status S              Initial status (proposed|active). Default: proposed.
  --source S              Source tag. Default: custom.
```

Behavior:
- Validates `<id>` matches `^[a-z][a-z0-9-]*$`; rejects duplicates
- Validates `domain` against known domain list (warning if custom — see design.md Q4)
- Writes term in canonical YAML order; preserves comments + other terms
- Emits `[+] Added term: <id> (<canonical>) [proposed]` on success

Example:
```
$ otaman vocab add vocab-tenant \
    --canonical "Tenant" \
    --definition "A customer organization with isolated workspace + data scope." \
    --domain software-saas \
    --synonym Organization \
    --synonym "Workspace owner" \
    --status active

[+] Added term: vocab-tenant (Tenant) [active]
    domain: software-saas
    synonyms: Organization, Workspace owner
```

---

### `vocab list` — enumerate terms

```
otaman vocab list [--domain D] [--status S]
```

Default output is a 3-column table sorted by canonical form:

```
$ otaman vocab list --domain software-saas

  ID                   CANONICAL          STATUS      SYNONYMS
  vocab-feature-flag   Feature flag       active      flag, toggle
  vocab-seat           Seat               active      license
  vocab-tenant         Tenant             active      Organization, Workspace owner
  vocab-workspace      Workspace          deprecated  (replaced-by: vocab-tenant)

  Summary: 4 terms (3 active, 1 deprecated)
```

`--status proposed` filters to terms awaiting review (common after bulk-import).

`--json` returns a list of term dicts:

```json
[
  {"id": "vocab-tenant", "canonical": "Tenant", "status": "active",
   "domain": "software-saas", "synonyms": ["Organization", "Workspace owner"]}
]
```

---

### `vocab show` — full detail

```
otaman vocab show <id> [--with-references]
```

```
$ otaman vocab show vocab-tenant --with-references

  Term: vocab-tenant
  ──────────────────────────────────────────────────────────────
  Canonical:    Tenant
  Definition:   A customer organization with isolated workspace + data scope.
  Domain:       software-saas
  Synonyms:     Organization, Workspace owner, Account
  Status:       active  (since 2026-05-15)
  Source:       platform-shipped/software-saas

  Examples:
    - A new tenant signs up via the onboarding flow.

  References:
    outcome:JTBD-3-invite-colleagues
    persona:platform-administrator

  Referenced by:                      [--with-references]
    outcome:JTBD-15-upgrade-paid-plan (3 mentions)
    solution:multi-tenant-isolation   (2 mentions)
    spec:tenancy-boundary-rules       (1 mention)
```

`--with-references` runs a reverse-index scan over sibling registries (slower).

---

### `vocab search` — fuzzy lookup

```
otaman vocab search <query>
```

Matches against canonical, synonyms, and definition substrings. Ranks by:
1. Exact canonical match
2. Exact synonym match
3. Substring in canonical
4. Substring in synonyms
5. Substring in definition

```
$ otaman vocab search "workspace"

  vocab-tenant         Tenant             synonym match: "Workspace owner"
  vocab-workspace      Workspace          [deprecated → vocab-tenant]

  2 matches.
```

v1 uses Python `difflib.SequenceMatcher` for fuzzy ranking (no extra deps).
v1.5+ may add vector-embedding semantic search (deferred per design.md).

---

### `vocab deprecate` — mark deprecated

```
otaman vocab deprecate <id> [--replaced-by ID] [--reason TEXT]
```

```
$ otaman vocab deprecate vocab-workspace \
    --replaced-by vocab-tenant \
    --reason "Consolidated terminology after Q2 product rename"

[+] Deprecated: vocab-workspace
    Replaced by: vocab-tenant
    History entry recorded.
```

Behaviour:
- Term remains in registry (preserved for audit; see design.md Q5)
- `lint` issues a `[warn] deprecated-term-used` for documents still using it
- Appends an entry to the term's history with timestamp + agent + reason
- If `--replaced-by` points to a non-existent id, prompts before continuing

---

### `vocab lint` — scan docs for term issues

```
otaman vocab lint [<file-or-dir>]
                  [--terms-only]
                  [--strict]
                  [--ignore-pattern PATTERN]
```

Scans markdown files for term mentions. See **`cli-vocab-lint-prototype.md`** for the
full lint algorithm + sample output. CLI surface:

| Flag | Effect |
|---|---|
| `--terms-only` | Only emit term-level findings; skip prose-style warnings |
| `--strict` | Treat synonym mentions as errors (default: advisory) |
| `--ignore-pattern P` | Skip files matching the glob (repeatable) |

Exit codes:
- `0` — no findings
- `1` — advisory findings only (synonyms, deprecated, unused)
- `2` — errors (undefined terms when `--strict`, malformed registry)

Default scope when no path given: scan the `otaman-business/` directory (per design.md
focus on business-layer artifacts; spec/docs are advisory).

---

### `vocab pull-updates` — refresh prefill packs

```
otaman vocab pull-updates [--domain D] [--dry-run]
```

Per design.md Q2 (hybrid: snapshot + explicit pull):

1. Locate `otaman-meta/vocabulary-packs/<domain>.yaml` for each declared domain
2. Diff against current registry (additions / definition changes / new synonyms)
3. With `--dry-run`: print diff and exit
4. Without: apply additions; mark renamed terms as deprecated → new id; record history

```
$ otaman vocab pull-updates --domain fintech --dry-run

  Pack: fintech v1.2 (current: v1.0)
  ──────────────────────────────────────────────────
  + vocab-clearing-house  (new)        "Clearing house"
  + vocab-rtgs            (new)        "Real-time gross settlement"
  ~ vocab-counterparty    (definition) "...other party to a transaction..."
                                       → "...counterparty risk-bearing entity..."

  Would apply: 2 additions, 1 definition update. Run without --dry-run to commit.
```

Custom-program terms (`source: custom`) are NEVER touched by pull-updates.

---

### `vocab history` — audit trail

```
otaman vocab history <id>
```

```
$ otaman vocab history vocab-tenant

  vocab-tenant — History
  ──────────────────────────────────────────────────
  2026-05-15  added            roman           initial entry from software-saas pack v1.0
  2026-05-20  edited           cpo-agent       definition refined; added "data scope"
  2026-05-22  synonym-added    cpo-agent       added "Workspace owner"
  2026-05-25  reference-added  spec-agent      outcome:JTBD-3-invite-colleagues
```

History stored either inline (terms grow a `history:` field) or in a sibling
`vocabulary-history.yaml` — final placement deferred to implementation phase
(call out in tasks.md for follow-on change).

---

### `vocab export` — portable formats

```
otaman vocab export --format <json|csv|markdown>
```

- **json**: flat array of term objects (same shape as `--json` on list)
- **csv**: header `id,canonical,definition,domain,synonyms,status` (synonyms `|`-joined)
- **markdown**: glossary-style — one heading per canonical, sorted alphabetically

Use cases: feed vocabulary into external knowledge bases, wiki pages, downstream
LLM prompt context (per design.md "deferred" v1.5+ enhancement).

---

## In-memory data model (spike skeleton)

The skeleton stores the registry as a Python dict keyed by `id`:

```python
@dataclass(frozen=True)
class Term:
    id: str
    canonical: str
    definition: str
    domain: tuple[str, ...]       # one or more
    synonyms: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    status: str = "proposed"      # proposed | active | deprecated
    source: str = "custom"
    replaced_by: str | None = None  # for deprecated terms
    created_at: str = ""          # ISO-8601
    created_by: str = ""

class VocabRegistry:
    """In-memory registry; persistence is a separate concern (impl change)."""
    def __init__(self) -> None:
        self._terms: dict[str, Term] = {}
        self._canonical_index: dict[str, str] = {}   # canonical-lower → id
        self._synonym_index: dict[str, str] = {}     # synonym-lower → id

    def add(self, term: Term) -> None: ...
    def get(self, term_id: str) -> Term | None: ...
    def list(self, domain: str | None = None, status: str | None = None) -> list[Term]: ...
    def search(self, query: str, limit: int = 10) -> list[tuple[Term, float]]: ...
    def deprecate(self, term_id: str, replaced_by: str | None, reason: str) -> None: ...
    def resolve_mention(self, text: str) -> tuple[Term | None, str]:
        """Return (term, match_kind) for a candidate mention.
        match_kind ∈ {'canonical', 'synonym', None}."""
        ...
```

`resolve_mention()` is the lint primitive: given a token from a markdown doc,
identify which term it matches (and as canonical vs. synonym) or `None` for
undefined.

---

## Argparse wiring sketch

```python
def _add_vocab_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("vocab", help="program vocabulary registry")
    vp = p.add_subparsers(dest="vocab_cmd", required=True)

    # add
    pa = vp.add_parser("add", help="register a new term")
    pa.add_argument("id")
    pa.add_argument("--canonical", required=True)
    pa.add_argument("--definition", required=True)
    pa.add_argument("--domain", action="append", default=[])
    pa.add_argument("--synonym", action="append", default=[])
    pa.add_argument("--example", action="append", default=[])
    pa.add_argument("--reference", action="append", default=[])
    pa.add_argument("--status", choices=["proposed", "active"], default="proposed")
    pa.add_argument("--source", default="custom")
    pa.set_defaults(func=cmd_vocab_add)

    # list / show / search / deprecate / lint / pull-updates / history / export
    # follow the same pattern; see cli-vocab-lint-prototype.md for lint specifics
    ...
```

The `vocab` parser nests under the top-level `otaman` parser via the existing
`add_subparsers` pattern (see `route` precedent in `otaman-router-v1-design`).

---

## Integration with `otaman init`

When a program enables vocabulary in `platform.yaml`:

```yaml
program:
  processes:
    vocabulary:
      enabled: true
      domains: [software-saas, fintech]
      pack-version: "1.0"
```

`otaman init` (post-`generate-agent-config.py`):
1. Detect `vocabulary.enabled: true`
2. Create `<otaman-business>/vocabulary.yaml` if absent
3. Merge prefill packs from `otaman-meta/vocabulary-packs/<domain>.yaml` for each declared domain
4. Skip if registry already exists (idempotent — use `pull-updates` for refresh)

This is the only `init`-time interaction; all other operations are explicit
`otaman vocab` invocations.

---

## CE/EE gating

`otaman vocab` is available on **all editions** — vocabulary is a foundational
authoring capability, not a billable feature. The `lint` subcommand's
NLP-based fuzzy matching (deferred to v1.5+) would be the natural EE boundary
if monetisation is needed; default regex matching stays CE.

---

## Open questions for implementation phase

1. **Persistence layer**: write-through to `vocabulary.yaml` on every `add` /
   `deprecate`, or batch on explicit `save`? Recommend write-through for simplicity;
   YAML write is <1ms for ≤500 terms.
2. **History storage**: inline `history:` field on each term, or sibling
   `vocabulary-history.yaml`? Inline is simpler but bloats the main registry.
   Recommend sibling file for clean diffs.
3. **Concurrent edits**: two agents running `vocab add` simultaneously could race.
   Mitigation: file-lock around write (use `portalocker` or `fcntl`).
4. **`pull-updates` conflict resolution**: if a program has overridden a
   prefilled term, do pack updates override their override? Recommend: no —
   program overrides win; pack updates flagged as advisory in the diff.
5. **`history` field reverse-index**: building "referenced by" requires scanning
   all sibling registries on every `show --with-references`. Cache?
   Recommend: build on demand for v1 (≤500 terms × ≤5 sibling files = trivial).

These are NOT blockers for the spike — they're follow-on design questions for
the implementation change.

---

## Implementation note for follow-on change

The spike skeleton lives in `src/otaman_cli/vocab/` once implementation begins:

```
src/otaman_cli/vocab/
  __init__.py
  registry.py       # VocabRegistry + Term dataclass; in-memory + YAML persistence
  loader.py         # platform.yaml → registry path resolution; pack merge
  lint.py           # see cli-vocab-lint-prototype.md
  cli.py            # cmd_vocab_* dispatchers; argparse wiring
  format.py         # table / json / csv / markdown output formatters
```

The `route/` module from `otaman-router-v1-design` is the precedent template.
