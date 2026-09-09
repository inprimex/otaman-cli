"""Artifact ownership + tracking metadata (console-ux-redesign wave 1, task 1.4 / S9/S10).

Every artifact row and detail view shows who created it and when, the priority
inherited from the linked outcome, a RESERVED deadline slot (the field doesn't
exist yet — the layout reserves it now, an undefined value is never shown), and,
when pm-sync is enabled, the linked issue/ticket id so the console and the
external tracker agree on identity at a glance (S10).

Creator/when come from git (first commit that added the change dir) with the
change's ``.openspec.yaml`` as fallback; priority from the linked outcome via the
registry; pm-sync ids from a per-change ``.pm-sync.yaml`` when the bridge has
written one. Every read is best-effort — a missing source yields ``None``, never
a crash or a fabricated value.
"""

from __future__ import annotations

from dataclasses import dataclass

from otaman_cli.console.bus import Program


@dataclass(frozen=True)
class ChangeMeta:
    creator: str | None = None
    created: str | None = None  # YYYY-MM-DD
    priority: str | None = None  # inherited from the linked outcome
    deadline: str | None = None  # RESERVED — always None until the field is specced
    pm_sync_id: str | None = None  # change-level issue/ticket id
    pm_sync_provider: str | None = None


def read_pm_sync(change_dir) -> tuple[str | None, str | None]:
    """(issue_id, provider) from ``<change_dir>/.pm-sync.yaml`` — (None, None) if absent."""
    try:
        import yaml

        pf = change_dir / ".pm-sync.yaml"
        if not pf.is_file():
            return None, None
        data = yaml.safe_load(pf.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None, None
        issue = data.get("change_issue_id") or data.get("issue_id")
        return (str(issue) if issue is not None else None, data.get("provider"))
    except Exception:  # noqa: BLE001
        return None, None


def outcome_priority(program: Program, change_name: str) -> str | None:
    """Priority inherited from the change's linked outcome, or None."""
    try:
        from otaman_cli.console.tree import _change_outcome_id, _load_registries, registries_enabled

        if not registries_enabled(program):
            return None
        oid = _change_outcome_id(program, change_name)
        if not oid:
            return None
        outcomes, _ = _load_registries(program)
        if outcomes is None:
            return None
        match = next((o for o in outcomes.outcomes if o.id == oid), None)
        return str(match.priority) if match is not None else None
    except Exception:  # noqa: BLE001
        return None


def _git_first_commit(specs_root, change_dir) -> tuple[str | None, str | None]:
    """(author, date) of the FIRST commit that added *change_dir*, or (None, None)."""
    import subprocess

    try:
        r = subprocess.run(
            [
                "git",
                "-C",
                str(specs_root),
                "log",
                "--reverse",
                "--diff-filter=A",
                "--format=%an|%ad",
                "--date=short",
                "--",
                str(change_dir),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            author, _, date = r.stdout.strip().splitlines()[0].partition("|")
            return (author or None), (date or None)
    except (OSError, subprocess.SubprocessError):
        pass
    return None, None


def change_metadata(program: Program, change_name: str) -> ChangeMeta:
    """Assemble the S9/S10 metadata for one change (best-effort; safe off-thread)."""
    from otaman_cli.console.lifecycle import _specs_changes_dir, _specs_root

    changes = _specs_changes_dir(program)
    change_dir = (changes / change_name) if changes else None

    creator = created = None
    if change_dir is not None:
        # git first-commit is authoritative for creator/when; .openspec.yaml fills gaps.
        creator, created = _git_first_commit(_specs_root(program), change_dir)
        try:
            from otaman_core.spec_lifecycle import read_openspec

            data = read_openspec(change_dir / ".openspec.yaml")
            created = created or (str(data["created"]) if data.get("created") else None)
            creator = creator or data.get("spec_owner") or data.get("requested_by")
        except Exception:  # noqa: BLE001
            pass

    pm_id, pm_provider = read_pm_sync(change_dir) if change_dir is not None else (None, None)
    return ChangeMeta(
        creator=str(creator) if creator else None,
        created=created,
        priority=outcome_priority(program, change_name),
        deadline=None,  # RESERVED
        pm_sync_id=pm_id,
        pm_sync_provider=pm_provider,
    )


def metadata_lines(meta: ChangeMeta) -> list[str]:
    """Human-readable metadata rows for a detail view (values-free)."""
    lines = [
        f"creator: {meta.creator or '—'}   created: {meta.created or '—'}",
        f"priority: {meta.priority or '—'}   deadline: {meta.deadline or '(reserved)'}",
    ]
    if meta.pm_sync_id:
        provider = f" ({meta.pm_sync_provider})" if meta.pm_sync_provider else ""
        lines.append(f"pm-sync: {meta.pm_sync_id}{provider}")
    return lines


__all__ = ["ChangeMeta", "change_metadata", "metadata_lines", "outcome_priority", "read_pm_sync"]
