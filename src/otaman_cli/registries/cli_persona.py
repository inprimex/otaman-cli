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

from otaman_cli.identity import find_project_root
from otaman_cli.main import UI
from otaman_cli.registries import bus_messages
from otaman_cli.registries.loader import (
    resolve_registry_path,
    yaml_dump,
    yaml_load,
)
from otaman_cli.registries.personas import PersonaKind, PersonaRegistry
from otaman_cli.registries.platform_ext import load_program_extensions
from otaman_cli.registries.roles import (
    authz_advisory,
    resolve_operating_actor,
    resolve_roles,
)


def _bail(msg: str, code: int = 1) -> int:
    UI.error(msg)
    return code


def _load(root: Path) -> tuple[Path, Any] | None:
    path = resolve_registry_path(root, "personas")
    if path is None:
        _bail(
            "Cannot locate personas.yaml — no business repo found.\n"
            "  Set OTAMAN_BUSINESS_DIR, or add a repo with owner: cpo-agent in platform.yaml."
        )
        return None
    raw = yaml_load(path)
    if not isinstance(raw, dict):
        raw = {}
    if "personas" not in raw or raw["personas"] is None:
        raw["personas"] = []
    return path, raw


def _save(path: Path, raw: dict, *, validate: bool = True) -> int:
    if validate:
        try:
            PersonaRegistry.model_validate(raw)
        except Exception as exc:
            return _bail(f"Validation failed; refusing to write personas.yaml:\n{exc}", code=2)
    yaml_dump(raw, path)
    return 0


def _find(raw: dict, persona_id: str) -> dict | None:
    for p in raw.get("personas", []):
        if p.get("id") == persona_id:
            return p
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


def cmd_add(args: dict[str, Any]) -> int:
    root = find_project_root()
    if not root:
        return _bail("Not in an otaman project")
    actor, roles, _ = _ctx(root)
    authz_advisory("persona.add", actor, roles)

    required = ("id", "name", "description", "kind")
    missing = [k for k in required if not args.get(k)]
    if missing:
        return _bail("Missing required flag(s): " + ", ".join(f"--{k}" for k in missing))

    valid_kinds = {k.value for k in PersonaKind}
    if args["kind"] not in valid_kinds:
        return _bail(
            f"Invalid kind: {args['kind']!r}. Must be one of: {sorted(valid_kinds)}"
        )

    loaded = _load(root)
    if loaded is None:
        return 1
    path, raw = loaded
    if _find(raw, args["id"]):
        return _bail(f"Persona already exists: {args['id']}")

    new_entry = {
        "id": args["id"],
        "name": args["name"],
        "description": args["description"],
        "kind": args["kind"],
        "domain-prefill-source": args.get("domain_prefill_source"),
        "status": "active",
    }
    raw["personas"].append(new_entry)
    rc = _save(path, raw)
    if rc != 0:
        return rc

    UI.ok(f"Added persona: {args['id']} ({args['name']}) [active]")
    return 0


def cmd_list(args: dict[str, Any]) -> int:
    root = find_project_root()
    if not root:
        return _bail("Not in an otaman project")
    loaded = _load(root)
    if loaded is None:
        return 1
    _, raw = loaded
    personas = raw.get("personas", [])
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
        return _bail("Not in an otaman project")
    loaded = _load(root)
    if loaded is None:
        return 1
    _, raw = loaded
    p = _find(raw, args["id"])
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
        return _bail("Not in an otaman project")
    actor, roles, _ = _ctx(root)
    authz_advisory("persona.retire", actor, roles)

    loaded = _load(root)
    if loaded is None:
        return 1
    path, raw = loaded
    p = _find(raw, args["id"])
    if not p:
        return _bail(f"Persona not found: {args['id']}")
    if p.get("status") == "retired":
        UI.muted(f"Already retired: {args['id']}")
        return 0

    p["status"] = "retired"
    rc = _save(path, raw)
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
