#!/usr/bin/env python3
"""``otaman mcp-config`` -- emit a Claude Code .mcp.json snippet for the bridge.

Reads the cached OIDC access token (from ``otaman login``) and a bridge URL,
prints (or writes to a file) the JSON block Claude Code's `/mcp add-json`
or .mcp.json config expects.

Without this command, users would have to manually concatenate their bearer
token into a JSON template; this just does it.

Usage:
    otaman mcp-config --bridge-url http://localhost:8090
    otaman mcp-config --bridge-url http://localhost:8090 --output ~/.mcp.json
    otaman mcp-config --bridge-url http://localhost:8090 \\
                      --server-name greenbin-bridge --indent 2

Exit codes: 0 success, 1 missing/expired token, 2 missing args.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_TOKEN_CACHE = Path.home() / ".otaman" / "token.cache"
DEFAULT_SERVER_NAME = "otaman-bridge"


def load_cached_token(path: Path):
    """Return (access_token, expired_bool) from token cache, or (None, None) if missing."""
    if not path.is_file():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    token = data.get("access_token")
    if not token:
        return None, None
    expires_at = int(data.get("expires_at") or 0)
    expired = False
    if expires_at > 0:
        import time
        expired = time.time() >= (expires_at - 30)
    return token, expired


def build_config(*, bridge_url: str, token: str, server_name: str) -> dict:
    """Build the .mcp.json structure Claude Code expects."""
    url = bridge_url.rstrip("/") + "/mcp"
    return {
        "mcpServers": {
            server_name: {
                "type": "http",
                "url": url,
                "headers": {
                    "Authorization": f"Bearer {token}",
                },
            },
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="otaman mcp-config",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--bridge-url", required=True,
        help="Bridge base URL (e.g. http://localhost:8090). /mcp is appended.",
    )
    ap.add_argument(
        "--server-name", default=DEFAULT_SERVER_NAME,
        help=f"MCP server name in the config (default: {DEFAULT_SERVER_NAME!r})",
    )
    ap.add_argument(
        "--token-cache", type=Path, default=DEFAULT_TOKEN_CACHE,
        help=f"Path to token cache (default: {DEFAULT_TOKEN_CACHE})",
    )
    ap.add_argument(
        "--output", "-o", type=Path,
        help="Write JSON to this file instead of stdout",
    )
    ap.add_argument(
        "--indent", type=int, default=2,
        help="JSON indent (default 2; 0 for single-line)",
    )
    ap.add_argument(
        "--allow-expired", action="store_true",
        help="Emit config even if the cached token has expired (useful for "
             "scripting before `otaman login`); the resulting config will "
             "fail at use time.",
    )
    args = ap.parse_args(argv)

    token, expired = load_cached_token(args.token_cache)
    if token is None:
        print(
            f"ERROR: no token found at {args.token_cache}. "
            f"Run `otaman login` first.",
            file=sys.stderr,
        )
        return 1
    if expired and not args.allow_expired:
        print(
            f"ERROR: cached token at {args.token_cache} has expired. "
            f"Run `otaman login --force`, or pass --allow-expired to emit anyway.",
            file=sys.stderr,
        )
        return 1

    config = build_config(
        bridge_url=args.bridge_url,
        token=token,
        server_name=args.server_name,
    )
    indent = args.indent if args.indent > 0 else None
    body = json.dumps(config, indent=indent)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body + "\n", encoding="utf-8")
        try:
            args.output.chmod(0o600)
        except OSError:
            pass
        print(f"wrote MCP config to {args.output}", file=sys.stderr)
    else:
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
