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

from otaman_cli.identity import find_project_root
from otaman_cli.main import UI, _resolve_bus_paths
from otaman_cli.registries import bus_messages
from otaman_cli.registries.loader import (
    resolve_registry_path,
    yaml_dump,
    yaml_load,
)
from otaman_cli.registries.outcomes import (
    OutcomeRegistry,
    OutcomeStatus,
    demote_target,
    promote_target,
)
from otaman_cli.registries.platform_ext import load_program_extensions
from otaman_cli.registries.roles import (
    authz_advisory,
    resolve_operating_actor,
    resolve_roles,
)
from otaman_cli.registries.transitions import append_transition, make_transition


def _bail(msg: str, code: int = 1) -> int:
    UI.error(msg)
    return code


def _load(root: Path) -> tuple[Path, Any] | None:
    path = resolve_registry_path(root, "outcomes")
    if path is None:
        _bail(
            "Cannot locate outcomes.yaml — no business repo found.\n"
            "  Set OTAMAN_BUSINESS_DIR, or add a repo with owner: cpo-agent in platform.yaml."
        )
        return None
    raw = yaml_load(path)
    if not isinstance(raw, dict):
        raw = {}
    if "outcomes" not in raw or raw["outcomes"] is None:
        raw["outcomes"] = []
    return path, raw


def _save(path: Path, raw: dict, *, validate: bool = True) -> int:
    """Save with re-validation. Returns 0 on success, 2 on validation error."""
    if validate:
        try:
            OutcomeRegistry.model_validate(raw)
        except Exception as exc:
            return _bail(f"Validation failed; refusing to write outcomes.yaml:\n{exc}", code=2)
    yaml_dump(raw, path)
    return 0


def _find_outcome(raw: dict, outcome_id: str) -> dict | None:
    for o in raw.get("outcomes", []):
        if o.get("id") == outcome_id:
            return o
    return None


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
        return _bail("Not in an otaman project")
    actor, roles, _ = _ctx(root)
    authz_advisory("outcome.add", actor, roles)

    required = ("id", "as_a", "i_want_to", "incremental_outcome", "so_i_can")
    missing = [k for k in required if not args.get(k)]
    if missing:
        return _bail(
            "Missing required flag(s): " + ", ".join(f"--{k.replace('_', '-')}" for k in missing)
        )

    loaded = _load(root)
    if loaded is None:
        return 1
    path, raw = loaded

    if _find_outcome(raw, args["id"]):
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

    raw["outcomes"].append(new_entry)
    rc = _save(path, raw)
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
        return _bail("Not in an otaman project")
    loaded = _load(root)
    if loaded is None:
        return 1
    _, raw = loaded

    outcomes = raw.get("outcomes", [])
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
        return _bail("Not in an otaman project")
    loaded = _load(root)
    if loaded is None:
        return 1
    _, raw = loaded

    outcome = _find_outcome(raw, args["id"])
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
        return _bail("Not in an otaman project")
    loaded = _load(root)
    if loaded is None:
        return 1
    _, raw = loaded
    outcome = _find_outcome(raw, args["id"])
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
        return _bail("Not in an otaman project")
    actor, roles, _ = _ctx(root)
    authz_advisory(op, actor, roles)

    loaded = _load(root)
    if loaded is None:
        return 1
    path, raw = loaded
    outcome = _find_outcome(raw, args["id"])
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
    outcome["status"] = to_value
    outcome["updated"] = bus_messages.utc_now_iso()[:10]
    append_transition(
        outcome,
        make_transition(
            actor=actor,
            action=action,
            from_=from_value,
            to=to_value,
            note=args.get("reason"),
        ),
    )
    rc = _save(path, raw)
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
        return _bail("Not in an otaman project")
    actor, roles, _ = _ctx(root)
    authz_advisory("outcome.request-estimate", actor, roles)

    loaded = _load(root)
    if loaded is None:
        return 1
    path, raw = loaded
    outcome = _find_outcome(raw, args["id"])
    if not outcome:
        return _bail(f"Outcome not found: {args['id']}")
    if outcome.get("estimate-requested"):
        UI.muted(f"Already marked estimate-requested: {outcome['id']}")
        return 0

    outcome["estimate-requested"] = True
    outcome["updated"] = bus_messages.utc_now_iso()[:10]
    append_transition(
        outcome,
        make_transition(actor=actor, action="request-estimate", note=args.get("reason")),
    )
    rc = _save(path, raw)
    if rc != 0:
        return rc

    _emit_bus(root, bus_messages.build_outcome_estimate_requested(outcome, actor))
    UI.ok(f"Marked estimate-requested: {outcome['id']}")
    UI.muted("Bus signal: outcome-estimate-requested → cto-agent")
    return 0


def cmd_accept_cost(args: dict[str, Any]) -> int:
    root = find_project_root()
    if not root:
        return _bail("Not in an otaman project")
    if not args.get("solution"):
        return _bail("--solution <SOL-id> is required")
    actor, roles, _ = _ctx(root)
    authz_advisory("outcome.accept-cost", actor, roles)

    loaded = _load(root)
    if loaded is None:
        return 1
    path, raw = loaded
    outcome = _find_outcome(raw, args["id"])
    if not outcome:
        return _bail(f"Outcome not found: {args['id']}")

    # Locate the solution in solutions.yaml for the payload data
    sol_path = resolve_registry_path(root, "solutions")
    solution = None
    if sol_path and sol_path.is_file():
        sol_raw = yaml_load(sol_path) or {}
        for s in sol_raw.get("solutions", []):
            if s.get("id") == args["solution"]:
                solution = s
                break
    if solution is None:
        return _bail(f"Solution not found in solutions.yaml: {args['solution']}")
    if solution.get("outcome-id") != outcome["id"]:
        return _bail(
            f"Solution {args['solution']} belongs to outcome "
            f"{solution.get('outcome-id')!r}, not {outcome['id']!r}"
        )

    from_status = outcome.get("status", "Backlog")
    outcome["cost-accepted"] = True
    outcome["chosen-solution"] = args["solution"]
    if from_status == "Backlog":
        outcome["status"] = "Approved"
    outcome["updated"] = bus_messages.utc_now_iso()[:10]
    append_transition(
        outcome,
        make_transition(
            actor=actor,
            action="accept-cost",
            from_=from_status if outcome["status"] != from_status else None,
            to=outcome["status"] if outcome["status"] != from_status else None,
            note=args.get("reason"),
        ),
    )
    rc = _save(path, raw)
    if rc != 0:
        return rc

    _emit_bus(root, bus_messages.build_outcome_cost_accepted(outcome, solution, actor))
    UI.ok(f"Accepted cost: {outcome['id']} → chosen-solution: {args['solution']}")
    return 0


def cmd_reject_cost(args: dict[str, Any]) -> int:
    root = find_project_root()
    if not root:
        return _bail("Not in an otaman project")
    actor, roles, _ = _ctx(root)
    authz_advisory("outcome.reject-cost", actor, roles)

    loaded = _load(root)
    if loaded is None:
        return 1
    path, raw = loaded
    outcome = _find_outcome(raw, args["id"])
    if not outcome:
        return _bail(f"Outcome not found: {args['id']}")

    outcome["cost-accepted"] = False
    # If a previous accept-cost set chosen-solution, clear it on rejection
    rejected_solution = outcome.get("chosen-solution")
    outcome["chosen-solution"] = None
    outcome["updated"] = bus_messages.utc_now_iso()[:10]
    append_transition(
        outcome,
        make_transition(actor=actor, action="reject-cost", note=args.get("reason")),
    )
    rc = _save(path, raw)
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
}


def dispatch(action: str, args: dict[str, Any]) -> int:
    fn = _ACTIONS.get(action)
    if fn is None:
        UI.error(f"Unknown outcome action: {action}")
        UI.muted("Available: " + ", ".join(sorted(_ACTIONS.keys())))
        return 2
    return fn(args)


__all__ = ["dispatch"]
