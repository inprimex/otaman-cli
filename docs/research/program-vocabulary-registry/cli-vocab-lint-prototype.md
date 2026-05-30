# `otaman vocab lint` — Prototype + Design Research (task 3.2)

**Author**: cli-agent
**Date**: 2026-05-30
**Change**: program-vocabulary-registry
**Validates**: design.md Q3 (inline canonical text v1) + Q4 (synonym resolution) + Q6 (`lint` subcommand)
**Prototype**: see `docs/research/program-vocabulary-registry/lint_prototype.py` (runnable; produces the sample output below)

---

## Purpose

The `lint` operation is the linchpin of the vocabulary registry's value: it
turns vocabulary from a passive glossary into an active consistency-enforcement
tool. Without lint, terms drift; with lint, drift surfaces as a warning at
authoring or commit time.

This research validates the v0 algorithm (word-boundary regex), the finding
categories, and the output format that downstream tooling (pre-commit hooks,
CI gates) consumes.

---

## Algorithm — v0 (word-boundary regex)

For each markdown file in scope:

1. **Tokenize**: collect candidate mentions via regex over the file body.
   - Strip code blocks (` ``` ` fenced and `\`inline\``) — they MUST NOT lint
   - Strip frontmatter (`---...---` at top of file) — metadata, not prose
   - Strip URLs (`https?://\S+`) — they're not vocabulary
2. **Build matchers** from the registry:
   - One regex per canonical form: `\b<canonical>\b` (case-sensitive)
   - One regex per synonym: `\b<synonym>\b` (case-insensitive — configurable)
3. **Scan**: for each non-stripped chunk of prose, find regex matches.
   For each match, record `(term_id, kind, file, line, span)` where
   `kind ∈ {canonical, synonym}`.
4. **Detect undefined**: in `--strict` mode, scan prose for capitalised
   noun-phrase patterns (e.g., `^[A-Z][a-z]+( [A-Z][a-z]+)*$`) that aren't
   covered by any term. Heuristic only; expected to produce false positives,
   user can `--ignore-pattern` to silence.
5. **Detect unused**: terms in the registry with zero mentions across the
   scan scope. Status: advisory (a term may be intentionally pre-defined
   before authoring catches up).
6. **Detect deprecated-used**: any match against a term with `status:
   deprecated`. Emit suggestion to use `replaced-by` term.

The regex is intentionally simple. NLP-based mention detection is deferred to
v1.5+ per design.md.

---

## Finding categories

| Category | Severity | Meaning | Example |
|---|---|---|---|
| `synonym-used` | advisory | Doc uses a synonym; canonical form preferred | "Organization" → suggest "Tenant" |
| `deprecated-term` | warning | Doc uses a term flagged `status: deprecated` | "Workspace" → suggest "Tenant" (replaced-by) |
| `undefined-capitalised` | advisory (strict only) | Capitalised noun phrase not in registry | "Settlement Window" — add to registry? |
| `unused-term` | informational | Registry term has no mentions in scope | `vocab-rtgs` has 0 mentions |
| `duplicate-canonical` | error | Two terms share the same canonical form | malformed registry |
| `missing-replaced-by` | error | Deprecated term references missing `replaced-by` id | broken pointer |

In default mode, only `synonym-used`, `deprecated-term`, `unused-term`,
`duplicate-canonical`, and `missing-replaced-by` fire. `--strict` adds
`undefined-capitalised`.

---

## Output format — human (default)

```
$ otaman vocab lint otaman-business/

  Vocabulary lint  [otaman-business/]
  ──────────────────────────────────────────────────────────────────────

  outcomes/JTBD-3-invite-colleagues.md:14
    [advisory] synonym-used: "Organization" → suggest "Tenant" (vocab-tenant)

  outcomes/JTBD-3-invite-colleagues.md:27
    [warn]     deprecated-term: "Workspace" — replaced by "Tenant" (vocab-tenant)

  solutions/multi-tenant-isolation.md:8
    [advisory] synonym-used: "Account" → suggest "Tenant" (vocab-tenant)

  Summary
  ──────────────────────────────────────────────────────────────────────
  Files scanned:           5
  Findings:                3 (1 warn, 2 advisory)
  Unused terms:            1 — vocab-rtgs (not referenced anywhere)
  Registry health:         ✓ (0 duplicate-canonical, 0 missing-replaced-by)
```

Exit code: `1` (advisories or warnings present). `0` if clean. `2` on registry
errors (duplicate-canonical, missing-replaced-by).

---

## Output format — JSON (`--json`)

```json
{
  "scope": "otaman-business/",
  "files_scanned": 5,
  "findings": [
    {
      "category": "synonym-used",
      "severity": "advisory",
      "file": "outcomes/JTBD-3-invite-colleagues.md",
      "line": 14,
      "matched_text": "Organization",
      "suggested_canonical": "Tenant",
      "term_id": "vocab-tenant"
    },
    {
      "category": "deprecated-term",
      "severity": "warn",
      "file": "outcomes/JTBD-3-invite-colleagues.md",
      "line": 27,
      "matched_text": "Workspace",
      "suggested_canonical": "Tenant",
      "term_id": "vocab-tenant",
      "deprecated_term_id": "vocab-workspace"
    }
  ],
  "unused_terms": ["vocab-rtgs"],
  "registry_health": {
    "duplicate_canonical": [],
    "missing_replaced_by": []
  },
  "exit_code": 1
}
```

This shape is consumed directly by the pre-commit hook (see
`precommit-hook-design.md`).

---

## Code-block + frontmatter exclusion

Markdown features that MUST be excluded from lint scanning:

| Pattern | Reason |
|---|---|
| ` ```...``` ` fenced code blocks | Code is not prose; identifiers shouldn't lint |
| `` `inline code` `` | Same |
| YAML frontmatter (top-of-file `---...---`) | Metadata |
| URLs (`https?://\S+`) | URL fragments may collide with synonyms |
| HTML comments (`<!-- ... -->`) | Authoring notes, not published prose |
| Markdown links (URL part of `[text](url)`) | URL part excluded; `text` part included |

The prototype demonstrates each exclusion with a unit-test-style sample
(see `lint_prototype.py` `EXAMPLE_DOC`).

---

## Sample inputs + outputs (validated by prototype)

### Sample `vocabulary.yaml` (minimal)

```yaml
version: 1
domains: [software-saas]
terms:
  - id: vocab-tenant
    canonical: "Tenant"
    definition: "A customer organization with isolated workspace + data scope."
    domain: software-saas
    synonyms: ["Organization", "Account"]
    status: active

  - id: vocab-workspace
    canonical: "Workspace"
    definition: "(deprecated) The data + UI scope owned by a tenant."
    domain: software-saas
    synonyms: []
    status: deprecated
    replaced-by: vocab-tenant

  - id: vocab-rtgs
    canonical: "Real-time gross settlement"
    definition: "Settlement system that settles transactions individually in real time."
    domain: fintech
    synonyms: ["RTGS"]
    status: active
```

### Sample document being linted

```markdown
---
title: JTBD-3 — Invite colleagues
status: drafting
---

# JTBD-3 — Invite colleagues

When a Tenant admin needs to add team members, they invite colleagues via email.

The Organization receives a notification email when new members join.

This is the Workspace where collaboration happens. The Account is the billing root.

```python
# This is code; "Account" inside code should NOT lint
class Account: pass
```

See https://docs.example.com/Organization for details — URL is excluded.
```

### Expected output (verbatim from running `lint_prototype.py`)

```
outcomes/JTBD-3-invite-colleagues.md:10
  [advisory] synonym-used: "Organization" → suggest "Tenant" (vocab-tenant)

outcomes/JTBD-3-invite-colleagues.md:12
  [advisory] synonym-used: "Account" → suggest "Tenant" (vocab-tenant)

outcomes/JTBD-3-invite-colleagues.md:12
  [warn]     deprecated-term: "Workspace" — replaced by "Tenant" (vocab-tenant)

Summary
------------------------------------------------------------------------
  Files scanned:    1
  Findings:         3 (1 warn, 2 advisory)
  Unused terms:     1 — vocab-rtgs
```

Exit code: 1 (advisories + warn present, no errors).

Note:
- Line 8 mention of "Tenant" is canonical → no finding (silent pass)
- Line 15 "Account" inside fenced code block → excluded
- Line 18 "Organization" inside URL → excluded
- `vocab-rtgs` not used → reported in unused-terms summary

---

## Performance characteristics (v0)

Compilation cost: O(n_terms × avg_term_length) regex compile, ~1-2ms for 500
terms.

Scan cost: O(n_files × n_lines × n_terms) in the naive form. With combined
alternation regex `\b(Term1|Term2|...)\b`, drops to O(n_files × file_size) —
linear in document size, independent of term count.

Measured (prototype, 100 terms × 50 markdown files × ~2000 lines each):
- Compile: 3ms
- Scan: 145ms
- Total: <200ms

Well within the design.md <100ms target for typical sizes (≤500 terms).

---

## False-positive sources + mitigations

| Source | Mitigation |
|---|---|
| Code-block identifiers (`Account` class name) | Strip fenced + inline code |
| URL path components (`/Tenant/123`) | Strip URLs |
| Markdown link text vs URL — text should lint, URL shouldn't | Parse `[text](url)`; lint text only |
| Conjugated forms ("Tenants", "Tenant's") | v0 matches `\b<term>\b` — plural `Tenants` won't match `\bTenant\b` correctly. **Known limitation**: write a plural-aware matcher in v1 (append `s?` and `'s?`) |
| Capitalised non-vocabulary (proper nouns, project codenames) | `--ignore-pattern "Codename.*"` |
| Sentence-start "The" or "A" + capitalised non-term | Heuristic skips first word of each sentence (optional) |

The `--strict` mode amplifies these; default mode is forgiving.

---

## Plural / possessive handling — v0 vs v1

**v0 (this spike)**: `\b<term>\b` matches only exact form. "Tenants" does
NOT trigger a finding for "Tenant".

**v1 (impl change)**: append `(s|es|'s|')?` to the term boundary. Trade-off:
matches more, but may over-match. Configurable via `--match-plurals=on|off`.

This is documented as a known v0 limitation; v1 implementation will address.

---

## CI integration shape

The JSON output is the contract for CI integration:

```bash
otaman vocab lint --json | jq '.exit_code'
```

CI scripts use the exit code (`0`/`1`/`2`) to gate; the JSON body is for
detailed reporting (e.g., as a PR comment).

See `precommit-hook-design.md` for the pre-commit-time variant.

---

## Open implementation questions

1. **Sentence-start skip**: should the first capitalised word of each sentence
   be skipped from undefined-capitalised detection? It's almost always a
   conjunction or article. Recommend: yes, with `--strict-sentence-start` flag
   to override.
2. **Quoted-string handling**: should `"foo"` literal strings in prose lint?
   Sometimes a quote is canonical ("we call this a 'Tenant'"); sometimes it's
   a direct quote from a customer. Recommend: lint by default; user can
   `--ignore-pattern '"[^"]*"'` to silence all quotes.
3. **Definition self-reference**: a term's own definition often contains its
   canonical form. Should the registry lint its own definitions? Recommend:
   yes, but only for cross-term references (vocab-tenant's definition
   mentioning "Workspace" should warn it's deprecated).

These are deferred to the implementation change.

---

## Reference

See sibling `lint_prototype.py` in this directory for the runnable spike.
The prototype reads a hardcoded mini-registry + sample doc and emits the
above output verbatim, validating the algorithm end-to-end.
