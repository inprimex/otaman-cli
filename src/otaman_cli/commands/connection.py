"""`otaman connection <create|list|show|update|delete|check>`.

agent-credential-access 3.1. Connections are first-class objects an agent
consumes — "how do I reach + auth to X" — composed of locations/identifiers
only: ``{name, type, endpoint, secret_ref, ssh_ref, scope}``. This command is
the cli surface over otaman-core's read/cascade (`connections.resolve_for`),
values-free inventory (`_secrets.list_keys` + `connections.missing_secret_refs`),
and check engine (`connection_check.ConnectionChecker`), plus the per-scope
`connections.yaml` writer in `otaman_cli.connections.store`.

Hard invariant (spec: values SHALL NEVER be exposed): every field rendered
here is a location or identifier — `secret_ref` is a backend key NAME, never
a value. Resolution happens at the call site, never in these surfaces.

Manage-not-guess (spec): create/update PROPOSE metadata and require explicit
confirmation before persisting; they never silently infer + commit typing.
"""

from __future__ import annotations

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root
from otaman_cli.main import UI

_ACTIONS = ("create", "list", "show", "update", "delete", "check")


def cmd_connection(args: list[str]) -> int:
    """Dispatch `otaman connection <action> [...]`."""
    if not args:
        UI.error("Usage: otaman connection <action> [options]")
        UI.muted("Actions: " + " | ".join(_ACTIONS))
        return 1
    action, *rest = args
    if action not in _ACTIONS:
        UI.error(f"Unknown connection action: {action}")
        UI.muted("Actions: " + " | ".join(_ACTIONS))
        return 2

    root = find_project_root()
    if root is None:
        UI.error("Not in an otaman project (no platform.yaml in cwd or ancestors)")
        return 1

    if action == "list":
        return _cmd_list(root, rest)
    if action == "show":
        return _cmd_show(root, rest)
    if action == "create":
        return _cmd_create(root, rest)
    if action == "update":
        return _cmd_update(root, rest)
    if action == "delete":
        return _cmd_delete(root, rest)
    if action == "check":
        return _cmd_check(root, rest)
    return 2  # unreachable


# ---------------------------------------------------------------------------
# arg parsing helpers


def _parse_flags(rest: list[str]) -> tuple[list[str], dict[str, str], set[str]]:
    """Split *rest* into (positionals, valued-flags, bare-flags).

    Valued flags are ``--key value``; bare flags (``--yes``, ``--all``,
    ``--fix``, ``--reattach``) take no value.
    """
    bare = {"--yes", "--all", "--fix", "--reattach"}
    positionals: list[str] = []
    valued: dict[str, str] = {}
    present_bare: set[str] = set()
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok in bare:
            present_bare.add(tok)
            i += 1
        elif tok.startswith("--") and i + 1 < len(rest):
            valued[tok] = rest[i + 1]
            i += 2
        else:
            positionals.append(tok)
            i += 1
    return positionals, valued, present_bare


def _program_name(root) -> str:
    """The platform.yaml `project` name — the CheckReport store key."""
    try:
        import yaml

        cfg = yaml.safe_load((root / "platform.yaml").read_text(encoding="utf-8"))
        if isinstance(cfg, dict) and cfg.get("project"):
            return str(cfg["project"])
    except Exception:  # noqa: BLE001 - fall back to dir name
        pass
    return root.name


def _available_keys(root) -> set[str]:
    """Values-free backend key NAMES (workspace ∪ tenant dotenv). Never values."""
    from otaman_core._secrets import list_keys

    return list_keys(maestro_root=root)


# ---------------------------------------------------------------------------
# list / show — values-free inventory


def _cmd_list(root, rest: list[str]) -> int:
    from otaman_core.connections import missing_secret_refs, resolve_for

    _pos, valued, _bare = _parse_flags(rest)
    scope_filter = valued.get("--scope")

    conns = resolve_for(root)
    if scope_filter:
        conns = [c for c in conns if c.scope == scope_filter]

    UI.header("Connections")
    if not conns:
        UI.muted("No connections configured. Add one: otaman connection create <name> ...")
        return 0

    unbacked = set(missing_secret_refs(conns, _available_keys(root)))
    reports = _load_reports(root)

    for c in conns:
        badge = "  [!] no backing key" if c.name in unbacked else ""
        UI.bullet(f"{c.name}  ({c.type})")
        UI.kv("  endpoint", c.endpoint)
        UI.kv("  secret_ref", (c.secret_ref or "—") + badge)
        if c.ssh_ref:
            UI.kv("  ssh_ref", c.ssh_ref)
        UI.kv("  scope", c.scope)
        UI.kv("  last-check", _render_last_check(reports.get(c.name)))
    return 0


def _cmd_show(root, rest: list[str]) -> int:
    from otaman_core.connections import missing_secret_refs, resolve_for

    pos, _valued, _bare = _parse_flags(rest)
    if not pos:
        UI.error("Usage: otaman connection show <name>")
        return 1
    name = pos[0]
    conns = resolve_for(root)
    match = next((c for c in conns if c.name == name), None)
    if match is None:
        UI.error(f"No connection named {name!r}")
        return 1

    unbacked = set(missing_secret_refs([match], _available_keys(root)))
    reports = _load_reports(root)
    UI.header(f"Connection: {match.name}")
    UI.kv("type", match.type)
    UI.kv("endpoint", match.endpoint)
    UI.kv(
        "secret_ref",
        (match.secret_ref or "—") + ("  [!] no backing key" if match.name in unbacked else ""),
    )
    UI.kv("ssh_ref", match.ssh_ref or "—")
    UI.kv("scope", match.scope)
    UI.kv("last-check", _render_last_check(reports.get(match.name)))
    UI.muted(
        "(values are never shown — secret_ref is a backend key name, resolved at the call site)"
    )
    return 0


# ---------------------------------------------------------------------------
# create / update / delete — manage-not-guess (propose-and-confirm)


def _cmd_create(root, rest: list[str]) -> int:
    from otaman_cli.connections.store import find_connection, scope_write_path, upsert_connection

    pos, valued, bare = _parse_flags(rest)
    if not pos:
        UI.error("Usage: otaman connection create <name> --type T --endpoint E [options]")
        UI.muted("Options: --secret-ref R  --ssh-ref H  --scope program|tenant  --yes")
        return 1
    name = pos[0]
    scope = valued.get("--scope", "program")
    try:
        path = scope_write_path(scope, root)
    except ValueError as exc:
        UI.error(str(exc))
        return 1

    if find_connection(path, name) is not None:
        UI.error(
            f"Connection {name!r} already exists in {scope} scope — use `update` to change it."
        )
        return 1

    conn = {
        "name": name,
        "type": valued.get("--type"),
        "endpoint": valued.get("--endpoint"),
        "secret_ref": valued.get("--secret-ref"),
        "ssh_ref": valued.get("--ssh-ref"),
        "scope": scope,
    }
    missing = [f for f in ("type", "endpoint") if not conn.get(f)]
    if missing:
        UI.error(f"Missing required metadata: {', '.join('--' + m for m in missing)}")
        UI.muted("Metadata is never auto-guessed — provide it explicitly, then confirm.")
        return 1

    if not _confirm_metadata("Create connection", conn, yes="--yes" in bare):
        UI.error("Cancelled — not confirmed.")
        return 1

    upsert_connection(path, conn)
    UI.ok(f"Created connection {name!r} ({scope} scope)")
    UI.kv("file", str(path))
    return 0


def _cmd_update(root, rest: list[str]) -> int:
    from otaman_cli.connections.store import find_connection, scope_write_path, upsert_connection

    pos, valued, bare = _parse_flags(rest)
    if not pos:
        UI.error(
            "Usage: otaman connection update <name> [--type|--endpoint|--secret-ref|--ssh-ref V]"
        )
        UI.muted("Also: --scope program|tenant  --yes")
        return 1
    name = pos[0]
    scope = valued.get("--scope", "program")
    try:
        path = scope_write_path(scope, root)
    except ValueError as exc:
        UI.error(str(exc))
        return 1

    existing = find_connection(path, name)
    if existing is None:
        UI.error(f"No connection named {name!r} in {scope} scope.")
        return 1

    updated = dict(existing)
    updated["name"] = name
    updated["scope"] = scope
    for flag, field in (
        ("--type", "type"),
        ("--endpoint", "endpoint"),
        ("--secret-ref", "secret_ref"),
        ("--ssh-ref", "ssh_ref"),
    ):
        if flag in valued:
            updated[field] = valued[flag]

    if not _confirm_metadata("Update connection", updated, yes="--yes" in bare):
        UI.error("Cancelled — not confirmed.")
        return 1

    upsert_connection(path, updated)
    UI.ok(f"Updated connection {name!r} ({scope} scope)")
    return 0


def _cmd_delete(root, rest: list[str]) -> int:
    from otaman_cli.connections.store import delete_connection, find_connection, scope_write_path

    pos, valued, bare = _parse_flags(rest)
    if not pos:
        UI.error("Usage: otaman connection delete <name> [--scope program|tenant]")
        return 1
    name = pos[0]
    scope = valued.get("--scope", "program")
    try:
        path = scope_write_path(scope, root)
    except ValueError as exc:
        UI.error(str(exc))
        return 1

    if find_connection(path, name) is None:
        UI.error(f"No connection named {name!r} in {scope} scope.")
        return 1

    from otaman_cli.safety import confirm_destructive_operation

    if not confirm_destructive_operation(
        f"Delete connection {name!r} from {scope} scope", str(path), yes="--yes" in bare
    ):
        UI.error("Cancelled.")
        return 1

    delete_connection(path, name)
    UI.ok(f"Deleted connection {name!r} ({scope} scope)")
    return 0


def _confirm_metadata(title: str, conn: dict, *, yes: bool) -> bool:
    """Propose-and-confirm gate (spec: metadata confirmed, never auto-guessed).

    Echoes the proposed metadata (locations/identifiers only), then requires
    confirmation before the caller persists. Interactive → ``[y/N]``;
    non-interactive → requires ``--yes`` (the operator's explicit review),
    refusing otherwise so nothing is committed on inferred metadata.
    """
    import sys

    from otaman_cli.connections.store import CONNECTION_FIELDS

    print()
    print(f"{title} — proposed metadata (no secret value is stored):")
    for f in CONNECTION_FIELDS:
        if conn.get(f) is not None:
            print(f"  {f}: {conn[f]}")
    print()

    if yes:
        return True
    if not sys.stdin.isatty():
        print(
            "Refusing to persist without confirmation in a non-interactive context "
            "(pass --yes to confirm the metadata above).",
            file=sys.stderr,
        )
        return False
    try:
        return input("Persist this connection? [y/N]: ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


# ---------------------------------------------------------------------------
# check — reachability + auth; read-only by default, --fix self-heals


def _cmd_check(root, rest: list[str]) -> int:
    from otaman_core.connection_check import (
        ConnectionChecker,
        NetworkProber,
        SshProber,
        persist_reports,
        render_last_check,
        report_store_path,
    )
    from otaman_core.connections import resolve_for
    from otaman_core.ssh_registry import SshAgentRegistry

    pos, valued, bare = _parse_flags(rest)
    fix = "--fix" in bare or "--reattach" in bare
    conns = resolve_for(root)

    if "--all" in bare:
        targets = conns
    elif pos:
        targets = [c for c in conns if c.name == pos[0]]
        if not targets:
            UI.error(f"No connection named {pos[0]!r}")
            return 1
    else:
        UI.error("Usage: otaman connection check <name> | --all [--fix]")
        return 1

    checker = ConnectionChecker(
        ssh_prober=SshProber(SshAgentRegistry()),
        network_prober=NetworkProber(_http_probe, _available_keys(root)),
    )
    reports = checker.check_all(targets, fix=fix)

    # Persist (durable last-check for §2.1 and future `list`). check is
    # read-only w.r.t. the CONNECTION; the report store is its own machine
    # state, written on every check per the store contract.
    try:
        persist_reports(reports, report_store_path(), _program_name(root))
    except OSError as exc:  # pragma: no cover - disk failure
        UI.warn(f"Could not persist check report: {exc}")

    UI.header("Connection check")
    ok = True
    for r in reports:
        line = f"{r.name}: {r.status}"
        if r.status == "ok" or r.healed:
            UI.ok(line + (" (healed)" if r.healed else ""))
        else:
            UI.error(line)
            ok = False
        UI.muted(f"    {r.detail}")
        UI.muted(f"    last-check: {render_last_check(r)}")
    return 0 if ok else 1


def _http_probe(endpoint: str) -> bool:
    """Reachability probe for git-https/api endpoints: True if the host answers.

    Reachability only — never sends or reads a credential (auth is a
    values-free backing-key check in NetworkProber). Any HTTP response
    (including 401/403) counts as reachable; only a transport failure is
    unreachable.
    """
    import urllib.error
    import urllib.request

    url = endpoint if "://" in endpoint else f"https://{endpoint}"
    req = urllib.request.Request(url, method="HEAD")
    try:
        urllib.request.urlopen(req, timeout=5)  # noqa: S310 - endpoint is operator-configured
        return True
    except urllib.error.HTTPError:
        return True  # server answered (even 4xx) → reachable
    except Exception:  # noqa: BLE001 - any transport error → unreachable
        return False


# ---------------------------------------------------------------------------
# shared report helpers


def _load_reports(root):
    from otaman_core.connection_check import load_reports, report_store_path

    try:
        return load_reports(report_store_path(), _program_name(root))
    except Exception:  # noqa: BLE001 - absent/malformed store → no last-check
        return {}


def _render_last_check(report) -> str:
    from otaman_core.connection_check import render_last_check

    return render_last_check(report)


register(
    CommandSpec(
        name="connection",
        handler=cmd_connection,
        help="Manage connections: create, list, show, update, delete, check",
    )
)
