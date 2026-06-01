"""`otaman solution <action>` command implementation (tasks 3.1-3.6).

Dispatch table:
    add               — propose a new solution
    list              — enumerate solutions (--outcome filter)
    show <id>         — full detail
    history <id>      — render transitions[]
    propose <id>      — mark CTO recommendation; emit outcome-estimates-ready
    promote-to-complete <id> — set Complete (must equal parent.chosen-solution)
    discard <id>      — set Discarded
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
from otaman_cli.registries.platform_ext import load_program_extensions
from otaman_cli.registries.roles import (
    authz_advisory,
    resolve_operating_actor,
    resolve_roles,
)
from otaman_cli.registries.solutions import SolutionRegistry, SolutionStatus
from otaman_cli.registries.transitions import append_transition, make_transition


def _bail(msg: str, code: int = 1) -> int:
    UI.error(msg)
    return code


def _load(root: Path) -> tuple[Path, Any] | None:
    path = resolve_registry_path(root, "solutions")
    if path is None:
        _bail(
            "Cannot locate solutions.yaml — no business repo found.\n"
            "  Set OTAMAN_BUSINESS_DIR, or add a repo with owner: cpo-agent in platform.yaml."
        )
        return None
    raw = yaml_load(path)
    if not isinstance(raw, dict):
        raw = {}
    if "solutions" not in raw or raw["solutions"] is None:
        raw["solutions"] = []
    return path, raw


def _save(path: Path, raw: dict, *, validate: bool = True) -> int:
    if validate:
        try:
            SolutionRegistry.model_validate(raw)
        except Exception as exc:
            return _bail(f"Validation failed; refusing to write solutions.yaml:\n{exc}", code=2)
    yaml_dump(raw, path)
    return 0


def _find(raw: dict, sol_id: str) -> dict | None:
    for s in raw.get("solutions", []):
        if s.get("id") == sol_id:
            return s
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


def _emit_bus(root: Path, msg: dict) -> None:
    active_dir, _ = _resolve_bus_paths(root)
    bus_messages.emit(msg, active_dir)


# ---------------------------------------------------------------------------
# add


def cmd_add(args: dict[str, Any]) -> int:
    root = find_project_root()
    if not root:
        return _bail("Not in an otaman project")
    actor, roles, platform = _ctx(root)
    authz_advisory("solution.add", actor, roles)

    required = ("id", "outcome", "description")
    missing = [k for k in required if not args.get(k)]
    if missing:
        return _bail(
            "Missing required flag(s): " + ", ".join(f"--{k}" for k in missing)
        )

    loaded = _load(root)
    if loaded is None:
        return 1
    path, raw = loaded

    if _find(raw, args["id"]):
        return _bail(f"Solution already exists: {args['id']}")

    # Derive effort-days from t-shirt size if provided
    t_shirt = args.get("t_shirt")
    effort_days = args.get("effort_days")
    if t_shirt and effort_days is None:
        effort_days = platform.t_shirt_scale.get(t_shirt)
        if effort_days is None:
            return _bail(
                f"t-shirt {t_shirt!r} not in platform.yaml program.t-shirt-scale "
                f"({sorted(platform.t_shirt_scale.keys())})"
            )

    today = bus_messages.utc_now_iso()[:10]
    new_entry: dict[str, Any] = {
        "id": args["id"],
        "outcome-id": args["outcome"],
        "release": args.get("release"),
        "description": args["description"],
        "t-shirt": t_shirt,
        "effort-days": effort_days,
        "dependencies": args.get("dependencies") or [],
        "pros": args.get("pros") or [],
        "cons": args.get("cons") or [],
        "cto-notes": args.get("cto_notes", "") or "",
        "status": "Considering",
        "created": today,
        "updated": today,
        "transitions": [
            make_transition(actor=actor, action="create", to="Considering", note=args.get("note")),
        ],
    }
    raw["solutions"].append(new_entry)
    rc = _save(path, raw)
    if rc != 0:
        return rc

    UI.ok(f"Added solution: {args['id']} for outcome {args['outcome']}")
    UI.muted(f"  size: {t_shirt or '-'}  effort-days: {effort_days or '-'}  status: Considering")
    UI.muted(f"File: {path}")
    # Auto-triage integration deferred to Phase 3 (task 7.2)
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
    solutions = raw.get("solutions", [])
    outcome_filter = args.get("outcome")
    status_filter = args.get("status")
    release_filter = args.get("release")

    def _match(s: dict) -> bool:
        if outcome_filter and s.get("outcome-id") != outcome_filter:
            return False
        if status_filter and s.get("status") != status_filter:
            return False
        if release_filter and s.get("release") != release_filter:
            return False
        return True

    filtered = [s for s in solutions if _match(s)]
    if not filtered:
        print("No solutions match.")
        return 0

    UI.header("Solutions")
    for s in filtered:
        line = (
            f"  {s.get('id')}   "
            f"outcome={s.get('outcome-id')}   "
            f"{s.get('status', '?'):<12}  "
            f"size={s.get('t-shirt') or '-':<14}  "
            f"days={s.get('effort-days') or '-'}  "
            f"release={s.get('release') or '-'}"
        )
        print(line)
    print()
    UI.muted(f"Total: {len(filtered)} (of {len(solutions)} in registry)")
    return 0


def cmd_show(args: dict[str, Any]) -> int:
    root = find_project_root()
    if not root:
        return _bail("Not in an otaman project")
    loaded = _load(root)
    if loaded is None:
        return 1
    _, raw = loaded
    s = _find(raw, args["id"])
    if not s:
        return _bail(f"Solution not found: {args['id']}")

    UI.header(f"Solution: {s['id']}")
    print(f"  Outcome:        {s.get('outcome-id')}")
    print(f"  Status:         {s.get('status')}")
    print(f"  T-shirt:        {s.get('t-shirt') or '-'}")
    print(f"  Effort-days:    {s.get('effort-days') or '-'}")
    print(f"  Release:        {s.get('release') or '-'}")
    print()
    print("  Description")
    for line in str(s.get("description") or "").splitlines() or [s.get("description", "")]:
        print(f"    {line}")
    if s.get("pros"):
        print()
        print("  Pros")
        for p in s["pros"]:
            print(f"    • {p}")
    if s.get("cons"):
        print()
        print("  Cons")
        for c in s["cons"]:
            print(f"    • {c}")
    if s.get("dependencies"):
        print()
        print("  Dependencies")
        for d in s["dependencies"]:
            ref = d.get("ref") or d.get("name") or "?"
            print(f"    [{d.get('kind')}] {ref}")
    if s.get("cto-notes"):
        print()
        print("  CTO notes")
        for line in str(s["cto-notes"]).splitlines() or [s["cto-notes"]]:
            print(f"    {line}")
    print()
    UI.muted(f"created: {s.get('created')}  updated: {s.get('updated')}")
    return 0


def cmd_history(args: dict[str, Any]) -> int:
    root = find_project_root()
    if not root:
        return _bail("Not in an otaman project")
    loaded = _load(root)
    if loaded is None:
        return 1
    _, raw = loaded
    s = _find(raw, args["id"])
    if not s:
        return _bail(f"Solution not found: {args['id']}")
    transitions = s.get("transitions") or []
    UI.header(f"History: {s['id']}")
    if not transitions:
        print("  (no transitions)")
        return 0
    print(f"  {'AT':<22}  {'BY':<16}  {'ACTION':<22}  FROM → TO")
    for t in transitions:
        at = str(t.get("at", "?"))[:22]
        by = str(t.get("by", "?"))[:16]
        action = str(t.get("action", "?"))[:22]
        from_ = t.get("from", "-")
        to = t.get("to", "-")
        print(f"  {at:<22}  {by:<16}  {action:<22}  {from_} → {to}")
        if t.get("note"):
            print(f"    note: {t['note']}")
    return 0


# ---------------------------------------------------------------------------
# Status mutators


def cmd_propose(args: dict[str, Any]) -> int:
    """`otaman solution propose <id>` — mark CTO's recommended solution; emits
    outcome-estimates-ready for the parent outcome (Appendix F.3 schema).
    Does NOT change the solution's own status (propose action per Appendix B.6).
    """
    root = find_project_root()
    if not root:
        return _bail("Not in an otaman project")
    actor, roles, _ = _ctx(root)
    authz_advisory("solution.propose", actor, roles)

    loaded = _load(root)
    if loaded is None:
        return 1
    path, raw = loaded
    s = _find(raw, args["id"])
    if not s:
        return _bail(f"Solution not found: {args['id']}")

    append_transition(
        s,
        make_transition(actor=actor, action="propose", note=args.get("reason")),
    )
    s["updated"] = bus_messages.utc_now_iso()[:10]
    rc = _save(path, raw)
    if rc != 0:
        return rc

    # Gather all solutions for the same outcome to include in the estimates-ready msg
    sibling_solutions = [
        x for x in raw.get("solutions", []) if x.get("outcome-id") == s.get("outcome-id")
    ]
    msg = bus_messages.build_outcome_estimates_ready(
        outcome_id=s.get("outcome-id"),
        solutions=sibling_solutions,
        from_actor=actor,
        recommended=args["id"],
    )
    _emit_bus(root, msg)
    UI.ok(f"Proposed solution: {args['id']} (recommended for {s.get('outcome-id')})")
    UI.muted("Bus signal: outcome-estimates-ready → cpo-agent")
    return 0


def cmd_promote_to_complete(args: dict[str, Any]) -> int:
    root = find_project_root()
    if not root:
        return _bail("Not in an otaman project")
    actor, roles, _ = _ctx(root)
    authz_advisory("solution.promote-to-complete", actor, roles)

    loaded = _load(root)
    if loaded is None:
        return 1
    path, raw = loaded
    s = _find(raw, args["id"])
    if not s:
        return _bail(f"Solution not found: {args['id']}")

    # Validation per B.7 rule 9: parent outcome's chosen-solution must equal this id
    outcome_path = resolve_registry_path(root, "outcomes")
    if outcome_path and outcome_path.is_file():
        oraw = yaml_load(outcome_path) or {}
        parent = next(
            (o for o in oraw.get("outcomes", []) if o.get("id") == s.get("outcome-id")),
            None,
        )
        if parent is None:
            return _bail(f"Parent outcome not found: {s.get('outcome-id')}")
        if parent.get("chosen-solution") != s["id"]:
            return _bail(
                f"Cannot promote to Complete: outcome {parent['id']}.chosen-solution="
                f"{parent.get('chosen-solution')!r}, expected {s['id']!r}.\n"
                f"  Run `otaman outcome accept-cost {parent['id']} --solution {s['id']}` first."
            )

    from_status = s.get("status", "In-Progress")
    s["status"] = "Complete"
    s["updated"] = bus_messages.utc_now_iso()[:10]
    append_transition(
        s,
        make_transition(
            actor=actor,
            action="promote-to-complete",
            from_=from_status,
            to="Complete",
            note=args.get("reason"),
        ),
    )
    rc = _save(path, raw)
    if rc != 0:
        return rc

    _emit_bus(
        root,
        bus_messages.build_solution_status_changed(
            s, from_status, "Complete", actor, "promote-to-complete",
        ),
    )
    UI.ok(f"Solution {s['id']}: {from_status} → Complete")
    return 0


def cmd_discard(args: dict[str, Any]) -> int:
    root = find_project_root()
    if not root:
        return _bail("Not in an otaman project")
    actor, roles, _ = _ctx(root)
    authz_advisory("solution.discard", actor, roles)

    loaded = _load(root)
    if loaded is None:
        return 1
    path, raw = loaded
    s = _find(raw, args["id"])
    if not s:
        return _bail(f"Solution not found: {args['id']}")
    if s.get("status") == "Discarded":
        UI.muted(f"Already discarded: {args['id']}")
        return 0

    from_status = s.get("status", "Considering")
    s["status"] = "Discarded"
    s["updated"] = bus_messages.utc_now_iso()[:10]
    append_transition(
        s,
        make_transition(
            actor=actor,
            action="discard",
            from_=from_status,
            to="Discarded",
            note=args.get("reason"),
        ),
    )
    rc = _save(path, raw)
    if rc != 0:
        return rc

    _emit_bus(
        root,
        bus_messages.build_solution_status_changed(
            s, from_status, "Discarded", actor, "discard",
        ),
    )
    UI.ok(f"Discarded solution: {s['id']}")
    return 0


# ---------------------------------------------------------------------------
# Dispatch


_ACTIONS = {
    "add": cmd_add,
    "list": cmd_list,
    "show": cmd_show,
    "history": cmd_history,
    "propose": cmd_propose,
    "promote-to-complete": cmd_promote_to_complete,
    "discard": cmd_discard,
}


def dispatch(action: str, args: dict[str, Any]) -> int:
    fn = _ACTIONS.get(action)
    if fn is None:
        UI.error(f"Unknown solution action: {action}")
        UI.muted("Available: " + ", ".join(sorted(_ACTIONS.keys())))
        return 2
    return fn(args)


__all__ = ["dispatch"]
