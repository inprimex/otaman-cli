"""knowledge-v2 2.2 — the three health signals over a knowledge vault.

Pure logic, no printing and no mutation except through :func:`sweep`, so the
doctor section and the `knowledge sweep` verb share one implementation rather
than growing a second opinion about what "past due" means.

Three signals, and they fail in different directions:

* **decay** — an entry past its own `review-by` that nobody has read since is
  a claim the corpus is still asserting on no evidence. It goes dormant: not
  deleted, not rewritten, and reversible, because "nobody looked recently" is
  weak evidence of wrongness and strong evidence of irrelevance.
* **out-of-band edit** — the vault is Obsidian-compatible by design, so a human
  WILL edit a note in Obsidian. That is allowed; it is silently diverging from
  what the CLI would write that is not.
* **ownership violation** — an entry written past its partition owner skipped
  the curator gate that the index line depends on.

The dormancy REASON is not written into the entry. That is core's ruling
(``set_state``: "the caller states the reason ... it is not persisted on the
entry, only surfaced by the sweep"), and it is what makes "reversibly" exact:
the sweep changes one frontmatter field and touches nothing the author wrote.

Only the first two can be computed from the vault alone. Ownership needs the
partitions map, which does not exist yet, and so it returns NOT-CHECKED rather
than an empty list: reporting "no violations" from a check that never ran is
the failure no-silent-success names, and it is the exact shape that made a
stale session look healthy for a week.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Signal kinds. `NOT_CHECKED` is a first-class result, never an empty list.
DECAY = "decay"
OUT_OF_BAND = "out-of-band"
OWNERSHIP = "ownership"
NOT_CHECKED = "not-checked"


@dataclass(frozen=True)
class Finding:
    """One thing worth saying about the vault."""

    kind: str
    stem: str
    detail: str
    remedy: str = ""


def _date(value: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None


def is_unreinforced(entry: Any, today: str) -> bool:
    """Is *entry* past review-by with no read to justify keeping it active?

    Reinforcement is an access AT OR AFTER the review date: reading an entry
    before it came due says nothing about whether it is still true now. An
    entry with no `accessed-at` at all has never been read through the CLI,
    which is the strongest decay signal available.
    """
    due = _date(getattr(entry, "review_by", ""))
    now = _date(today)
    if due is None or now is None or due >= now:
        return False
    read = _date(getattr(entry, "accessed_at", "") or "")
    return read is None or read < due


def decay_candidates(entries: list[Any], today: str) -> list[Finding]:
    """Active entries that are past due and unread since."""
    out: list[Finding] = []
    for entry in entries:
        if getattr(entry, "state", "active") != "active":
            continue
        if not is_unreinforced(entry, today):
            continue
        read = getattr(entry, "accessed_at", "") or "never read"
        out.append(
            Finding(
                kind=DECAY,
                stem=getattr(entry, "stem", "") or getattr(entry, "title", "?"),
                detail=(
                    f"review-by {getattr(entry, 'review_by', '?')} has passed and the last "
                    f"access was {read}"
                ),
                remedy="`otaman knowledge sweep --apply` to retire it, or `show` it to reinforce",
            )
        )
    return out


def known_keys(core: Any) -> set[str]:
    """Frontmatter keys the CLI's writer can emit, DERIVED from core's schema.

    Derived rather than listed so core adding a field does not turn every entry
    carrying it into a false positive — the drift that a hardcoded list causes
    silently, and the reason the fleet probes attributes instead of versions.
    """
    import dataclasses

    names: set[str] = set()
    for f in dataclasses.fields(core.KnowledgeEntry):
        if f.name == "body":
            continue
        names.add(f.name)
        names.add(f.name.replace("_", "-"))  # `review_by` is written `review-by`
    return names


def out_of_band_edits(knowledge_dir: Path, core: Any) -> list[Finding]:
    """Entries carrying content the CLI's writer could not have produced.

    The test is NOT a byte-level round-trip. That was the first implementation
    and it fired on all six pre-knowledge-v2 entries in the live vault, whose
    only sin was being written before `state:` existed and before the renderer
    quoted dates. A signal that flags every legacy entry as hand-edited is one
    people learn to scroll past, and the real edit then hides in the noise.

    So the signal is narrowed to what normalization cannot explain:

    * a frontmatter key the schema does not know — the CLI never writes one, and
      parsing silently DROPS it, so the next write would destroy it; and
    * an entry that fails core's own validation.

    KNOWN GAP, and a RULED one: an edit to a known field's value, or to BODY
    PROSE, is indistinguishable from a CLI write and is invisible here.

    Closing it would need a content hash on the entry, and core ruled against
    that (2026-09-25, in answer to this module's question). Three reasons, kept
    here so the question is not re-opened by someone who only sees the hole:
    the vault is human-editable by design, so a hash is a line a human sees and
    does not understand, and a legitimate prose fix in Obsidian then reports as
    drift until re-blessed — cry-wolf, needing a bless verb that does not
    exist; git ALREADY is the byte-level record (design D1), with author and
    time, for free; and the cases that actually destroy something — a dropped
    unknown key, an invalid entry — are the ones already caught above. The
    remaining case looks benign because it usually is: a human improving prose
    in the lens the vault was built for. Flagging it fights the feature.

    If byte-drift detection is ever genuinely needed, the ruled direction is a
    git-backed doctor check (files changed since the verb's last commit, or
    commits not authored by the verb) — not a schema field.

    THIS PARAGRAPH is the register: `test_the_known_gap_is_stated` fails if it
    disappears, so the hole stays admitted in executable form.
    """
    if not knowledge_dir.is_dir():
        return []
    try:
        from otaman_core.frontmatter import parse as parse_frontmatter
    except Exception:  # noqa: BLE001 - no frontmatter reader → nothing checkable
        return []

    allowed = known_keys(core)
    out: list[Finding] = []
    for path in sorted(knowledge_dir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        entry = core.parse_entry(text)
        if entry is None:
            continue  # not an entry (a README in the pile); load_entries skips it too

        fm, _ = parse_frontmatter(text)
        stray = sorted(k for k in (fm or {}) if str(k) not in allowed)
        if stray:
            out.append(
                Finding(
                    kind=OUT_OF_BAND,
                    stem=path.stem,
                    detail=(
                        f"frontmatter carries {', '.join(repr(k) for k in stray)}, which the "
                        "schema does not know — the next CLI write would drop it"
                    ),
                    remedy="move it into the body, or propose the field",
                )
            )
            continue

        errors = core.validate_entry(entry)
        if errors:
            out.append(
                Finding(
                    kind=OUT_OF_BAND,
                    stem=path.stem,
                    detail=f"does not satisfy the schema: {errors[0]}",
                    remedy=f"`otaman knowledge show {path.stem}` to see it as parsed",
                )
            )
    return out


def ownership_violations(entries: list[Any], partitions: dict[str, str] | None) -> list[Finding]:
    """Entries authored past their partition's owner.

    *partitions* is `{function: owner}`. When it is None or empty there is
    nothing to check against, and the answer is ONE not-checked finding — never
    an empty list, which would render as "no violations found" and quietly
    assert the curator gate is holding when it was never tested.
    """
    if not partitions:
        return [
            Finding(
                kind=NOT_CHECKED,
                stem="",
                detail=(
                    "no `program.processes.knowledge.partitions` map, so entry authorship "
                    "cannot be checked against a partition owner"
                ),
                remedy="the map arrives with knowledge-v2 3.1 (otaman-plugin)",
            )
        ]
    out: list[Finding] = []
    for entry in entries:
        function = getattr(entry, "function", "") or ""
        owner = partitions.get(function)
        author = getattr(entry, "author", "") or ""
        if not function or not owner or not author or author == owner:
            continue
        out.append(
            Finding(
                kind=OWNERSHIP,
                stem=getattr(entry, "stem", "") or getattr(entry, "title", "?"),
                detail=(f"authored by {author}, but the {function} partition is owned by {owner}"),
                remedy=f"{owner} authors the index line — submit via the bus",
            )
        )
    return out
