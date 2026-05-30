# `@solution:<id>` Annotation Parser Extension — Design Note (task 3.4)

**Author**: cli-agent
**Date**: 2026-05-30
**Change**: outcome-and-solution-registries
**Scope**: extending `otaman_plugin.map_tasks.parse_tasks_md` to recognise
`@solution:<id>` annotations alongside the existing `@<repo>` annotation

---

## Context

Tasks in `tasks.md` files are currently parseable for one annotation type:

```markdown
- [ ] 1.1 Task description @repo-name
- [ ] 1.2 **repo-name**: Task description
```

`otaman_plugin.map_tasks.parse_tasks_md` extracts the `@<repo-name>` hint and
maps it to the owning agent via `ownership.json`. The parser is currently
implemented in `otaman-plugin/src/otaman_plugin/map_tasks.py`, lines 67-117.

This change adds a SECOND orthogonal annotation: `@solution:<id>`, which
links a task to a Complete solution in `<otaman-business>/solutions.yaml`.

The two annotation kinds COEXIST on the same task:

```markdown
- [ ] 1.1 @otaman-auth-service @solution:SOL-2-magic-link-only Magic-link generator
- [ ] 1.2 @otaman-web @solution:SOL-2-magic-link-only Signup form
- [ ] 1.3 @otaman-email-svc @solution:SOL-2-magic-link-only Postmark integration
```

The parser must extract both independently, in either order, and tolerate
either being absent.

---

## Coordination with other annotation work

Three known annotation kinds (current + planned):

| Annotation | Purpose | Source | Status |
|---|---|---|---|
| `@<repo-name>` | Owning repo → owning agent | existing | shipped (PR #15 fanout) |
| `@solution:<id>` | Link to solution in solution-registry | THIS task | proposed (this doc) |
| `@spawn:<harness>:<backend>` (or similar) | Auto-session-spawn routing hint | `auto-session-spawn-on-bus-events` proposal | proposed |

**Design principle**: a single annotation grammar SHALL handle all three. The
parser learns shape `@<kind>[:<value>][\s|$]` once; new annotation kinds add a
recogniser without re-architecting the parser.

This research note proposes the unified grammar; the auto-session-spawn
implementation lands its kind on top of the same primitive.

---

## Proposed annotation grammar (unified)

```
ANNOTATION := "@" KIND [":" VALUE] (\s | $)
KIND       := [\w-]+
VALUE      := [\w./-]+         # broad enough for paths and qualified ids
```

Three concrete kinds the parser knows about in v1:

| Pattern | Kind | Example |
|---|---|---|
| `@<repo>` | `repo` (no colon → kind=repo) | `@otaman-cli` |
| `@solution:<id>` | `solution` | `@solution:SOL-2-magic-link-only` |
| `@spawn:<harness>:<backend>` | `spawn` (colon-delimited multi-part value) | `@spawn:claude-code:anthropic` |

**Disambiguation rule**: if `@<token>` has no colon, it's a repo annotation
(preserves backward compatibility). If it has a colon, the prefix before the
first colon is the kind; the rest is the value (which may itself contain
colons).

---

## Parser implementation sketch

Extend `parse_tasks_md` in `otaman-plugin/src/otaman_plugin/map_tasks.py`:

```python
_ANNOTATION_RE = re.compile(
    r"@([\w-]+)(?::([\w./:-]+))?(?=\s|$)"
)


def _extract_annotations(text: str) -> tuple[str, dict[str, str]]:
    """Strip @annotations from *text*; return (cleaned_text, annotations_dict).

    Order independent. Multiple annotations of the same kind: last one wins
    (with a debug-log warning).
    """
    annotations: dict[str, str] = {}
    def _consume(m: re.Match) -> str:
        kind = m.group(1)
        value = m.group(2) or kind   # @repo has no colon; value defaults to kind itself
        normalised_kind = "repo" if m.group(2) is None else kind
        annotations[normalised_kind] = value
        return ""   # remove from text
    cleaned = _ANNOTATION_RE.sub(_consume, text)
    return cleaned.strip(), annotations


def parse_tasks_md(tasks_path: Path) -> list[dict[str, Any]]:
    # ... (existing header / checkbox parsing unchanged) ...
    task_text = task_match.group(2).strip()
    task_text, annotations = _extract_annotations(task_text)

    # Backwards-compat: existing repo_hint key kept; new fields added
    tasks.append({
        "text": task_text,
        "done": done,
        "group": current_group,
        "repo_hint": annotations.get("repo"),
        "solution_hint": annotations.get("solution"),
        "spawn_hint": annotations.get("spawn"),   # for auto-session-spawn
        "annotations": annotations,               # all kinds in one dict for forward-compat
    })
```

The `**repo-name**:` prefix syntax (the older alternative form) remains
supported via the existing bold-match branch; nothing changes there.

---

## Validation rules

| Rule | Enforcement |
|---|---|
| `solution_hint` matches `^SOL-\d+-[a-z][a-z0-9-]*$` | warning (non-blocking) — task still maps to repo |
| Referenced solution exists in `solutions.yaml` | warning at `otaman assign` time; not at parse time (decouple parse from registry I/O) |
| Referenced solution status is `Complete` | warning if `Considering` or `Discard`; allows tasks to be authored before promote-to-complete |
| Multiple `@solution:` annotations on one task | last-wins; warning logged |

Parser stays I/O-free (no registry reads); cross-validation happens in
`cmd_assign` after the parse.

---

## `otaman assign` integration

`cmd_assign` (in `otaman-cli/src/otaman_cli/main.py`, line 2254) already calls
`run_script("map-tasks.py", target, capture=True)` and consumes the JSON
report. The map-tasks script will need to:

1. Include `solution_hint` in each task entry of the JSON report
2. Cross-validate against `solutions.yaml` (if present) and surface warnings
3. Add a new `by_solution` aggregation alongside the existing `by_owner`

Report shape (additive):

```json
{
  "feature": "magic-link-signup",
  "total_tasks": 3,
  "by_owner": {
    "auth-agent": ["1.1 Magic-link generator"],
    "web-agent":  ["1.2 Signup form"],
    "email-agent":["1.3 Postmark integration"]
  },
  "by_solution": {
    "SOL-2-magic-link-only": ["1.1 Magic-link generator", "1.2 Signup form", "1.3 Postmark integration"]
  },
  "solution_warnings": [],
  "unassigned": 0
}
```

`cmd_assign` then prints the per-solution summary:

```
Solutions referenced
  SOL-2-magic-link-only: 3 task(s)  [Complete; release: MVP]
```

This makes the outcome → solution → task chain visible at assignment time.

---

## Backward compatibility

Existing `tasks.md` files (no `@solution:` annotation):
- Parser returns `solution_hint: None` for each task
- `cmd_assign` skips the solution-validation step entirely
- `outcome show --with-tasks` shows "(no tasks annotated yet)"
- Zero behaviour change for programs not using outcome management

Existing `@repo-name` annotations:
- Parser still recognises `@otaman-cli` as `repo: otaman-cli` (no colon path)
- The `**repo-name**:` prefix form unchanged
- Existing tests pass without modification

The new fields (`solution_hint`, `spawn_hint`, `annotations`) are ADDITIVE.
Downstream consumers (cmd_complete, cmd_check) ignore them until they have a
reason to use them.

---

## Test plan (for the impl change)

Six test cases cover the new behaviour:

1. **Repo-only annotation**: `@otaman-cli foo` → `repo_hint="otaman-cli"`, `solution_hint=None`
2. **Solution-only annotation**: `@solution:SOL-1-x foo` → `solution_hint="SOL-1-x"`, `repo_hint=None`
3. **Both annotations, repo first**: `@otaman-cli @solution:SOL-1-x foo` → both extracted
4. **Both annotations, solution first**: `@solution:SOL-1-x @otaman-cli foo` → both extracted
5. **Invalid solution id**: `@solution:bogus foo` → captured as `solution_hint="bogus"`, warning surfaced by cmd_assign (not parser)
6. **`**repo-name**:` prefix + solution annotation**: `**otaman-cli**: @solution:SOL-1-x foo` → both extracted

Plus regression tests:
- Existing parser tests (single `@repo` annotation, prefix form, plain task)
  still pass

---

## Coordination with `auto-session-spawn-on-bus-events`

The auto-session-spawn proposal introduces `@spawn:<harness>` (or similar)
annotations on task-assignment messages. Three cohesion concerns:

1. **Parser ownership**: the unified `_extract_annotations()` helper lives in
   `otaman_plugin.map_tasks`. The auto-session-spawn change extends the kind
   recogniser but does NOT rewrite the parser.
2. **Annotation kind namespace**: `repo`, `solution`, `spawn` are reserved.
   New kinds in future proposals add to this list; the parser pattern
   `[\w-]+` accepts any well-formed kind name.
3. **Multi-kind tasks**: a task may carry all three annotations:
   ```
   - [ ] 1.1 @otaman-cli @solution:SOL-2 @spawn:claude-code:anthropic Implement X
   ```
   The parser extracts all three independently; downstream consumers
   (map_tasks for repo, registries for solution, session-spawner for spawn)
   each pick the kind they care about.

**Cross-coupling note to auto-session-spawn**: this design note ships
alongside the outcome-and-solution-registries proposal. Spec-agent should
reference this note in the auto-session-spawn proposal's `design.md` to
confirm the unified grammar.

---

## Out of scope

- **Multi-value annotations** (`@solution:SOL-1,SOL-2`): a task references at
  most one solution in v1. Multi-solution tasks are an anti-pattern (split
  the task instead). If demand surfaces, add comma-split in v2.
- **Annotation values with spaces**: kept out of v1 for parsing simplicity.
  All current uses are slugs/ids without spaces.
- **Self-referential annotations** (`@outcome:...`): outcomes are linked via
  solutions (transitive); direct task → outcome annotation is rejected to
  preserve the workflow chain. If needed, can add in v2.

---

## Implementation phase work

Concrete changes for the follow-on impl change in
`outcome-and-solution-registries-impl`:

| Repo | Change | Estimate |
|---|---|---|
| otaman-plugin | Add `_extract_annotations` + extend `parse_tasks_md` | ~20 LOC + 6 tests |
| otaman-plugin | Extend map_tasks.py JSON report shape (add `by_solution`) | ~15 LOC + 2 tests |
| otaman-cli | Extend `cmd_assign` to print per-solution summary | ~10 LOC + 1 test |
| otaman-cli | `outcome show --with-tasks` reads from this report shape | ~15 LOC + 2 tests |

Total: ~60 LOC + 11 tests. Small, contained, low-risk change.

---

## Cross-references

- `cli-outcome-subcommand.md` — uses solution_hint via `outcome show --with-tasks`
- `cli-solution-subcommand.md` — uses solution_hint via `solution show --tasks-status`
- `../program-vocabulary-registry/cli-vocab-lint-prototype.md` — sibling
  parser-discipline precedent
