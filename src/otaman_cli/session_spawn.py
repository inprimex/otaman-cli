#!/usr/bin/env python3
"""``otaman session spawn`` -- spawn an otaman session as the logged-in user.

Reads the OIDC token cache from ``otaman login``, extracts the user id
from the JWT's ``sub`` claim, then POSTs to the local runner's /spawn
endpoint with that id attached. After this the session appears in
``list_team_sessions`` attributed to the right user.

Without this command, users would have to either: (a) curl the runner
directly with a hand-crafted body including their user_id, or (b) use
otaman-runner's CLI which doesn't currently surface OIDC identity.

Usage:
    otaman session spawn \\
        --agent backend-agent --repo auth-service \\
        --project-root /home/user/otaman-project

    otaman session spawn --agent X --repo Y --project-root Z \\
        --mode interactive --account dev \\
        --initial-prompt 'help me'

Exit codes: 0 success, 1 token / runner errors, 2 bad args.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_TOKEN_CACHE = Path.home() / ".otaman" / "token.cache"
DEFAULT_RUNNER_ENDPOINT = Path.home() / ".otaman" / "runner.endpoint"

# F032 (security GAP finding, 2026-07-04, plan confirmed by Roman 2026-07-07)
# Part B — the runner bearer token must not travel plaintext HTTP to a
# non-loopback host. otaman-runner has no TLS support today (tracked
# separately with runner-agent); this is the CLI-side fail-closed guard,
# safe to ship regardless of when/whether that lands.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_INSECURE_RUNNER_ESCAPE_HATCH = "OTAMAN_ALLOW_INSECURE_RUNNER"


def _validate_spawn_target(host: str, scheme: str) -> str | None:
    """Return an error message if (host, scheme) is unsafe to send the
    runner bearer token to, else None.

    https:// -- always fine (once otaman-runner supports it).
    http:// + loopback -- fine, the common case: the runner defaults to
        binding 127.0.0.1, so this traffic never leaves the machine.
    http:// + non-loopback + OTAMAN_ALLOW_INSECURE_RUNNER set -- allowed,
        but warns loudly on every use.
    http:// + non-loopback + no escape hatch -- refused (fail closed) --
        `--host` on the runner side is operator-overridable for the
        multi-user "spawn as logged-in user" topology this command exists
        for, so non-loopback is a real, not hypothetical, deployment shape.
    anything else -- refused.
    """
    if scheme == "https":
        return None
    if scheme != "http":
        return f"Unknown runner endpoint scheme {scheme!r} (expected http or https)"
    if host in _LOOPBACK_HOSTS:
        return None
    if os.environ.get(_INSECURE_RUNNER_ESCAPE_HATCH, "").strip():
        print(
            f"WARNING: runner endpoint {host!r} is non-loopback and uses plaintext "
            f"http:// — the runner bearer token travels unencrypted. Allowed only "
            f"because {_INSECURE_RUNNER_ESCAPE_HATCH} is set.",
            file=sys.stderr,
        )
        return None
    return (
        f"Refusing to send the runner token to non-loopback host {host!r} over "
        f"plaintext http:// — it would travel unencrypted. Set "
        f"{_INSECURE_RUNNER_ESCAPE_HATCH}=1 if you've accepted the risk, or "
        f"configure scheme=https in the runner endpoint file once the runner "
        f"supports TLS."
    )


def jwt_sub(token: str):
    """Pull the `sub` claim from a JWT without verifying the signature.

    Safe in this context because: (a) the token already came from
    ``otaman login`` which got it directly from Zitadel; (b) we're only
    using sub to label our OWN spawn request to OUR OWN local runner,
    not to authorize anything trust-sensitive.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload_b64 = parts[1]
    # base64url, padding-tolerant
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) and sub else None


def load_token(path: Path):
    """Return access_token string from token cache file, or None."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    tok = data.get("access_token")
    return tok if isinstance(tok, str) and tok else None


def load_runner_endpoint(path: Path):
    """Return (host, port, token, scheme) from runner endpoint file, or None.

    `scheme` (F032 Part B) defaults to `"http"` when the endpoint file
    doesn't declare one — backward compatible with existing otaman-runner
    deployments, none of which support TLS yet.
    """
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    host = "127.0.0.1"
    port = None
    token = None
    scheme = "http"
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k == "host":
            host = v
        elif k == "port":
            port = int(v)
        elif k == "token":
            token = v
        elif k == "scheme":
            scheme = v
    if port is None or token is None:
        return None
    return host, port, token, scheme


def build_spawn_body(args, user_id) -> dict:
    """Translate CLI args into a runner /spawn request body."""
    body = {
        "agent": args.agent,
        "repo": args.repo,
        "project_root": str(Path(args.project_root).resolve()),
        "mode": args.mode,
        "harness": args.harness,
        "user": user_id,
    }
    if args.account:
        body["account"] = args.account
    if args.worktree:
        body["worktree"] = str(Path(args.worktree).resolve())
    if args.initial_prompt:
        body["initial_prompt"] = args.initial_prompt
    if args.env:
        env = {}
        for kv in args.env:
            if "=" not in kv:
                continue
            k, _, v = kv.partition("=")
            env[k.strip()] = v
        body["env"] = env
    return body


def post_spawn(*, host, port, token, body, scheme="http", opener=None, timeout=30.0):
    """POST to runner /spawn. Returns (status_code, response_dict)."""
    url = f"{scheme}://{host}:{port}/spawn"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    opener = opener or urllib.request.build_opener()
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read())
        except (ValueError, json.JSONDecodeError):
            err_body = {"error": f"HTTP {e.code}"}
        return e.code, err_body
    except urllib.error.URLError as e:
        return 0, {"error": f"runner unreachable: {e}"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="otaman session spawn",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--agent", required=True, help="Agent identity (e.g. backend-agent)")
    ap.add_argument("--repo", required=True, help="Repo name from platform.yaml or absolute path")
    ap.add_argument("--project-root", required=True, help="Absolute path to otaman folder")
    ap.add_argument("--mode", default="interactive", choices=["interactive", "headless"])
    ap.add_argument("--harness", default="claude-code")
    ap.add_argument("--account", help="CLAUDE_CONFIG_DIR profile name")
    ap.add_argument("--worktree", help="Worktree path; default = use repo directly")
    ap.add_argument("--initial-prompt", help="First message to seed the session")
    ap.add_argument(
        "--env",
        action="append",
        default=[],
        help="OTAMAN_USER_KEY=value (repeatable) -- passed to spawned process. "
        "Key must be prefixed OTAMAN_USER_ (F091): the runner rejects any "
        "other key with 400, including attempts to set PATH/LD_PRELOAD/"
        "BASH_ENV/NODE_OPTIONS or override a runner-computed var.",
    )
    ap.add_argument("--token-cache", type=Path, default=DEFAULT_TOKEN_CACHE)
    ap.add_argument("--runner-endpoint", type=Path, default=DEFAULT_RUNNER_ENDPOINT)
    ap.add_argument(
        "--output-json", action="store_true", help="Print response as JSON instead of human text"
    )
    args = ap.parse_args(argv)

    # Auth: read token, extract sub
    token = load_token(args.token_cache)
    if token is None:
        print(
            f"ERROR: no OIDC token at {args.token_cache}. Run `otaman login` first.",
            file=sys.stderr,
        )
        return 1
    user_id = jwt_sub(token)
    if not user_id:
        print(
            "ERROR: cached token has no 'sub' claim. Try `otaman login --force`.",
            file=sys.stderr,
        )
        return 1

    # Find runner
    ep = load_runner_endpoint(args.runner_endpoint)
    if ep is None:
        print(
            f"ERROR: runner endpoint file missing or malformed: {args.runner_endpoint}. "
            f"Is otaman-runner running?",
            file=sys.stderr,
        )
        return 1
    host, port, runner_token, scheme = ep

    # F032 Part B — fail closed before sending the bearer token anywhere.
    unsafe = _validate_spawn_target(host, scheme)
    if unsafe:
        print(f"ERROR: {unsafe}", file=sys.stderr)
        return 1

    body = build_spawn_body(args, user_id)
    status, resp = post_spawn(
        host=host,
        port=port,
        token=runner_token,
        body=body,
        scheme=scheme,
    )

    if status != 200:
        print(
            f"ERROR: runner /spawn returned status {status}: {resp}",
            file=sys.stderr,
        )
        return 1

    if args.output_json:
        print(json.dumps(resp, indent=2))
    else:
        session_id = resp.get("session_id", "?")
        attach = resp.get("attach") or {}
        print(f"spawned: session_id={session_id} user={user_id}")
        if attach:
            print(
                f"attach: backend={attach.get('backend')} session_name={attach.get('session_name')}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
