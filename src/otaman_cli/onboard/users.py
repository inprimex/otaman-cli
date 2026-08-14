"""add-user and list-users subcommand logic."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from otaman_cli.onboard.audit import OnboardAudit
from otaman_cli.onboard.state import (
    StateError,
    User,
    default_state_dir,
    load_users,
    upsert_user,
    validate_email,
    validate_roles,
)


def _parse_roles(raw: str) -> list[str]:
    """Split ``developer,approver`` into [otaman:developer, otaman:approver].

    Accepts both bare names (``developer``) and fully-qualified
    (``otaman:developer``). Bare names get the ``otaman:`` prefix.
    """
    out: list[str] = []
    for piece in raw.split(","):
        r = piece.strip()
        if not r:
            continue
        if not r.startswith("otaman:"):
            r = f"otaman:{r}"
        out.append(r)
    return out


def _operator_identity() -> str:
    """Best-effort identity of the human running the command.

    Used as the ``actor`` field in audit events. v0 reads $USER /
    Unix login name; v0.1 reads the authenticated OIDC identity from
    the runner's token cache.
    """
    return os.environ.get("USER") or os.environ.get("LOGNAME") or getpass.getuser()


def cmd_add_user(args: argparse.Namespace) -> int:
    state_dir = Path(args.state_dir) if args.state_dir else default_state_dir()
    audit = OnboardAudit(state_dir / "audit")
    actor = _operator_identity()

    email = args.email
    try:
        validate_email(email)
        roles = _parse_roles(args.role)
        validate_roles(roles)
    except StateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        audit.user_add_failed(actor=actor, subject=email, error=str(exc))
        return 2

    display_name = args.display_name or email.split("@")[0]
    user = User(
        email=email,
        display_name=display_name,
        roles=roles,
        unix_user=args.unix_user,
        unix_groups=[],  # filled later by add-project
        telegram_id=args.telegram_id,
    )

    if not args.apply:
        print("DRY-RUN: would add user")
        print(f"  email: {email}")
        print(f"  display_name: {display_name}")
        print(f"  roles: {roles}")
        if args.unix_user:
            print(f"  unix_user: {args.unix_user}")
        if args.telegram_id:
            print(f"  telegram_id: {args.telegram_id}")
        print("Re-run with --apply to make changes.")
        return 0

    try:
        _result, added = upsert_user(state_dir, user)
    except StateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        audit.user_add_failed(actor=actor, subject=email, error=str(exc))
        return 1

    if added:
        audit.user_added(actor=actor, subject=email, roles=roles)
        print(f"added user: {email}")
    else:
        print(f"user already present (no change): {email}")
    return 0


def cmd_list_users(args: argparse.Namespace) -> int:
    state_dir = Path(args.state_dir) if args.state_dir else default_state_dir()
    users = load_users(state_dir)
    if not users:
        print("(no users registered)")
        return 0
    if args.json:
        import json

        print(json.dumps([u.to_dict() for u in users], indent=2, sort_keys=True))
        return 0

    # Human-readable columns
    headers = ["email", "display_name", "roles", "enabled"]
    widths = {h: len(h) for h in headers}
    rows: list[tuple] = []
    for u in users:
        row = (
            u.email,
            u.display_name,
            ",".join(sorted(u.roles)),
            "yes" if u.enabled else "no",
        )
        for h, val in zip(headers, row, strict=False):
            widths[h] = max(widths[h], len(val))
        rows.append(row)
    fmt = "  ".join("{:<" + str(widths[h]) + "}" for h in headers)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * widths[h] for h in headers)))
    for row in rows:
        print(fmt.format(*row))
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    """Print the operator's identity and their otaman registration if present."""
    state_dir = Path(args.state_dir) if args.state_dir else default_state_dir()
    operator = _operator_identity()
    print(f"unix_user: {operator}")
    users = load_users(state_dir)
    # Match by email matching unix_user, or by unix_user field directly
    matched: User | None = None
    for u in users:
        if u.unix_user == operator or u.email.split("@")[0] == operator:
            matched = u
            break
    if matched is None:
        print(f"otaman: (not registered — run `otaman onboard add-user {operator}@...`)")
        return 0
    print(f"otaman email: {matched.email}")
    print(f"display_name: {matched.display_name}")
    print(f"roles: {', '.join(sorted(matched.roles))}")
    print(f"enabled: {matched.enabled}")
    return 0
