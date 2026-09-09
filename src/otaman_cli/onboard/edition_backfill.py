"""Edition-aware backfill of org-implied platform sections (scan-init-edition-backfill 1.1).

Live 2026-09-09: a new program (pmeets) scanned under a working EE tenant
produced a platform.yaml with NO ``runner:``/``terminal:`` at all. That is not
a cosmetic gap — otaman-runner derives ``agent_bootstrap`` ONCE from the
alphabetically-first registered platform and applies it TENANT-WIDE, so one
program that is missing (or divergent in) these sections can regress every
program's sessions. Consistency is a correctness requirement.

This module locates the org's PRIMARY registered platform — the alphabetically
-first entry in the platforms registry, exactly the one otaman-runner treats as
its tenant-wide bootstrap source — and copies its org-implied sections
(``runner:``, ``terminal:``, ``human-roster:``) into a target program config,
filling ONLY the sections the target is missing (idempotent, byte-consistent
with the org's own blocks). When no template can be determined the plan is
AMBIGUOUS so the caller confirms with the human rather than guess or omit
silently — never a silent gap.

Pure logic + one ruamel writer; the CLI surfaces (scan/init) supply the
confirm/announce UX.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The org-implied sections scan/init backfills from the org's primary platform.
ORG_IMPLIED_SECTIONS = ("runner", "terminal", "human-roster")


def _load_yaml(path: Path) -> dict:
    """Best-effort mapping read; ``{}`` on missing/unparseable/non-mapping."""
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - any read/parse failure → treat as empty
        return {}
    return data if isinstance(data, dict) else {}


def _section_absent(doc: dict, key: str) -> bool:
    """A section counts as MISSING when its key is absent or its value is empty
    (``None`` / empty mapping / empty list). Idempotency hinges on this: a
    already-populated section is never overwritten."""
    if key not in doc:
        return True
    v = doc[key]
    return v is None or (isinstance(v, (dict, list)) and len(v) == 0)


@dataclass
class TemplateSource:
    """The org's primary registered platform, the backfill template."""

    name: str
    path: Path
    sections: dict[str, Any]  # ORG_IMPLIED_SECTIONS actually present in it


def find_primary_platform(
    *, exclude: Path | None = None, dir_override: str | None = None
) -> TemplateSource | None:
    """The runner's PRIMARY registered platform as the backfill template.

    The alphabetically-first ``ok`` entry in the platforms registry — the same
    entry otaman-runner derives its tenant-wide bootstrap from. *exclude* (the
    target program's own platform, matched by resolved path) is skipped so a
    program never templates from itself. ``None`` when no usable template
    remains (empty registry, only dangling links, or self is the only entry).
    """
    from otaman_cli._runner_registry import platforms_list

    exclude_r = exclude.resolve() if exclude else None
    for entry in platforms_list(dir_override=dir_override):  # registry is sorted by name
        if entry.get("state") != "ok":
            continue
        target = entry.get("target")
        if not isinstance(target, Path):
            continue
        target_r = target.resolve()
        if exclude_r is not None and target_r == exclude_r:
            continue
        doc = _load_yaml(target_r)
        sections = {k: v for k, v in doc.items() if k in ORG_IMPLIED_SECTIONS}
        return TemplateSource(
            name=str(entry.get("name") or target_r.stem), path=target_r, sections=sections
        )
    return None


@dataclass
class BackfillPlan:
    """What backfill would do for one target platform config."""

    edition: str  # ce / ee / unknown (identity signal, for messaging + the ambiguity gate)
    template: TemplateSource | None
    additions: dict[str, Any] = field(default_factory=dict)  # section -> value to inject
    missing: tuple[str, ...] = ()  # org-implied sections the target lacks
    ambiguous: bool = (
        False  # sections missing but no template to copy from → confirm, never omit silently
    )
    reason: str = ""

    @property
    def has_work(self) -> bool:
        return bool(self.additions) or self.ambiguous


def plan_backfill(
    target_doc: dict, *, edition: str, template: TemplateSource | None
) -> BackfillPlan:
    """Decide what to backfill into *target_doc*.

    Idempotent: only sections absent/empty in the target AND present in the
    template are added, copied verbatim (byte-consistent with the org). When
    sections are missing but no template exists the plan is AMBIGUOUS — the
    caller must confirm with the human, never guess or silently omit. When a
    template exists but itself defines none of the missing sections, those
    sections simply aren't part of this org's convention (e.g. a CE org has no
    ``runner:``) — not ambiguous, nothing to do.
    """
    missing = tuple(s for s in ORG_IMPLIED_SECTIONS if _section_absent(target_doc, s))
    if not missing:
        return BackfillPlan(
            edition=edition, template=template, reason="all org-implied sections present"
        )
    if template is None:
        return BackfillPlan(
            edition=edition,
            template=None,
            missing=missing,
            ambiguous=True,
            reason="missing org-implied sections but no registered org platform to copy from",
        )
    additions = {
        s: copy.deepcopy(template.sections[s])
        for s in missing
        if s in template.sections and not _section_absent(template.sections, s)
    }
    reason = (
        f"backfill {', '.join(additions)} from org primary '{template.name}'"
        if additions
        else f"org primary '{template.name}' defines none of the missing sections — nothing to copy"
    )
    return BackfillPlan(
        edition=edition,
        template=template,
        additions=additions,
        missing=missing,
        ambiguous=False,
        reason=reason,
    )


def plan_for_platform(
    platform_path: Path, *, edition_path: Path | None = None, dir_override: str | None = None
) -> BackfillPlan:
    """Assemble a :class:`BackfillPlan` for an on-disk platform.yaml (or draft):
    detect edition, resolve the org primary (excluding this file), diff."""
    from otaman_cli.edition import get_edition

    doc = _load_yaml(platform_path)
    edition = get_edition(edition_path)
    template = find_primary_platform(exclude=platform_path, dir_override=dir_override)
    return plan_backfill(doc, edition=edition, template=template)


def apply_backfill(platform_path: Path, additions: dict[str, Any]) -> list[str]:
    """Inject *additions* into *platform_path* via a comment/format-preserving
    ruamel round-trip, only for sections still absent (idempotent). Returns the
    list of section names actually written."""
    if not additions:
        return []
    import io

    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    # width=4096 mirrors platform_gen: keep long command scalars on one line so
    # round-trips stay byte-stable (deploy-agent drift bug 20260824T125215).
    yaml.width = 4096
    doc = yaml.load(platform_path.read_text(encoding="utf-8")) or {}
    written: list[str] = []
    for key, value in additions.items():
        if _section_absent(doc, key):
            doc[key] = value
            written.append(key)
    if not written:
        return []
    buf = io.StringIO()
    yaml.dump(doc, buf)
    platform_path.write_text(buf.getvalue(), encoding="utf-8")
    return written


__all__ = [
    "ORG_IMPLIED_SECTIONS",
    "BackfillPlan",
    "TemplateSource",
    "apply_backfill",
    "find_primary_platform",
    "plan_backfill",
    "plan_for_platform",
]
