"""cmt 1.2 — Messages as the policy-ordered, type-grouped tree.

Roman's console had ~1000 pending messages and the handful needing a decision was
buried in task-complete noise: a flat list ordered by arrival, where volume decided
what he saw. This module turns that set into TYPE GROUPS ordered by core's review
policy (`otaman_core.review_policy`, core #108) — mandatory classes first and
expanded, opt-in grouped and collapsed, the auto-triage pile one collapsed count at
the bottom.

Textual-free and pure: rows in, groups out. The screen renders what this returns, so
the ordering rules are testable without a terminal.

Three decisions worth stating:

**Classification is PER ROW, grouping is per type.** `classify` takes the message
type AND its priority, and `priority: urgent` is mandatory whatever the type. So an
`info` bus message marked urgent lands in the mandatory band while the rest of `info`
stays in auto-triage — the same type appears in two bands, with its own count in
each. That is what "nothing mandatory can hide" requires: an urgent item cannot
inherit its type's collapse.

**Collapse state is derived on every paint, never stored.** The delta's third
scenario is a mandatory message arriving into a collapsed group; if collapse were
remembered per group, that message would land inside a closed node and stay unseen.
Groups are rebuilt and re-classified from the snapshot on each refresh, so a new
mandatory row is in the top band before the tree is drawn. The human can still
collapse a group by hand — that lives in the widget, for as long as the tree does.

**The group header names the RAW message type, not a friendly one.** The header is
the string a human has to put in `review-policy.mandatory` to change this policy; a
header reading "SCR" over a type called `spec-change-request` would make the policy
file guesswork.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Both spellings this program's platform.yaml actually uses for block names
#: (`spec_policy` with an underscore, `human-roster` with a hyphen, in one file).
#: core's docstring names `review-policy`; the surrounding config says
#: `review_policy`. Reading one and ignoring the other would mean a human who
#: followed either document gets silence, so both are read — and declaring BOTH is
#: refused rather than resolved, because which one wins is not knowable.
POLICY_KEYS: tuple[str, ...] = ("review-policy", "review_policy")

#: Render order of the three classes — the same three, in the same order, as core's
#: `CLASSES`. A test asserts that against core's constant rather than this file
#: trusting the copy (`shared vocabularies are asserted against core's constant on
#: every consuming side` — rcg 1.4), because a band this file has a name for and
#: core does not would silently drop every message in it.
BAND_ORDER: tuple[str, ...] = ("mandatory", "opt-in", "auto-triage")


@dataclass(frozen=True)
class PolicyView:
    """The active review policy plus the line that names it on screen."""

    policy: Any
    source_line: str
    #: A declaration that could not be parsed, named. The shipped default is in
    #: force when this is set — an unreadable policy must not read as no policy.
    error: str = ""


@dataclass(frozen=True)
class MessageGroup:
    """One type group within one class band."""

    msg_type: str
    review_class: str
    rows: tuple
    #: Default paint state. Mandatory groups are never collapsed: the delta's
    #: "never collapsed-hidden" is about what the human sees without acting.
    collapsed: bool

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def label(self) -> str:
        """`decision-required (3)  [mandatory]` — count always, class when it matters."""
        base = f"{self.msg_type or 'message'} ({self.count})"
        if self.review_class == "opt-in":
            return base
        return f"{base}  [{self.review_class}]"


def _platform_block(root: Path) -> tuple[Any, str, str]:
    """``(block, key, error)`` — the declared review-policy block, if any.

    Reads platform.yaml directly rather than through `yaml_fast.load_file`, which
    returns the same default for missing, empty and unparseable. Those are not the
    same fact here: an undeclared policy is normal and a broken one must be named.
    Called once per screen visit, off the paint path, so the memoization it skips
    would have bought nothing.
    """
    import yaml

    try:
        cfg = yaml.safe_load((root / "platform.yaml").read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return None, "", ""
    except (OSError, yaml.YAMLError) as exc:
        # Unreadable is NOT undeclared: the fail-open family's standing rule. The
        # default still renders, and the line says the file could not be read.
        return None, "", f"platform.yaml could not be read ({type(exc).__name__})"
    if not isinstance(cfg, dict):
        return None, "", "platform.yaml is not a mapping"
    present = [key for key in POLICY_KEYS if key in cfg]
    if len(present) > 1:
        return None, "", f"platform.yaml declares {present!r} — keep one, they conflict"
    if not present:
        return None, "", ""
    return cfg[present[0]], present[0], ""


def load_policy(root: Path) -> PolicyView:
    """The program's review policy, or the shipped default — always named.

    Never raises and never returns a policy without a line a human can read: "no
    policy shown" and "no policy declared" are different facts, and the delta
    requires the undeclared default to name itself.
    """
    from otaman_core.review_policy import ReviewPolicyError, default_policy, parse_review_policy

    block, key, error = _platform_block(root)
    if error:
        return PolicyView(
            policy=default_policy(),
            source_line=f"review policy: shipped default — {error}",
            error=error,
        )
    if block is None:
        return PolicyView(
            policy=default_policy(),
            source_line=(
                "review policy: shipped default (no review-policy declared in platform.yaml)"
            ),
        )
    try:
        policy = parse_review_policy(block)
    except ReviewPolicyError as exc:
        return PolicyView(
            policy=default_policy(),
            source_line=f"review policy: shipped default — {key} is malformed: {exc}",
            error=str(exc),
        )
    return PolicyView(
        policy=policy, source_line=f"review policy: declared in platform.yaml ({key})"
    )


def _sort_key(row: Any) -> str:
    return str(getattr(row, "timestamp", "") or "")


def group_messages(rows, policy) -> list[MessageGroup]:
    """*rows* as type groups, ordered mandatory → opt-in → auto-triage.

    Within a band the freshest group leads (its newest message's timestamp), so the
    decision that just arrived is the first thing under the cursor. Rows inside a
    group are newest-first for the same reason.
    """
    from otaman_core.review_policy import classify

    buckets: dict[tuple[str, str], list] = {}
    for row in rows:
        review_class = classify(
            policy,
            str(getattr(row, "msg_type", "") or ""),
            priority=str(getattr(row, "priority", "") or "") or None,
        )
        key = (review_class, str(getattr(row, "msg_type", "") or ""))
        buckets.setdefault(key, []).append(row)

    groups: list[MessageGroup] = []
    for (review_class, msg_type), bucket in buckets.items():
        bucket.sort(key=_sort_key, reverse=True)
        groups.append(
            MessageGroup(
                msg_type=msg_type,
                review_class=review_class,
                rows=tuple(bucket),
                collapsed=review_class != "mandatory",
            )
        )
    # Two stable sorts rather than one composite key: freshest-first inside a band
    # needs a DESCENDING string, and a composite ascending key would have to negate
    # a timestamp. A group always holds at least one row, so `rows[0]` is safe.
    groups.sort(key=lambda g: _sort_key(g.rows[0]), reverse=True)
    groups.sort(key=lambda g: BAND_ORDER.index(g.review_class))
    return groups


def summary_line(groups: list[MessageGroup]) -> str:
    """What the banner says: the per-class counts, with the must-see kinds named.

    `4 need you now (3 awaiting your decision · 1 awaiting your answer) · 7 to read ·
    812 auto-triaged`.

    Two requirements meet here. cmt 1.2 wants the counts per POLICY CLASS, because
    that is what the ordering now means. dae 1.2 wants a decision and a question
    counted SEPARATELY — "3 awaiting your decision" that silently included questions
    under-describes what the human is looking at, and `a` does not apply to a
    decision-required at all. So the mandatory count breaks out into its kinds
    rather than replacing them.
    """
    per_class = {band: 0 for band in BAND_ORDER}
    for group in groups:
        per_class[group.review_class] += group.count
    must_see = [row for g in groups if g.review_class == "mandatory" for row in g.rows]
    to_decide = sum(1 for row in must_see if getattr(row, "is_decision", False))
    to_answer = sum(1 for row in must_see if getattr(row, "needs_answer", False))
    head = f"{per_class['mandatory']} need you now"
    kinds = []
    if to_decide:
        kinds.append(f"{to_decide} awaiting your decision")
    if to_answer:
        kinds.append(f"{to_answer} awaiting your answer")
    if kinds:
        head += " (" + " · ".join(kinds) + ")"
    parts = [head]
    if per_class["opt-in"]:
        parts.append(f"{per_class['opt-in']} to read")
    if per_class["auto-triage"]:
        parts.append(f"{per_class['auto-triage']} auto-triaged")
    return " · ".join(parts)


__all__ = [
    "BAND_ORDER",
    "POLICY_KEYS",
    "MessageGroup",
    "PolicyView",
    "group_messages",
    "load_policy",
    "summary_line",
]
