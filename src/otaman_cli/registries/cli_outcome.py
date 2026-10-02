"""`otaman outcome <action>` command implementation (tasks 2.1-2.8).

Dispatch table:
    add               — author a new outcome
    list              — enumerate outcomes
    show <id>         — full detail for one outcome
    history <id>      — render transitions[] as table
    promote <id>      — Drafting→Backlog / Backlog→Approved /
                        Approved→In-Progress / In-Progress→Done
    demote <id>       — reverse direction
    request-estimate <id> — flag estimate-requested, emit outcome-estimate-requested
    accept-cost <id> --solution SOL — set cost-accepted=true + chosen-solution + emit
    reject-cost <id> [--reason TEXT] — set cost-accepted=false + emit
    retire <id> [--reason TEXT] — move to Retired
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from otaman_cli.bus_paths import _resolve_bus_paths
from otaman_cli.identity import find_project_root, not_in_project_message
from otaman_cli.registries import access, bus_messages
from otaman_cli.registries.loader import resolve_registry_path
from otaman_cli.registries.outcomes import (
    OutcomeRegistry,
    OutcomeStatus,
    demote_target,
    promote_target,
)
from otaman_cli.registries.platform_ext import load_program_extensions
from otaman_cli.registries.roles import (
    CHOOSE_HATS,
    FUND_HATS,
    acting_hats,
    authz_advisory,
    hat_advisory,
    held_hat,
    operating_mode,
    resolve_operating_actor,
    resolve_roles,
)
from otaman_cli.registries.transitions import make_transition
from otaman_cli.ui import UI

#: What `approval.spec` names: the capability whose clauses grant the authority this
#: record attests to. Core requires a non-empty string and does not interpret it; naming
#: the SPEC rather than the change keeps the record meaningful after the change archives.
APPROVAL_SPEC = "outcome-registry"

#: The hat each authority action is granted by (team-mode "roles are hats"). Recorded in
#: the transition note, because core's approval shape carries `via: hat` but not WHICH —
#: and the delta's founder-mode scenario requires the log to show both hats.
#:
#: The pairs come from `registries.roles`, which is also where the console's two hat
#: tables now live (rac 2.1). `choose` is the CTO's technical judgment, `accept-cost` and
#: `reject-cost` are the CEO's budget authority, and `founder` stands in for either.
ACTION_HATS: dict[str, tuple[str, ...]] = {
    "choose": CHOOSE_HATS,
    "accept-cost": FUND_HATS,
    "reject-cost": FUND_HATS,
}


def _bail(msg: str, code: int = 1) -> int:
    UI.error(msg)
    return code


def _contract():
    """core's registry access contract, through this CLI's single door (`access`).

    REQUIRED rather than probed-with-fallback. This module is the write path the change
    exists to funnel: keeping the old direct `yaml_dump` alive beside the contract would
    leave exactly the second home the delta calls a conformance defect ("a direct
    registry-file access outside it is a conformance defect"). So an old bundle refuses
    with a remedy instead of writing around the chokepoint — the `blocked_gate` shape,
    for the same reason.
    """
    return access.contract()


def _no_contract() -> int:
    return _bail(access.NO_CONTRACT, code=2)


def _approval(root: Path, action: str) -> tuple[dict[str, Any] | None, str]:
    """``(approval record, refusal)`` for an authority action.

    core enforces the approval's PRESENCE and SHAPE inside the write path
    (`APPROVAL_REQUIRED_ACTIONS`); obtaining it is this surface's job, because deciding
    needs the human roster and — for a HITL action — a human. So this resolves the
    acting roster human and attests how the authority was held.

    A missing human is a REFUSAL, not a warning. That is a behaviour change from Mode
    1's advisory checks, and it is not mine to soften: core raises on a missing or
    malformed approval for these three actions, so the only choice this surface has is
    between a clean refusal with a remedy and an exception. An agent with no human
    behind it files an outcome-proposal instead.
    """
    import os as _os

    try:
        from otaman_core.human_roster import load_human_roster, resolve_roster_human
    except Exception:  # noqa: BLE001 - no roster reader → cannot attest anything
        return None, "this otaman-core does not carry the human roster reader"
    who = _os.environ.get("OTAMAN_HUMAN")
    try:
        entry = resolve_roster_human(load_human_roster(root / "platform.yaml"), who)
    except Exception as exc:  # noqa: BLE001 - an unreadable roster cannot authorize
        return None, f"the human roster could not be read ({type(exc).__name__})"
    if entry is None:
        named = f" (OTAMAN_HUMAN={who!r})" if who else " (OTAMAN_HUMAN is unset)"
        return None, (
            f"`otaman outcome {action}` records an approval, and no acting human "
            f"resolves from this program's human-roster{named}.\n"
            "  Run it from the human console, or set OTAMAN_HUMAN to a roster name.\n"
            "  An agent with no human behind it files an outcome-proposal instead."
        )
    hats = ACTION_HATS.get(action, ())
    held = frozenset(str(r).lower() for r in (getattr(entry, "roles", None) or []))
    via = "hat" if held_hat(held, hats) else "roster-role"
    return {
        "by": entry.name,
        "at": bus_messages.utc_now_iso(),
        "via": via,
        "spec": APPROVAL_SPEC,
    }, ""


def _solution(root: Path, solution_id: str) -> dict | None:
    """The solution record, read THROUGH the contract (the cross-register invariant).

    `accept-cost` and `choose` refuse a solution that belongs to another outcome or is
    Discarded. The contract takes one register at a time, so this invariant stays in the
    surface — core flagged that to spec-agent for the delta — but the READ is the
    contract's, so no verb opens a register file itself any more.
    """
    core = _contract()
    if core is None:
        return None
    path = resolve_registry_path(root, "solutions")
    if path is None or not path.is_file():
        return None
    register = core.load_register(path, records_key="solutions")
    return core.get(register, solution_id)


def _hat_note(action: str, approval: dict[str, Any], root: Path) -> str:
    """`hat: cto` for the transition note — which hat, and the one actually HELD.

    Not `ACTION_HATS[action][0]`: a founder holds `founder`, not `cto`, and a log that
    named the first acceptable hat would attribute the decision to a hat its author does
    not wear. D2's founder-mode scenario is read off this note, so it has to be true.
    """
    hats, _ = acting_hats(root)
    held = held_hat(hats, ACTION_HATS.get(action, ()))
    if approval.get("via") == "hat" and held:
        return f"hat: {held}"
    return f"authority: {approval.get('via', 'unknown')}"


def _load(root: Path) -> tuple[Path, Any] | int:
    """``(path, Register)`` — the register as CORE loads it (rac 1.2).

    `load_register` is the contract's read: a ruamel round-trip, so the human's comments
    and key order survive the write that follows. Returns the `Register` rather than the
    raw dict, because every mutation below now goes through `apply_transition` /
    `create_record` and those take the register.
    """
    core = _contract()
    if core is None:
        # The exit CODE, not None: a contract-less bundle is a refusal (2), and the
        # read path must not report it as the generic error (1) the unresolvable
        # registry home below is.
        return _no_contract()
    path = resolve_registry_path(root, "outcomes")
    if path is None:
        return _bail(
            "Cannot locate outcomes.yaml — no business repo found.\n"
            "  Set program.registries.strategy_repo in platform.yaml (or OTAMAN_STRATEGY_DIR)."
        )
    return path, access.open_register(core, path, records_key="outcomes")


def _save(path: Path, register: Any, *, validate: bool = True) -> int:
    """Write through the contract, after the same pydantic gate as before.

    The validation stays on this side deliberately: `save_register` is the serializer,
    and losing the Appendix-A check at the one place that touches every write would make
    the chokepoint the place that stopped checking. (rac 1.3 moves schema validation into
    the contract; until then this is the gate.)
    """
    core = _contract()
    if core is None:
        return _no_contract()
    if validate:
        try:
            OutcomeRegistry.model_validate(register.data)
        except Exception as exc:
            return _bail(f"Validation failed; refusing to write outcomes.yaml:\n{exc}", code=2)
    core.save_register(register, path)
    return 0


def _find_outcome(register: Any, outcome_id: str) -> dict | None:
    core = _contract()
    if core is None:
        return None
    return core.get(register, outcome_id)


def _ctx(root: Path):
    actor = resolve_operating_actor()
    try:
        platform = load_program_extensions(root / "platform.yaml")
    except Exception:
        from otaman_cli.registries.platform_ext import ProgramExtensions

        platform = ProgramExtensions()
    roles = resolve_roles(actor, platform)
    return actor, roles, platform


# ---------------------------------------------------------------------------
# add


def cmd_add(args: dict[str, Any]) -> int:
    """`otaman outcome add` — non-interactive form takes flags; required:
    --id JTBD-N-slug --as-a P --i-want-to T --incremental-outcome T --so-i-can T
    """
    root = find_project_root()
    if not root:
        return _bail(not_in_project_message())
    actor, roles, _ = _ctx(root)
    authz_advisory("outcome.add", actor, roles)

    required = ("id", "as_a", "i_want_to", "incremental_outcome", "so_i_can")
    missing = [k for k in required if not args.get(k)]
    if missing:
        return _bail(
            "Missing required flag(s): " + ", ".join(f"--{k.replace('_', '-')}" for k in missing)
        )

    loaded = _load(root)
    if isinstance(loaded, int):
        return loaded
    path, register = loaded
    core = _contract()

    if _find_outcome(register, args["id"]):
        return _bail(f"Outcome already exists: {args['id']}", code=1)

    today = bus_messages.utc_now_iso()[:10]  # YYYY-MM-DD
    new_entry: dict[str, Any] = {
        "id": args["id"],
        "category": args.get("category", "") or "",
        "persona": args.get("persona"),
        "statement": {
            "as-a": args["as_a"],
            "i-want-to": args["i_want_to"],
            "incremental-outcome": args["incremental_outcome"],
            "so-i-can": args["so_i_can"],
        },
        "status": "Drafting",
        "priority": args.get("priority", "P2") or "P2",
        "impact": args.get("impact"),
        "estimate-requested": False,
        "chosen-solution": None,
        "cost-accepted": None,
        "release": args.get("release"),
        "product-notes": args.get("product_notes", "") or "",
        "created": today,
        "updated": today,
        "transitions": [
            make_transition(actor=actor, action="create", to="Drafting", note=args.get("note")),
        ],
    }
    if args.get("ultimate_outcome"):
        new_entry["statement"]["ultimate-outcome"] = args["ultimate_outcome"]

    # The one path that invents a record — core refuses a blank or duplicate id, which
    # is what makes `add` a contract method rather than a transition.
    core.create_record(register, new_entry)
    rc = _save(path, register)
    if rc != 0:
        return rc

    UI.ok(f"Added outcome: {args['id']} (status: Drafting)")
    UI.muted(f"File: {path}")
    return 0


# ---------------------------------------------------------------------------
# list / show / history


def cmd_list(args: dict[str, Any]) -> int:
    root = find_project_root()
    if not root:
        return _bail(not_in_project_message())
    loaded = _load(root)
    if isinstance(loaded, int):
        return loaded
    _, register = loaded

    outcomes = register.records()
    status_filter = args.get("status")
    priority_filter = args.get("priority")
    category_filter = args.get("category")
    persona_filter = args.get("persona")

    def _match(o: dict) -> bool:
        if status_filter and o.get("status") != status_filter:
            return False
        if priority_filter and o.get("priority") != priority_filter:
            return False
        if category_filter and o.get("category") != category_filter:
            return False
        if persona_filter and o.get("persona") != persona_filter:
            return False
        return True

    filtered = [o for o in outcomes if _match(o)]
    if not filtered:
        print("No outcomes match.")
        return 0

    UI.header("Outcomes")
    for o in filtered:
        line = (
            f"  {o.get('id')}   "
            f"{o.get('status', '?'):<12}  "
            f"{o.get('priority', '--')}  "
            f"impact={o.get('impact') or '-'}  "
            f"persona={o.get('persona') or '-'}  "
            f"chosen={o.get('chosen-solution') or '-'}  "
            f"est-req={o.get('estimate-requested', False)}"
        )
        print(line)
    print()
    UI.muted(f"Total: {len(filtered)} (of {len(outcomes)} in registry)")
    return 0


def cmd_show(args: dict[str, Any]) -> int:
    root = find_project_root()
    if not root:
        return _bail(not_in_project_message())
    loaded = _load(root)
    if isinstance(loaded, int):
        return loaded
    _, register = loaded

    outcome = _find_outcome(register, args["id"])
    if not outcome:
        return _bail(f"Outcome not found: {args['id']}")

    UI.header(f"Outcome: {outcome['id']}")
    print(f"  Status:           {outcome.get('status')}")
    print(f"  Priority:         {outcome.get('priority')}")
    print(f"  Impact:           {outcome.get('impact') or '-'}")
    print(f"  Category:         {outcome.get('category') or '-'}")
    print(f"  Persona:          {outcome.get('persona') or '-'}")
    print(f"  Release:          {outcome.get('release') or '-'}")
    print(f"  Estimate-req'd:   {outcome.get('estimate-requested')}")
    print(f"  Chosen-solution:  {outcome.get('chosen-solution') or '-'}")
    print(f"  Cost-accepted:    {outcome.get('cost-accepted')}")
    print()
    stmt = outcome.get("statement") or {}
    print("  JTBD statement")
    print(f"    As a       {stmt.get('as-a')}")
    print(f"    I want to  {stmt.get('i-want-to')}")
    print(f"    Outcome    {stmt.get('incremental-outcome')}")
    print(f"    So I can   {stmt.get('so-i-can')}")
    if stmt.get("ultimate-outcome"):
        print(f"    Ultimate   {stmt.get('ultimate-outcome')}")
    if outcome.get("product-notes"):
        print()
        print("  Product notes")
        for line in str(outcome["product-notes"]).splitlines() or [outcome["product-notes"]]:
            print(f"    {line}")
    print()
    UI.muted(f"created: {outcome.get('created')}  updated: {outcome.get('updated')}")
    return 0


def cmd_history(args: dict[str, Any]) -> int:
    root = find_project_root()
    if not root:
        return _bail(not_in_project_message())
    loaded = _load(root)
    if isinstance(loaded, int):
        return loaded
    _, register = loaded
    outcome = _find_outcome(register, args["id"])
    if not outcome:
        return _bail(f"Outcome not found: {args['id']}")

    transitions = outcome.get("transitions") or []
    UI.header(f"History: {outcome['id']}")
    if not transitions:
        print("  (no transitions)")
        return 0
    print(f"  {'AT':<22}  {'BY':<16}  {'ACTION':<20}  FROM → TO")
    for t in transitions:
        at = str(t.get("at", "?"))[:22]
        by = str(t.get("by", "?"))[:16]
        action = str(t.get("action", "?"))[:20]
        from_ = t.get("from", "-")
        to = t.get("to", "-")
        print(f"  {at:<22}  {by:<16}  {action:<20}  {from_} → {to}")
        if t.get("note"):
            print(f"    note: {t['note']}")
    return 0


# ---------------------------------------------------------------------------
# Status mutators (promote/demote/retire/accept-cost/reject-cost/request-estimate)


def _emit_bus(root: Path, msg: dict) -> None:
    active_dir, _ = _resolve_bus_paths(root)
    bus_messages.emit(msg, active_dir)


def _mutate_status(args: dict[str, Any], op: str, action: str, target: str | None = None) -> int:
    """Shared helper for promote/demote/retire."""
    root = find_project_root()
    if not root:
        return _bail(not_in_project_message())
    actor, roles, _ = _ctx(root)
    authz_advisory(op, actor, roles)

    loaded = _load(root)
    if isinstance(loaded, int):
        return loaded
    path, register = loaded
    core = _contract()
    outcome = _find_outcome(register, args["id"])
    if not outcome:
        return _bail(f"Outcome not found: {args['id']}")

    current = OutcomeStatus(outcome.get("status", "Drafting"))

    if action == "retire":
        new_status = OutcomeStatus.RETIRED
    elif action == "promote":
        nxt = promote_target(current)
        if nxt is None:
            return _bail(f"Cannot promote from terminal state: {current.value}")
        new_status = nxt
    elif action == "demote":
        prv = demote_target(current)
        if prv is None:
            return _bail(f"Cannot demote from initial state: {current.value}")
        new_status = prv
    else:
        return _bail(f"Internal error: unknown status action {action!r}", code=2)

    from_value = current.value
    to_value = new_status.value
    core.apply_transition(
        register,
        outcome["id"],
        action=action,
        by=actor,
        at=bus_messages.utc_now_iso(),
        to_status=to_value,
        fields={"updated": bus_messages.utc_now_iso()[:10]},
        note=args.get("reason"),
    )
    rc = _save(path, register)
    if rc != 0:
        return rc

    msg = bus_messages.build_outcome_status_changed(outcome, from_value, to_value, actor, action)
    _emit_bus(root, msg)
    UI.ok(f"Outcome {outcome['id']}: {from_value} → {to_value}")
    UI.muted("Bus signal: outcome-status-changed")
    return 0


def cmd_promote(args):
    return _mutate_status(args, "outcome.promote", "promote")


def cmd_demote(args):
    return _mutate_status(args, "outcome.demote", "demote")


def cmd_retire(args):
    return _mutate_status(args, "outcome.retire", "retire")


def cmd_request_estimate(args: dict[str, Any]) -> int:
    root = find_project_root()
    if not root:
        return _bail(not_in_project_message())
    actor, roles, _ = _ctx(root)
    authz_advisory("outcome.request-estimate", actor, roles)

    loaded = _load(root)
    if isinstance(loaded, int):
        return loaded
    path, register = loaded
    core = _contract()
    outcome = _find_outcome(register, args["id"])
    if not outcome:
        return _bail(f"Outcome not found: {args['id']}")
    if outcome.get("estimate-requested"):
        UI.muted(f"Already marked estimate-requested: {outcome['id']}")
        return 0

    # No status change: `to_status=None` is the contract's way of saying so, which is
    # why core takes it as a separate parameter from `fields`.
    core.apply_transition(
        register,
        outcome["id"],
        action="request-estimate",
        by=actor,
        at=bus_messages.utc_now_iso(),
        fields={
            "estimate-requested": True,
            "updated": bus_messages.utc_now_iso()[:10],
        },
        note=args.get("reason"),
    )
    rc = _save(path, register)
    if rc != 0:
        return rc

    _emit_bus(root, bus_messages.build_outcome_estimate_requested(outcome, actor))
    UI.ok(f"Marked estimate-requested: {outcome['id']}")
    UI.muted("Bus signal: outcome-estimate-requested → cto-agent")
    return 0


#: What a team-mode operator is told when they try to fund and choose in one keystroke.
#: Names BOTH verbs and whose they are, because the refusal's job is to route the work,
#: not merely to decline it (D2's scenario requires the refusal to name the two verbs).
_SPLIT_REFUSAL = (
    "choosing a solution and funding it are different hats, and this program's hats are "
    "held by different people (team-mode).\n"
    "  `otaman outcome accept-cost {id} --solution {sol}` would do both in one write.\n"
    "  Run them separately:\n"
    "    otaman outcome choose {id} --solution {sol}   # the CTO's call\n"
    "    otaman outcome accept-cost {id}               # the CEO's call\n"
    "  (One human holding both hats — founder-mode — may combine them; the log still "
    "records both decisions.)"
)


def cmd_accept_cost(args: dict[str, Any]) -> int:
    """`otaman outcome accept-cost <id> [--solution <SOL-id>]` — fund the outcome.

    The choose/fund split (rac 2.1, D2). `--solution` makes this the COMBINED
    invocation: it chooses the solution and funds it. Two hats own those decisions —
    the CTO chooses, the CEO funds — so the combined form is available only when ONE
    human holds both (founder-mode), and even then the log records `choose` and
    `accept-cost` as separate transitions with their own hats. "The keystroke may
    collapse, the record may not."

    Without `--solution` it funds what `otaman outcome choose` already chose, which is
    the team-mode path: the CTO's decision is already in the log, with their hat on it.
    """
    root = find_project_root()
    if not root:
        return _bail(not_in_project_message())
    actor, roles, _ = _ctx(root)
    authz_advisory("outcome.accept-cost", actor, roles)

    loaded = _load(root)
    if isinstance(loaded, int):
        return loaded
    path, register = loaded
    core = _contract()
    outcome = _find_outcome(register, args["id"])
    if not outcome:
        return _bail(f"Outcome not found: {args['id']}")

    # WHO before WHAT: the acting human is resolved before the request is judged,
    # because the mode below is read off that human's hats — telling an unidentified
    # caller "this program is team-mode" would report the default as a finding. The
    # second approval (the choose, further down) is the same human under a different
    # hat; both are obtained before EITHER write, since half of a combined decision is
    # worse than none of it.
    fund_approval, refusal = _approval(root, "accept-cost")
    if fund_approval is None:
        return _bail(refusal, code=2)

    # --- the split (D2) -----------------------------------------------------
    combined = bool(args.get("solution"))
    chosen = outcome.get("chosen-solution")
    mode = operating_mode(root)
    if combined and mode == "team":
        return _bail(
            _SPLIT_REFUSAL.format(id=outcome["id"], sol=args["solution"]),
            code=2,
        )
    if not combined and not chosen:
        return _bail(
            f"{outcome['id']} has no chosen-solution to fund.\n"
            f"  otaman outcome choose {outcome['id']} --solution <SOL-id>\n"
            "  then accept-cost funds what was chosen.",
            code=2,
        )
    solution_id = args.get("solution") or chosen

    # Locate the solution for the payload data — through the contract (rac 1.2).
    solution = _solution(root, solution_id)
    if solution is None:
        return _bail(f"Solution not found in solutions.yaml: {solution_id}")
    if solution.get("outcome-id") != outcome["id"]:
        return _bail(
            f"Solution {solution_id} belongs to outcome "
            f"{solution.get('outcome-id')!r}, not {outcome['id']!r}"
        )
    if combined and solution.get("status") == "Discarded":
        # The same refusal `choose` makes: the combined form performs a choose, so it
        # cannot accept what choose would reject.
        return _bail(f"Cannot choose a discarded solution: {solution_id}")

    from_status = outcome.get("status", "Backlog")

    # D1/1.3 — refuse below Backlog, and refuse BEFORE writing anything.
    #
    # The half-apply this replaces: from Drafting, the code below set
    # cost-accepted=True and chosen-solution but left status at Drafting,
    # because the status bump was conditional on `from_status == "Backlog"`. The
    # result was an outcome with money accepted and an incomplete statement —
    # JTBD-138 exactly. The strict validator permitted it; this change
    # supersedes that permission.
    #
    # The reason comes from check_action, not from a string here, so the CLI and
    # the console refuse with the identical sentence (D2).
    from otaman_cli.registries.outcomes import check_action

    verdict = check_action("accept-cost", from_status)
    if not verdict.allowed:
        return _bail(verdict.reason)

    choose_approval = None
    if combined and solution_id != chosen:
        choose_approval, refusal = _approval(root, "choose")
        if choose_approval is None:
            return _bail(refusal, code=2)

    # The keystroke may collapse; the record may not (D2). A combined founder-mode
    # invocation appends `choose` with the CTO hat and `accept-cost` with the CEO hat —
    # two entries, never one. core composes them on the same register and `save_register`
    # writes once, so the pair lands together or not at all.
    if choose_approval is not None:
        core.apply_transition(
            register,
            outcome["id"],
            action="choose",
            by=actor,
            at=bus_messages.utc_now_iso(),
            fields={"chosen-solution": solution_id},
            approval=choose_approval,
            note=f"chose {solution_id} ({_hat_note('choose', choose_approval, root)})",
        )

    note = args.get("reason") or _hat_note("accept-cost", fund_approval, root)
    fields: dict[str, Any] = {
        "cost-accepted": True,
        "updated": bus_messages.utc_now_iso()[:10],
    }
    # `chosen-solution` is NOT in this transition's fields. Every path that reaches
    # here with a different solution recorded it in the `choose` above; the others
    # (team-mode, or the combined form naming the existing choice) already hold the
    # right value, and re-writing it would put a no-op field change in the audit.
    core.apply_transition(
        register,
        outcome["id"],
        action="accept-cost",
        by=actor,
        at=bus_messages.utc_now_iso(),
        to_status="Approved" if from_status == "Backlog" else None,
        fields=fields,
        approval=fund_approval,
        note=note,
    )
    rc = _save(path, register)
    if rc != 0:
        return rc

    _emit_bus(root, bus_messages.build_outcome_cost_accepted(outcome, solution, actor))
    UI.ok(f"Accepted cost: {outcome['id']} → chosen-solution: {solution_id}")
    if choose_approval is not None:
        UI.muted(f"  recorded two decisions: choose + accept-cost (founder-mode, {mode})")
    return 0


def cmd_choose(args: dict[str, Any]) -> int:
    """`otaman outcome choose <id> --solution <SOL-id>` — mark the outcome's
    chosen solution (team-mode 2.4b, CTO hat). Sets ``chosen-solution`` + a
    ``choose`` transition; does NOT accept the cost (that's accept-cost) or touch
    the siblings (they stay Considering). Canon: cofounder's solutions-draft
    team-mode. Hat check advisory at Mode 1."""
    root = find_project_root()
    if not root:
        return _bail(not_in_project_message())
    if not args.get("solution"):
        return _bail("--solution <SOL-id> is required")
    actor, _roles, _ = _ctx(root)
    hat_advisory("outcome.choose", ("cto",), root)

    loaded = _load(root)
    if isinstance(loaded, int):
        return loaded
    path, register = loaded
    core = _contract()
    outcome = _find_outcome(register, args["id"])
    if not outcome:
        return _bail(f"Outcome not found: {args['id']}")

    solution = _solution(root, args["solution"])
    if solution is None:
        return _bail(f"Solution not found in solutions.yaml: {args['solution']}")
    if solution.get("outcome-id") != outcome["id"]:
        return _bail(
            f"Solution {args['solution']} belongs to outcome "
            f"{solution.get('outcome-id')!r}, not {outcome['id']!r}"
        )
    if solution.get("status") == "Discarded":
        return _bail(f"Cannot choose a discarded solution: {args['solution']}")

    status = outcome.get("status", "Backlog")
    # Read the PREVIOUS choice before overwriting it (cofounder-agent
    # 20260919T232356). The transition recorded only `note: chose <SOL>`, so a
    # re-choose left two entries each naming only their own target — a reader
    # could not tell what the previous choice was except by inferring from
    # order. The action stays `choose`, which is what makes the decision
    # queryable; the old/new pair is what makes it auditable. Both, not either.
    approval, refusal = _approval(root, "choose")
    if approval is None:
        return _bail(refusal, code=2)
    # `chosen-solution` is the ONLY field in this transition, and deliberately so: core
    # records the field/old/new triple only for a single-field transition, and that triple
    # is what keeps the previous choice auditable (cofounder-agent 20260919T232356).
    #
    # `updated` therefore gets its own `update-field` entry below. The alternative —
    # putting it in this transition — collapses the triple, and dropping it is not
    # available either: the schema REQUIRES `updated` on an outcome. One bookkeeping row
    # in the log is the cheapest of the three. Asked core to record a triple per changed
    # field, which collapses the two back into one.
    core.apply_transition(
        register,
        outcome["id"],
        action="choose",
        by=actor,
        at=bus_messages.utc_now_iso(),
        fields={"chosen-solution": args["solution"]},
        approval=approval,
        note=f"chose {args['solution']} ({_hat_note('choose', approval, root)})",
    )
    core.apply_transition(
        register,
        outcome["id"],
        action="update-field",
        by=actor,
        at=bus_messages.utc_now_iso(),
        fields={"updated": bus_messages.utc_now_iso()[:10]},
    )
    rc = _save(path, register)
    if rc != 0:
        return rc

    _emit_bus(
        root,
        bus_messages.build_outcome_status_changed(outcome, status, status, actor, "choose"),
    )
    UI.ok(f"Chose solution: {outcome['id']} → chosen-solution: {args['solution']}")
    return 0


def cmd_reject_cost(args: dict[str, Any]) -> int:
    root = find_project_root()
    if not root:
        return _bail(not_in_project_message())
    actor, roles, _ = _ctx(root)
    authz_advisory("outcome.reject-cost", actor, roles)

    loaded = _load(root)
    if isinstance(loaded, int):
        return loaded
    path, register = loaded
    core = _contract()
    outcome = _find_outcome(register, args["id"])
    if not outcome:
        return _bail(f"Outcome not found: {args['id']}")

    # If a previous accept-cost set chosen-solution, clear it on rejection
    rejected_solution = outcome.get("chosen-solution")
    approval, refusal = _approval(root, "reject-cost")
    if approval is None:
        return _bail(refusal, code=2)
    core.apply_transition(
        register,
        outcome["id"],
        action="reject-cost",
        by=actor,
        at=bus_messages.utc_now_iso(),
        fields={
            "cost-accepted": False,
            "chosen-solution": None,
            "updated": bus_messages.utc_now_iso()[:10],
        },
        approval=approval,
        note=args.get("reason") or _hat_note("reject-cost", approval, root),
    )
    rc = _save(path, register)
    if rc != 0:
        return rc

    _emit_bus(
        root,
        bus_messages.build_outcome_cost_rejected(
            outcome,
            actor,
            note=args.get("reason"),
            rejected_solution=rejected_solution,
        ),
    )
    UI.ok(f"Rejected cost: {outcome['id']}")
    UI.muted("Bus signal: outcome-cost-rejected → cto-agent")
    return 0


# ---------------------------------------------------------------------------
# Dispatch


_ACTIONS = {
    "add": cmd_add,
    "list": cmd_list,
    "show": cmd_show,
    "history": cmd_history,
    "promote": cmd_promote,
    "demote": cmd_demote,
    "retire": cmd_retire,
    "request-estimate": cmd_request_estimate,
    "accept-cost": cmd_accept_cost,
    "reject-cost": cmd_reject_cost,
    "choose": cmd_choose,
}


def dispatch(action: str, args: dict[str, Any]) -> int:
    fn = _ACTIONS.get(action)
    if fn is None:
        UI.error(f"Unknown outcome action: {action}")
        UI.muted("Available: " + ", ".join(sorted(_ACTIONS.keys())))
        return 2
    return fn(args)


__all__ = ["dispatch"]
