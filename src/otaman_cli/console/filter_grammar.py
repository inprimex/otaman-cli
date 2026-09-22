"""The `:` command grammar — one parser, three lenses (console-lens D2).

`:` opens a mini-language, so it is exactly the class shared-logic-single-home
covers: ONE module turns text into a predicate, and each lens applies that
predicate to its own row model. The alternative — per-lens ad-hoc toggles — is
what this replaces; `f` becomes sugar for the closed-state filter rather than a
third way to say the same thing.

Grammar v1, deliberately small (D2: add on demand, not speculatively):

    p<n>        priority           :p1        :p0 :p1 is NOT valid — see below
    s<prefix>   status prefix      :sappr  → approved   (:s d lists candidates)
    t <term>    free text          :t login   matches id + title + description
    a           awaiting you       :a
    e+ / e-     expand / collapse all       VIEW commands, not filters

Conjunction is juxtaposition: `:p1 sc t login` is all three, ANDed. There is no
OR and no negation in v1.

**Filters and view commands are different things**, and the distinction is the
reason `e+`/`e-` live in this grammar instead of on bare keys. A FILTER persists,
is displayed, and is cleared by Esc. A VIEW command acts once and holds no
state. Because they share a parser, `:p1 e+` expands only the P1 rows — the
composition comes free, where two bare keys would have to reimplement it.

Everything here is pure: text in, predicate out. No Textual, no registries, no
I/O — so the grammar is testable without a terminal, which is most of why it is
a module rather than a method on the screen.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

#: View commands. Not filters: they act once and leave no state behind.
VIEW_EXPAND_ALL = "expand-all"
VIEW_COLLAPSE_ALL = "collapse-all"


#: The status vocabulary `s<prefix>` resolves against. Sourced from the palette
#: so the words a reader can TYPE are the words the rows actually SHOW — a
#: second hand-kept list would drift the moment a status is added.
def known_statuses() -> tuple[str, ...]:
    from otaman_cli.console.palette import STATUS_STYLE

    return tuple(sorted({s.lower() for s in STATUS_STYLE}))


class Row(Protocol):
    """What a filter needs from a row. Both TreeNode and the lifecycle row
    satisfy it structurally; `getattr` with defaults keeps a partial row usable
    rather than raising mid-render."""

    id: str
    title: str
    status: str
    priority: str | None


@dataclass(frozen=True)
class Term:
    """One clause of the query."""

    kind: str  # "priority" | "status" | "text" | "awaiting"
    value: str = ""

    def describe(self) -> str:
        if self.kind == "priority":
            return f"P{self.value}"
        if self.kind == "status":
            return self.value
        if self.kind == "text":
            return f'"{self.value}"'
        return "awaiting you"


@dataclass(frozen=True)
class Query:
    """A parsed `:` line: the filter terms, an optional view command, an error."""

    terms: tuple[Term, ...] = ()
    view: str | None = None
    error: str | None = None
    #: Tokens that parsed to nothing usable, kept for the error message.
    rejected: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not self.terms and self.view is None

    def describe(self) -> str:
        """The active-filter display. Empty string when nothing is filtering."""
        return " · ".join(t.describe() for t in self.terms)


_PRIORITY = re.compile(r"^p([0-9])$", re.IGNORECASE)
_STATUS = re.compile(r"^s(.*)$", re.IGNORECASE)


def parse(text: str, *, statuses: Sequence[str] | None = None) -> Query:
    """Parse a `:` line into a :class:`Query`.

    Ambiguity is REPORTED, not guessed: `:s d` with both `done` and `drafting`
    available returns an error naming both, because silently picking one would
    filter the view to something the reader did not ask for and has no way to
    notice.
    """
    vocab = tuple(statuses) if statuses is not None else known_statuses()
    tokens = [t for t in (text or "").strip().split() if t]
    terms: list[Term] = []
    view: str | None = None
    rejected: list[str] = []

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        low = tok.lower()

        if low in ("e+", "e-"):
            view = VIEW_EXPAND_ALL if low == "e+" else VIEW_COLLAPSE_ALL
            i += 1
            continue

        if low == "a":
            terms.append(Term("awaiting"))
            i += 1
            continue

        if low == "t":
            # `t` takes the REST of the line: a search term with spaces in it is
            # the common case, and requiring quotes for it would be a worse
            # trade than losing the ability to put a filter after the text.
            rest = " ".join(tokens[i + 1 :]).strip()
            if not rest:
                return Query(error="t needs a term — try `t login`")
            terms.append(Term("text", rest))
            i = len(tokens)
            continue

        m = _PRIORITY.match(tok)
        if m:
            terms.append(Term("priority", m.group(1)))
            i += 1
            continue

        m = _STATUS.match(tok)
        if m:
            prefix = m.group(1).lower()
            if not prefix:
                return Query(error=f"s needs a status prefix — one of: {', '.join(vocab)}")
            hits = [s for s in vocab if s.startswith(prefix)]
            if not hits:
                return Query(error=f"no status starts with {prefix!r} — try: {', '.join(vocab)}")
            if len(hits) > 1:
                exact = [s for s in hits if s == prefix]
                if not exact:
                    return Query(
                        error=f"{prefix!r} is ambiguous — did you mean: {', '.join(hits)}?"
                    )
                hits = exact
            terms.append(Term("status", hits[0]))
            i += 1
            continue

        rejected.append(tok)
        i += 1

    if rejected:
        return Query(
            error=f"don't understand: {' '.join(rejected)} — try p1, sappr, t <term>, a, e+, e-",
            rejected=tuple(rejected),
        )
    return Query(terms=tuple(terms), view=view)


def _row_text(row: Any) -> str:
    parts = [
        str(getattr(row, "id", "") or ""),
        str(getattr(row, "title", "") or ""),
        str(getattr(row, "description", "") or ""),
    ]
    return " ".join(parts).lower()


def _matches_term(term: Term, row: Any, *, awaiting: set[str] | None) -> bool:
    if term.kind == "priority":
        from otaman_cli.console.palette import short_priority

        return short_priority(getattr(row, "priority", "")).lower() == f"p{term.value}"
    if term.kind == "status":
        from otaman_cli.console.palette import short_status

        return short_status(getattr(row, "status", "")).lower() == term.value
    if term.kind == "text":
        return term.value.lower() in _row_text(row)
    if term.kind == "awaiting":
        # Keyed on the SAME set the marker and the action use (D3), passed in
        # rather than recomputed — a filter that disagrees with the action it
        # sits beside is worse than no filter.
        return bool(awaiting) and str(getattr(row, "id", "")) in awaiting
    return True


def matches(query: Query, row: Any, *, awaiting: set[str] | None = None) -> bool:
    """Whether *row* satisfies every term (conjunction; v1 has no OR)."""
    if query.error:
        return True  # a broken query filters nothing — the error is the feedback
    return all(_matches_term(t, row, awaiting=awaiting) for t in query.terms)


def matches_tree(query: Query, node: Any, *, awaiting: set[str] | None = None) -> bool:
    """Whether *node* or any DESCENDANT matches.

    A tree filter that tested only the node itself would hide every parent of a
    match, so a `:p1` on an outcome-first tree would empty the screen even
    though the P1 changes are right there one level down. A branch survives if
    anything inside it survives; the reader still sees the path to the hit.
    """
    if matches(query, node, awaiting=awaiting):
        return True
    return any(
        matches_tree(query, child, awaiting=awaiting)
        for child in getattr(node, "children", None) or []
    )


__all__ = [
    "Query",
    "Row",
    "Term",
    "VIEW_COLLAPSE_ALL",
    "VIEW_EXPAND_ALL",
    "known_statuses",
    "matches",
    "matches_tree",
    "parse",
]
