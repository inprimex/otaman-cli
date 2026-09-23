"""`otaman credential-helper` — the git credential helper (shared-agent-memory D4).

The connections layer's contract has always been "secret_ref is a KEY NAME;
resolution happens at the call site". The call site was missing, so every tenant
that needed git to authenticate hand-rolled one — a script, an askpass shim, a
URL with a token in it. The mildef incident is what that costs. This is the one
shipped call site; per shared-logic-single-home, a per-tenant hand-roll is now a
conformance defect rather than a local necessity.

Git's credential protocol: git runs `<helper> <op>` with a request on stdin as
`key=value` lines terminated by a blank line, and reads the same shape back on
stdout. Three operations, and what each MUST do here:

``get``    resolve and emit. The value is read at invocation from the credential
           cascade and written to stdout for git to consume. It is never logged,
           never echoed, and never written to a file.

``store``  **deliberately does nothing.** This is the operation git calls to
           persist a credential it just used, and doing so is exactly what this
           helper exists to avoid. Silence here is not an omission — a helper
           that honoured `store` would write the value to disk on the first
           successful push and quietly become the thing it replaced.

``erase``  nothing to erase, because nothing was stored. It succeeds so git's
           own flow is not broken by a helper that errors on a routine call.

Nothing here writes to disk. The only output is git's own protocol on stdout.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root
from otaman_cli.main import UI

#: Operations git may invoke. Anything else is a protocol error, not a no-op —
#: silently succeeding on an unknown verb would hide a git/helper mismatch.
OPERATIONS = ("get", "store", "erase")


def _read_request(stream) -> dict[str, str]:
    """Parse git's `key=value` request block from *stream*.

    Terminated by a blank line or EOF. Unknown keys are kept: git adds fields
    over time and a helper that drops them cannot match on what it was given.
    """
    request: dict[str, str] = {}
    for raw in stream:
        line = raw.rstrip("\n")
        if not line:
            break
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        request[key.strip()] = value
    return request


def _host_of(conn: Any) -> str:
    """The host a connection's endpoint points at, lowercased, port stripped."""
    endpoint = str(getattr(conn, "endpoint", "") or "")
    if not endpoint:
        return ""
    remainder = endpoint.split("://", 1)[-1]
    # Strip any user@ prefix and any /path or :port suffix.
    remainder = remainder.rsplit("@", 1)[-1]
    host = remainder.split("/", 1)[0].split(":", 1)[0]
    return host.strip().lower()


def _match(connections: list, host: str) -> Any | None:
    """The connection serving *host*, or None.

    Exact host match only. A suffix match (`github.com` serving
    `evil-github.com`) would hand a credential to a host the operator never
    registered, which is the one mistake a credential helper must not make.
    """
    host = (host or "").strip().lower()
    if not host:
        return None
    for conn in connections:
        if _host_of(conn) == host and str(getattr(conn, "secret_ref", "") or "").strip():
            return conn
    return None


def _resolve(request: dict[str, str], root: Path) -> tuple[str | None, str | None, str]:
    """``(username, password, note)`` for *request* — the value read at invocation."""
    try:
        from otaman_core.connections import resolve_for
    except Exception:  # noqa: BLE001 - no connections layer → nothing to answer with
        return None, None, "otaman-core has no connections layer in this install"

    host = request.get("host", "")
    try:
        connections = resolve_for(root)
    except Exception as exc:  # noqa: BLE001 - unreadable scope files → answer nothing
        return None, None, f"could not read connections: {exc}"

    conn = _match(list(connections), host)
    if conn is None:
        return None, None, f"no registered connection serves {host!r}"

    ref = str(getattr(conn, "secret_ref", "") or "").strip()
    from otaman_core._secrets import resolve_cascade

    value = resolve_cascade(ref, maestro_root=root)
    if not value:
        # Name the REF, never a value, and never whether some other key exists.
        return None, None, f"secret_ref {ref!r} is not defined in any credential layer"

    username = (
        str(getattr(conn, "username", "") or "") or request.get("username") or "x-access-token"
    )
    return username, value, ""


def cmd_credential_helper(args: list[str]) -> int:
    """`otaman credential-helper get|store|erase`, speaking git's protocol."""
    if not args or args[0] in {"-h", "--help", "help"}:
        UI.info("otaman credential-helper <get|store|erase>")
        UI.muted("  Git credential helper. Resolves a connection's secret_ref at")
        UI.muted("  invocation; never writes a value anywhere.")
        UI.muted("")
        UI.muted("  Wire it (per repo, or --global):")
        UI.muted("    git config credential.helper '!otaman credential-helper'")
        return 0 if args else 1

    operation = args[0]
    if operation not in OPERATIONS:
        UI.error(f"unknown credential operation: {operation!r}")
        return 2

    # `store` and `erase` are answered WITHOUT reading the request: there is
    # nothing to do with it, and consuming stdin we have no use for is how a
    # value ends up somewhere it was not meant to go.
    if operation in ("store", "erase"):
        return 0

    root = find_project_root()
    if root is None:
        # git treats a silent, successful helper as "no credential" and moves on
        # to the next one. That is the correct outcome outside a program, and it
        # must not be a hard error — git would abort the whole operation.
        return 0

    request = _read_request(sys.stdin)
    username, password, note = _resolve(request, root)
    if not password:
        # Diagnostics to STDERR only: stdout is git's protocol channel, and a
        # stray line there is parsed as a credential field.
        if note:
            print(f"otaman credential-helper: {note}", file=sys.stderr)
        return 0

    out = sys.stdout
    out.write(f"username={username}\n")
    out.write(f"password={password}\n")
    out.write("\n")
    out.flush()
    return 0


register(
    CommandSpec(
        name="credential-helper",
        handler=cmd_credential_helper,
        help="Git credential helper: resolves secret_ref at invocation, stores nothing",
    )
)

__all__ = ["cmd_credential_helper"]
