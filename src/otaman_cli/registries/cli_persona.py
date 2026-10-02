"""`otaman persona <action>` command implementation (task 4.1).

Dispatch table:
    add               — register a new persona
    list              — enumerate personas
    show <id>         — full detail
    retire <id>       — mark logically retired (soft-delete)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from otaman_cli.identity import find_project_root, not_in_project_message
from otaman_cli.registries import access
from otaman_cli.registries.loader import resolve_registry_path
from otaman_cli.registries.personas import PersonaKind, PersonaRegistry
from otaman_cli.registries.platform_ext import load_program_extensions
from otaman_cli.registries.roles import (
    authz_advisory,
    resolve_operating_actor,
    resolve_roles,
)
from otaman_cli.ui import UI


def _bail(msg: str, code: int = 1) -> int:
    UI.error(msg)
    return code


def _contract():
    """core's registry access contract, or None — see `access.contract`."""
    return access.contract()


def _no_contract() -> int:
    return _bail(access.NO_CONTRACT, code=2)


def _load(root: Path) -> tuple[Path, Any] | int:
    """``(path, Register)`` — the register as CORE loads it (rac 1.2)."""
    core = _contract()
    if core is None:
        # The exit CODE, not None: a contract-less bundle is a refusal (2), and the
        # read path must not report it as the generic error (1) the unresolvable
        # registry home below is.
        return _no_contract()
    path = resolve_registry_path(root, "personas")
    if path is None:
        return _bail(
            "Cannot locate personas.yaml — no business repo found.\n"
            "  Set program.registries.strategy_repo in platform.yaml (or OTAMAN_STRATEGY_DIR)."
        )
    return path, access.open_register(core, path, records_key="personas")


def _save(path: Path, register: Any, *, validate: bool = True) -> int:
    core = _contract()
    if core is None:
        return _no_contract()
    if validate:
        try:
            PersonaRegistry.model_validate(register.data)
        except Exception as exc:
            return _bail(f"Validation failed; refusing to write personas.yaml:\n{exc}", code=2)
    core.save_register(register, path)
    return 0


def _find(register: Any, persona_id: str) -> dict | None:
    core = _contract()
    if core is None:
        return None
    return core.get(register, persona_id)


def _records(register: Any) -> list[dict]:
    data = getattr(register, "data", register)
    return list(data.get("personas") or [])


def _ctx(root: Path):
    actor = resolve_operating_actor()
    try:
        platform = load_program_extensions(root / "platform.yaml")
    except Exception:
        from otaman_cli.registries.platform_ext import ProgramExtensions

        platform = ProgramExtensions()
    roles = resolve_roles(actor, platform)
    return actor, roles, platform


def cmd_add(args: dict[str, Any]) -> int:
    root = find_project_root()
    if not root:
        return _bail(not_in_project_message())
    actor, roles, _ = _ctx(root)
    authz_advisory("persona.add", actor, roles)

    required = ("id", "name", "description", "kind")
    missing = [k for k in required if not args.get(k)]
    if missing:
        return _bail("Missing required flag(s): " + ", ".join(f"--{k}" for k in missing))

    valid_kinds = {k.value for k in PersonaKind}
    if args["kind"] not in valid_kinds:
        return _bail(f"Invalid kind: {args['kind']!r}. Must be one of: {sorted(valid_kinds)}")

    loaded = _load(root)
    if isinstance(loaded, int):
        return loaded
    path, register = loaded
    if _find(register, args["id"]):
        return _bail(f"Persona already exists: {args['id']}")

    new_entry = {
        "id": args["id"],
        "name": args["name"],
        "description": args["description"],
        "kind": args["kind"],
        "domain-prefill-source": args.get("domain_prefill_source"),
        "status": "active",
    }
    core = _contract()
    if core is None:
        return _no_contract()
    try:
        core.create_record(register, new_entry)
    except Exception as exc:  # noqa: BLE001 - blank/duplicate id is the contract's refusal
        return _bail(f"Cannot add persona {args['id']}: {exc}", code=2)
    rc = _save(path, register)
    if rc != 0:
        return rc

    UI.ok(f"Added persona: {args['id']} ({args['name']}) [active]")
    return 0


def cmd_list(args: dict[str, Any]) -> int:
    root = find_project_root()
    if not root:
        return _bail(not_in_project_message())
    loaded = _load(root)
    if isinstance(loaded, int):
        return loaded
    _, register = loaded
    personas = _records(register)
    kind_filter = args.get("kind")
    status_filter = args.get("status")

    def _match(p: dict) -> bool:
        if kind_filter and p.get("kind") != kind_filter:
            return False
        if status_filter and p.get("status", "active") != status_filter:
            return False
        return True

    filtered = [p for p in personas if _match(p)]
    if not filtered:
        print("No personas match.")
        return 0

    UI.header("Personas")
    for p in filtered:
        print(
            f"  {p.get('id'):<28}  {p.get('name'):<28}  "
            f"{p.get('kind'):<22}  {p.get('status', 'active')}"
        )
    print()
    UI.muted(f"Total: {len(filtered)} (of {len(personas)} in registry)")
    return 0


def cmd_show(args: dict[str, Any]) -> int:
    root = find_project_root()
    if not root:
        return _bail(not_in_project_message())
    loaded = _load(root)
    if isinstance(loaded, int):
        return loaded
    _, register = loaded
    p = _find(register, args["id"])
    if not p:
        return _bail(f"Persona not found: {args['id']}")

    UI.header(f"Persona: {p['id']}")
    print(f"  Name:        {p.get('name')}")
    print(f"  Kind:        {p.get('kind')}")
    print(f"  Status:      {p.get('status', 'active')}")
    print(f"  Domain:      {p.get('domain-prefill-source') or '-'}")
    print()
    print("  Description")
    for line in str(p.get("description") or "").splitlines():
        print(f"    {line}")
    return 0


def cmd_retire(args: dict[str, Any]) -> int:
    root = find_project_root()
    if not root:
        return _bail(not_in_project_message())
    actor, roles, _ = _ctx(root)
    authz_advisory("persona.retire", actor, roles)

    loaded = _load(root)
    if isinstance(loaded, int):
        return loaded
    path, register = loaded
    p = _find(register, args["id"])
    if not p:
        return _bail(f"Persona not found: {args['id']}")
    if p.get("status") == "retired":
        UI.muted(f"Already retired: {args['id']}")
        return 0

    # The one field write in this module that is NOT `apply_transition`: the persona
    # schema carries no `transitions[]` (Appendix: `extra="forbid"`, id/name/description/
    # kind/status only), and the contract's field writer always appends an audit entry —
    # which this register cannot hold without failing its own validator. So retire sets
    # the soft-delete marker on the record the contract handed back, and the file is
    # still opened and written only by `load_register`/`save_register`. Reported to core
    # and spec-agent: either personas grow an audit trail or the contract grows a
    # transitionless field write; inventing a schema field here is not this surface's
    # call. Tracked in docs/registry-access-debt.md.
    p["status"] = "retired"
    rc = _save(path, register)
    if rc != 0:
        return rc

    UI.ok(f"Retired persona: {args['id']}")
    if args.get("reason"):
        UI.muted(f"  reason: {args['reason']}")
    return 0


_ACTIONS = {
    "add": cmd_add,
    "list": cmd_list,
    "show": cmd_show,
    "retire": cmd_retire,
}


def dispatch(action: str, args: dict[str, Any]) -> int:
    fn = _ACTIONS.get(action)
    if fn is None:
        UI.error(f"Unknown persona action: {action}")
        UI.muted("Available: " + ", ".join(sorted(_ACTIONS.keys())))
        return 2
    return fn(args)


__all__ = ["dispatch"]
