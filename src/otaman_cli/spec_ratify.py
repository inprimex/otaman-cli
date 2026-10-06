"""The ONE ratification write — shared by the CLI verb and the console action.

`console-complete-human-actions` 1.1: the console ratify action must run "the same
function as the CLI verb (no second implementation)".

It did not. `commands/spec.py:cmd_ratify` and `console/lifecycle.py:ratify_change`
each sequenced the same three core calls independently:

    ratify(name, by, reason, at)  ->  apply_ratification(data, record)  ->  ratified_at

They AGREED when measured on 2026-10-06, which is the whole problem: two producers of
one rule agree right up to the release where they do not, and `ratified_at` is the
marker `otaman doctor` and `otaman spec status` read for the month count. A drift there
is invisible until a count is wrong.

What stays with each caller, deliberately:

* the CLI verb keeps `safety.confirm_human_decision` (its TTY gate) and the
  authored-stage compose, which needs an approver the console resolves differently;
* the console keeps its ReasonModal prompt, journal entry and commit/push.

Those are genuinely different surfaces. The MUTATION is not, so it lives here.
"""

from __future__ import annotations

from typing import Any

__all__ = ["ratification_fields"]


def ratification_fields(
    data: dict[str, Any], name: str, *, by: str, reason: str, at: str
) -> dict[str, Any]:
    """Apply a ratification to *data* and return the updated mapping.

    Raises `otaman_core.spec_lifecycle.SpecLifecycleError` when core refuses to mint
    the record — the caller decides how to report it, because a CLI exit code and a
    console notification are not the same thing.

    `ratified_at` is stamped HERE rather than by either caller: it is not part of
    core's `apply_ratification` (which is a monotonic stage floor), and it was the one
    field most likely to be added on one path and forgotten on the other.
    """
    from otaman_core.spec_lifecycle import apply_ratification, ratify

    record = ratify(name, by=by.strip(), reason=reason.strip(), at=at)
    updated = apply_ratification(data, record)
    updated["ratified_at"] = at
    return updated
