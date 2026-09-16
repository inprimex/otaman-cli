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

# Role shapes the QUEUE, not the menu. Navigation is never role-gated: browsing
# stays open to every hat, because transparency is not a privilege in this
# product and a solo operator must never be locked out of a view by whichever
# hat they happen to resolve to.

#: hat -> the queue rows that hat is the next actor for.
_HAT_ROWS = {
    "founder": ("cost_acceptance", "value_decisions", "scr", "outcome"),
    "cofounder": ("cost_acceptance", "value_decisions", "scr", "outcome"),
    "ceo": ("cost_acceptance", "value_decisions"),
    "cpo": ("value_decisions", "outcome"),
    "cto": ("solution_choices", "spec_review"),
    "approver": ("scr",),
    "developer": ("assigned_tasks",),
}

#: Every row, in display order — the union a solo operator sees.
QUEUE_ROWS = (
    "scr",
    "outcome",
    "cost_acceptance",
    "value_decisions",
    "solution_choices",
    "spec_review",
    "ratify_blocked",
    "assigned_tasks",
)

_ROW_LABEL = {
    "scr": "spec-change requests",
    "outcome": "outcome-proposals",
    "cost_acceptance": "awaiting cost acceptance",
    "value_decisions": "value decisions",
    "solution_choices": "solution choices",
    "spec_review": "awaiting spec review",
    "ratify_blocked": "ratify-blocked",
    "assigned_tasks": "tasks assigned to you",
}


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
    # 4.1 — extra queue counts, and which rows this hat is the next actor for
    cost_acceptance: int = 0
    value_decisions: int = 0
    solution_choices: int = 0
    assigned_tasks: int = 0
    hats: set = field(default_factory=set)
    # The UNION by default, never (). An empty tuple renders "nothing waiting on
    # you" over a summary that may hold real counts — which is the very
    # inversion 4.2 fixes on the detail surface, reintroduced through a
    # dataclass default. No hat resolved means show everything, everywhere.
    queue_rows: tuple = QUEUE_ROWS
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


def _registry_queue_counts(program) -> tuple[int, int, int]:
    """(cost_acceptance, value_decisions, solution_choices) from the registries.

    Best-effort: a program without the outcomes process contributes zeros rather
    than an error, and a zero row simply does not render.
    """
    try:
        from otaman_cli.registries.loader import resolve_registry_path, yaml_read

        op = resolve_registry_path(program.root, "outcomes")
        sp = resolve_registry_path(program.root, "solutions")
        outcomes = (yaml_read(op) or {}).get("outcomes") or [] if op and op.is_file() else []
        solutions = (yaml_read(sp) or {}).get("solutions") or [] if sp and sp.is_file() else []
    except Exception:  # noqa: BLE001 - registries absent/broken → nothing queued
        return 0, 0, 0

    cost = sum(
        1
        for o in outcomes
        if isinstance(o, dict) and o.get("estimate-requested") and not o.get("cost-accepted")
    )
    # an Approved outcome with no chosen solution is a value decision waiting
    value = sum(
        1
        for o in outcomes
        if isinstance(o, dict)
        and str(o.get("status") or "") == "Approved"
        and not o.get("chosen-solution")
    )
    chosen = {o.get("id") for o in outcomes if isinstance(o, dict) and o.get("chosen-solution")}
    # outcomes with MORE THAN ONE live candidate and no choice yet
    by_outcome: dict[str, int] = {}
    for s in solutions:
        if not isinstance(s, dict) or str(s.get("status") or "").lower() == "discarded":
            continue
        oid = str(s.get("outcome-id") or "")
        if oid and oid not in chosen:
            by_outcome[oid] = by_outcome.get(oid, 0) + 1
    choices = sum(1 for n in by_outcome.values() if n > 1)
    return cost, value, choices


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

    # 4.1 — the acting hat decides which rows are YOURS to act on. Counts are
    # computed the same way regardless; only which rows are SHOWN differs, and
    # navigation stays open to every hat (D6).
    from otaman_cli.console.registry_detail import acting_hats

    hats = acting_hats(program)
    cost_acceptance, value_decisions, solution_choices = _registry_queue_counts(program)
    return HomeSummary(
        hats=hats,
        queue_rows=queue_rows_for_hats(hats),
        cost_acceptance=cost_acceptance,
        value_decisions=value_decisions,
        solution_choices=solution_choices,
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


# ---------------------------------------------------------------------------
# 4.1 — the needs-you queue, filtered by the ACTING HAT (D6)
#
def queue_rows_for_hats(hats: set[str]) -> tuple[str, ...]:
    """Which queue rows the acting hat is the next actor for (4.1 / D6).

    A SOLO OPERATOR — no resolved hat, or several — sees the UNION. Showing an
    unresolved human an empty queue would hide their own work from them, which
    is the same inversion 4.2 fixes on the detail surface: never let a missing
    or ambiguous identity produce LESS than a resolved one.
    """
    rows: set[str] = set()
    for hat in hats:
        rows |= set(_HAT_ROWS.get(hat, ()))
    if not rows:
        return QUEUE_ROWS  # unresolved → the union, never an empty queue
    return tuple(r for r in QUEUE_ROWS if r in rows)


def queue_label(row: str) -> str:
    return _ROW_LABEL.get(row, row)
