"""Textual-free Home aggregation (console-ux-redesign wave 1, task 1.1 / D1).

Home ORIENTS the human before they act: it aggregates counts from the SAME
surfaces the dedicated screens/CLI use (bus, the lifecycle derivation, the
registries/roster/connection readers, the status backend, platform.yaml, the
spec policy) into one :class:`HomeSummary` the HomeScreen renders. It never
re-derives — Home and every drilldown agree by construction.

Every sub-read is best-effort: a broken/absent reader degrades THAT stat to a
sentinel (0 / empty / presence-disabled), never blanks the page. The vocabulary
is adaptive per D1/S1: fleet shows only the agent states that actually exist
(there is no hardcoded "limited"), and program status uses the lifecycle
canon's own triage classes. The feature-usage score is a RESERVED slot — an
undefined number is never displayed (only surfaced once its metric is specced).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from otaman_cli.console.bus import Program


@dataclass(frozen=True)
class HomeSummary:
    """Everything HomeScreen renders — pure counts + small maps, values-free."""

    # YOUR QUEUE
    scr_count: int = 0
    outcome_count: int = 0
    ratify_blocked: int = 0
    spec_review: int = 0
    inbox_count: int = 0
    # FLEET (only states actually present; empty when presence is disabled)
    fleet: dict[str, int] = field(default_factory=dict)
    presence_enabled: bool = False
    # PROGRAM (triage vocabulary — adaptive to the program's own model)
    changes_total: int = 0
    triage: dict[str, int] = field(default_factory=dict)
    # panels
    processes_enabled: list[str] = field(default_factory=list)
    team_humans: int = 0
    team_agents: int = 0
    connections: int = 0
    secrets: int = 0
    skills: int = 0
    policy: str = "warn"
    process_level: str | None = None  # human-set spec_policy.process.level (D7)

    @property
    def decisions_total(self) -> int:
        return self.scr_count + self.outcome_count


def _load_cfg(program: Program) -> dict:
    try:
        from otaman_cli.owner_paths import load_platform_yaml

        return load_platform_yaml(program.root) or {}
    except Exception:  # noqa: BLE001 - a read-only console never dies on a bad config
        return {}


def _count_inbox(program: Program) -> int:
    """The messages-to-human inbox count (badge). Same reader the full inbox view
    (task 1.2) uses, so Home and the inbox screen never disagree."""
    from otaman_cli.console.inbox import list_inbox_messages

    return len(list_inbox_messages(program))


def _queue_counts(program: Program) -> tuple[int, int]:
    try:
        from otaman_cli.console.bus import list_pending_proposals

        props = list_pending_proposals(program)
        return (
            sum(1 for p in props if p.msg_type == "spec-change-request"),
            sum(1 for p in props if p.msg_type == "outcome-proposal"),
        )
    except Exception:  # noqa: BLE001
        return 0, 0


def _program_status(program: Program) -> tuple[int, dict[str, int], int]:
    """(changes_total, {triage: count}, ratify_blocked) from the shared derivation."""
    try:
        from otaman_cli.console.lifecycle import _specs_changes_dir
        from otaman_cli.lifecycle import COMPLETE_UNARCHIVED, TRIAGE_ORDER, derive_change_table

        active_dir, _ = program.bus_paths()
        rows = derive_change_table(
            changes_dir=_specs_changes_dir(program),
            bus_active_dir=active_dir if active_dir.is_dir() else None,
        )
        triage = {t: sum(1 for r in rows if r.triage == t) for t in TRIAGE_ORDER}
        untriaged = sum(1 for r in rows if r.triage not in TRIAGE_ORDER)
        if untriaged:
            triage["untriaged"] = untriaged
        ratify_blocked = sum(
            1
            for r in rows
            if r.state == COMPLETE_UNARCHIVED
            and "human" in r.next_actor
            and "ratify" in r.next_actor
        )
        return len(rows), triage, ratify_blocked
    except Exception:  # noqa: BLE001
        return 0, {}, 0


def _fleet(program: Program) -> tuple[dict[str, int], bool]:
    try:
        from otaman_cli.status import get_backend, is_agent_presence_enabled

        if not is_agent_presence_enabled(program.root):
            return {}, False
        counts: dict[str, int] = {}
        for r in get_backend(program.root).read_all():
            key = r.state.value if hasattr(r.state, "value") else str(r.state)
            counts[key] = counts.get(key, 0) + 1
        return counts, True
    except Exception:  # noqa: BLE001
        return {}, False


def build_home_summary(program: Program) -> HomeSummary:
    """Aggregate the whole Home in one call (safe to run off the UI thread)."""
    cfg = _load_cfg(program)
    scr, outcome = _queue_counts(program)
    changes_total, triage, ratify_blocked = _program_status(program)
    fleet, presence = _fleet(program)

    try:
        from otaman_cli.console.artifacts import list_authored_changes

        spec_review = len(list_authored_changes(program))
    except Exception:  # noqa: BLE001
        spec_review = 0

    try:
        inbox = _count_inbox(program)
    except Exception:  # noqa: BLE001
        inbox = 0

    # PROCESSES — consume platform_ext (the SAME reader the tree/registries stack
    # uses via tree.registries_enabled), NEVER re-parse the raw `processes:` key.
    # F1 (gate 3.1): the raw parse disagreed with platform_ext live — a program
    # with no `processes:` block but spec_policy.process.level=outcomes showed
    # "none" here while the tree treated outcomes as enabled. One derivation.
    try:
        from otaman_cli.registries.platform_ext import load_program_extensions

        procs = load_program_extensions(program.root / "platform.yaml").processes
        processes = [
            n
            for n in ("outcomes", "solutions", "personas")
            if getattr(getattr(procs, n, None), "enabled", False)
        ]
    except Exception:  # noqa: BLE001
        processes = []

    try:
        from otaman_core.human_roster import load_human_roster

        team_humans = len(load_human_roster(program.root / "platform.yaml"))
    except Exception:  # noqa: BLE001
        team_humans = 0
    team_agents = len(
        {r.get("owner") for r in (cfg.get("repos") or []) if isinstance(r, dict) and r.get("owner")}
    )

    try:
        from otaman_core.connections import resolve_for

        connections = len(resolve_for(program.root))
    except Exception:  # noqa: BLE001
        connections = 0

    try:
        from otaman_core._secrets import list_keys

        secrets = len(list_keys(maestro_root=program.root))
    except Exception:  # noqa: BLE001
        secrets = 0

    sk = cfg.get("skills") or {}
    skills = (
        (len(sk.get("extra") or []) + (1 if sk.get("profile") else 0))
        if isinstance(sk, dict)
        else 0
    )

    policy = "warn"
    process_level = None
    try:
        from otaman_cli.console.lifecycle import _load_policy

        sp = _load_policy(program)
        policy = sp.enforcement
        process_level = sp.process_level  # the human-set signal (D7)
    except Exception:  # noqa: BLE001
        policy = "warn"

    return HomeSummary(
        scr_count=scr,
        outcome_count=outcome,
        ratify_blocked=ratify_blocked,
        spec_review=spec_review,
        inbox_count=inbox,
        fleet=fleet,
        presence_enabled=presence,
        changes_total=changes_total,
        triage=triage,
        processes_enabled=processes,
        team_humans=team_humans,
        team_agents=team_agents,
        connections=connections,
        secrets=secrets,
        skills=skills,
        policy=policy,
        process_level=process_level,
    )


__all__ = ["HomeSummary", "build_home_summary"]
