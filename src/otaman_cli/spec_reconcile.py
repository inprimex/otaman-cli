"""One-time reconciliation of contradictory ratification records (1.6).

A change carrying ``ratified: true`` while its stage is still short of
``spec-approved`` is a contradiction: a human attestation exists, but the stage
the dispatch gate reads says it never happened. Those records were produced by
the pre-fix ``apply_ratification``, which hardcoded ``stage: approved`` and so
could not advance (or preserve) ``spec-approved`` — pmeets accumulated at least
four and "fixed" each by hand-editing ``.openspec.yaml``.

The specced remedy is a verb, not a hand-edit: this module finds the records so
``otaman spec approve <change>`` can correct them. Per spec-agent's constraint
the findings are SPLIT — a contradictory record on an ACTIVE change is
dispatch-relevant and actionable, while an ARCHIVED one is closed work that
already made it through the archive gate (which keys on ``approved_by``, not the
stage) and must not nag anyone.

A third category exists because it was found in the wild: an ``.openspec.yaml``
that YAML cannot parse. ``otaman_core.spec_lifecycle.read_openspec`` returns
``{}`` for those, which would make a contradictory record inside one INVISIBLE
to this scan — the same silent-absence failure this change exists to end. So the
file is read here directly and an unparseable record is reported as its own
category: not assessed, rather than quietly clean.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: Findings that call for human action vs. findings that are merely history.
ACTIONABLE = "needs-action"
CLOSED = "already-archived"
UNREADABLE = "unreadable"


@dataclass(frozen=True)
class Finding:
    """One record the reconciliation has something to say about."""

    change: str
    category: str
    stage: str = ""
    ratified_by: str = ""
    ratified_at: str = ""
    error: str = ""

    @property
    def fix_command(self) -> str:
        """The verb that corrects this record, or '' when nothing to run."""
        return f"otaman spec approve {self.change}" if self.category == ACTIONABLE else ""


def _load(path: Path) -> tuple[dict, str]:
    """(data, error) for one ``.openspec.yaml``.

    Deliberately NOT ``read_openspec``: that conflates "empty" with "unparseable"
    by returning ``{}`` for both, which would hide a contradictory record inside
    a broken file. A parse failure is returned as *error* so the caller can
    report it instead of treating the change as clean.
    """
    import yaml

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, f"unreadable file: {exc.strerror or exc}"
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        detail = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
        return {}, f"unparseable YAML: {detail}"
    return (data if isinstance(data, dict) else {}), ""


def _is_contradictory(data: dict) -> bool:
    """``ratified: true`` while the stage has not reached ``spec-approved``."""
    from otaman_core.spec_lifecycle import spec_approved_reached

    return data.get("ratified") is True and not spec_approved_reached(data)


def _ratifier(data: dict) -> str:
    """Who ratified, from the ``approved_by`` marker (``"ratified: <who> — <why>"``)."""
    raw = data.get("approved_by")
    if not isinstance(raw, str) or not raw.strip():
        return ""
    marker = raw.strip()
    if marker.lower().startswith("ratified:"):
        marker = marker.split(":", 1)[1].strip()
    return marker.split("—")[0].strip() or marker


def scan(changes_dir: Path | None) -> list[Finding]:
    """Every contradictory-or-unassessable record under *changes_dir*.

    Walks the active changes and the ``archive/`` subtree, tagging each finding
    ACTIONABLE (active, needs the verb), CLOSED (archived, informational), or
    UNREADABLE (could not be assessed). Sorted actionable-first so the thing a
    human must do leads the report.
    """
    if changes_dir is None or not changes_dir.is_dir():
        return []

    findings: list[Finding] = []
    archive = changes_dir / "archive"
    roots = [(changes_dir, False)] + ([(archive, True)] if archive.is_dir() else [])

    for base, archived in roots:
        try:
            entries = sorted(p for p in base.iterdir() if p.is_dir() and p.name != "archive")
        except OSError:
            continue
        for d in entries:
            oy = d / ".openspec.yaml"
            if not oy.is_file():
                continue
            data, error = _load(oy)
            if error:
                findings.append(Finding(change=d.name, category=UNREADABLE, error=error))
                continue
            if not _is_contradictory(data):
                continue
            findings.append(
                Finding(
                    change=d.name,
                    category=CLOSED if archived else ACTIONABLE,
                    stage=str(data.get("stage") or "unknown"),
                    ratified_by=_ratifier(data),
                    ratified_at=str(data.get("ratified_at") or ""),
                )
            )

    order = {ACTIONABLE: 0, UNREADABLE: 1, CLOSED: 2}
    findings.sort(key=lambda f: (order.get(f.category, 9), f.change))
    return findings


def by_category(findings: list[Finding]) -> dict[str, list[Finding]]:
    """Findings grouped by category (missing categories present as empty lists)."""
    out: dict[str, list[Finding]] = {ACTIONABLE: [], UNREADABLE: [], CLOSED: []}
    for f in findings:
        out.setdefault(f.category, []).append(f)
    return out


__all__ = [
    "ACTIONABLE",
    "CLOSED",
    "UNREADABLE",
    "Finding",
    "by_category",
    "scan",
]
