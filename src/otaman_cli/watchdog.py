"""`otaman watchdog` HTTP client + CLI handlers.

Wraps the four runner endpoints (spec `20260611T140057` task 3):

    GET  /watchdog/status
    POST /watchdog/start
    POST /watchdog/pause
    POST /watchdog/resume

All four take the loopback bearer (`token` from the runner endpoint file)
plus the same hostport (`host:port`) every other otaman-cli runner call
discovers via ``load_runner_endpoint`` in ``session_spawn``.

The runner returns the same status payload from all four endpoints — this
module's ``call_watchdog`` returns ``(http_status, payload_dict)`` and
``format_status`` renders the human-friendly table per the spec.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from otaman_cli.session_spawn import (
    DEFAULT_RUNNER_ENDPOINT,
    _validate_spawn_target,
    load_runner_endpoint,
)


def _format_timestamp_short(iso: str | None) -> str:
    """`2026-06-16T21:30:01+00:00` → `21:30:01`, fallback to the input."""
    if not iso:
        return "—"
    if "T" in iso:
        tail = iso.split("T", 1)[1]
        # Strip timezone suffix
        for sep in ("+", "Z", "-"):
            if sep in tail[2:]:
                tail = tail.split(sep, 1)[0] if sep != "-" else tail
                if sep == "-":
                    # only strip when it follows the time, not the date
                    idx = tail.rfind("-")
                    if idx > 4:
                        tail = tail[:idx]
                break
        # Trim sub-second precision
        if "." in tail:
            tail = tail.split(".", 1)[0]
        return tail
    return iso


def call_watchdog(
    action: str,
    *,
    endpoint_path: Path | None = None,
    opener: object = None,
    timeout: float = 15.0,
) -> tuple[int, dict]:
    """Hit one of the four watchdog endpoints.

    *action*: one of ``status``, ``start``, ``pause``, ``resume``.
    Returns ``(http_status_code, response_payload)``.

    HTTP method:
      - ``status`` → GET
      - ``start`` / ``pause`` / ``resume`` → POST (empty body)

    Returns ``(0, {"error": "..."})`` when the runner endpoint file is
    missing/malformed OR the runner is unreachable.  Returns the runner's
    structured ``{"error": "..."}`` payload as-is on 404 / 503 / other
    HTTP errors so the caller can render the spec-defined messages
    (`watchdog not configured`, `watchdog start failed (no ws loop available)`).
    """
    if action not in ("status", "start", "pause", "resume"):
        raise ValueError(f"unknown watchdog action: {action!r}")

    path = endpoint_path or DEFAULT_RUNNER_ENDPOINT
    ep = load_runner_endpoint(path)
    if ep is None:
        return 0, {
            "error": f"runner endpoint file missing or malformed: {path}",
            # ce-ee-release-channels 3.2 — lets the print path swap the raw
            # error for the CE explanation (a missing endpoint is the
            # EXPECTED state on CE; an unreachable runner is not).
            "endpoint_missing": True,
        }
    host, port, token, scheme = ep

    # F032 Part B — same bearer-token-over-plaintext-HTTP guard as
    # session_spawn.post_spawn; this hits the same runner endpoint file
    # with the same token, so it needs the same fail-closed check.
    unsafe = _validate_spawn_target(host, scheme)
    if unsafe:
        return 0, {"error": unsafe}

    url = f"{scheme}://{host}:{port}/watchdog/{action}"
    method = "GET" if action == "status" else "POST"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if method == "POST":
        headers["Content-Type"] = "application/json"
        data = b""
    else:
        data = None

    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    opener = opener or urllib.request.build_opener()
    try:
        with opener.open(req, timeout=timeout) as resp:
            body_bytes = resp.read()
            try:
                payload = json.loads(body_bytes) if body_bytes else {}
            except (ValueError, json.JSONDecodeError):
                payload = {"error": f"non-JSON response: {body_bytes!r}"}
            return resp.status, payload
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read())
        except (ValueError, json.JSONDecodeError):
            err_body = {"error": f"HTTP {e.code}"}
        return e.code, err_body
    except urllib.error.URLError as e:
        return 0, {"error": f"runner unreachable: {e}"}


def format_status(payload: dict, *, agent_name: str | None = None) -> str:
    """Render the spec-defined human status table from a `/watchdog/status` payload.

    Output (4 lines):

        Watchdog:  <state>  (poll: <N>s)
        Sessions:  <count> monitored
        Last act:  <agent> — <pattern> — <action> — <time>
        Escalated: <count> pending

    *agent_name* — optional override for the Last-act line.  If the caller
    already cross-referenced the session id to an agent via
    ``GET /sessions/<id>``, they can pass it in.  Defaults to showing
    the session id itself.
    """
    enabled = bool(payload.get("enabled"))
    running = bool(payload.get("running"))
    paused = bool(payload.get("paused"))
    poll = payload.get("poll_interval_s")
    sessions = payload.get("sessions_monitored") or []
    last = payload.get("last_action")
    pending = payload.get("pending_escalations", 0)

    if not enabled:
        state = "disabled"
    elif paused:
        state = "paused"
    elif running:
        state = "running"
    else:
        state = "stopped"

    poll_str = f"poll: {poll}s" if poll is not None else "poll: —"
    lines: list[str] = [
        f"Watchdog:  {state}  ({poll_str})",
        f"Sessions:  {len(sessions)} monitored",
    ]
    if last and isinstance(last, dict):
        sid = last.get("session_id", "?")
        pat = last.get("pattern_id", "?")
        act = last.get("action", "?")
        ts = _format_timestamp_short(last.get("timestamp"))
        label = agent_name or sid
        lines.append(f"Last act:  {label} — {pat} — {act} — {ts}")
    else:
        lines.append("Last act:  (none yet)")
    lines.append(f"Escalated: {pending} pending")
    return "\n".join(lines)


# --------------------------------------------------------------------- CLI handlers
def _print_payload_or_error(action: str, status: int, payload: dict, *, json_out: bool) -> int:
    """Shared output path for all four subcommands.

    Returns the CLI exit code:
        0   on HTTP 200
        2   on HTTP 404 (watchdog not configured) — common case, friendly hint
        2   on HTTP 503 (ws loop not available) — start-time only
        1   on any other non-200 / runner-unreachable / endpoint-missing
    """
    if json_out:
        print(json.dumps({"http_status": status, "payload": payload}))
    if status == 200:
        if not json_out:
            print(format_status(payload))
        return 0
    if status == 404:
        if not json_out:
            print(f"Watchdog not configured: {payload.get('error', '404')}")
            print(
                "  Hint: rebuild the runner with watchdog enabled, "
                "or check `runner.watchdog.enabled` in platform.yaml."
            )
        return 2
    if status == 503:
        if not json_out:
            print(f"Watchdog start failed: {payload.get('error', '503')}")
            print("  Hint: the runner's WS loop must be ready first — start a session, then retry.")
        return 2
    if status == 0:
        # Endpoint file missing or runner unreachable
        if not json_out:
            # ce-ee-release-channels 3.2 — on a CE install a missing
            # runner endpoint is the expected state: explain it with the
            # hosted-tier pointer instead of erroring raw. Edition file
            # is identity-only; unknown edition keeps the raw error.
            from otaman_cli.edition import absent_runner_notice

            notice = (
                absent_runner_notice(f"otaman watchdog {action}")
                if payload.get("endpoint_missing")
                else None
            )
            if notice:
                for line in notice:
                    print(line)
            else:
                print(f"ERROR: {payload.get('error', 'unknown error')}")
        return 1
    if not json_out:
        err = payload.get("error", f"HTTP {status}")
        print(f"ERROR: watchdog {action} → {status}: {err}")
    return 1


def cmd_watchdog(args: list[str]) -> int:
    """`otaman watchdog <action>` dispatcher.

    Usage:
        otaman watchdog status [--json]
        otaman watchdog start  [--json]
        otaman watchdog pause  [--json]
        otaman watchdog resume [--json]

    All four return the same status payload from the runner.
    """
    if not args:
        print("Usage: otaman watchdog <status|start|pause|resume> [--json]")
        return 2

    action = args[0]
    json_out = "--json" in args[1:]

    if action not in ("status", "start", "pause", "resume"):
        print(f"Unknown watchdog action: {action!r}")
        print("Usage: otaman watchdog <status|start|pause|resume> [--json]")
        return 2

    status, payload = call_watchdog(action)
    return _print_payload_or_error(action, status, payload, json_out=json_out)


__all__ = [
    "call_watchdog",
    "cmd_watchdog",
    "format_status",
]
