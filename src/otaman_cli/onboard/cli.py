"""argparse subcommand wiring for `otaman onboard`."""

from __future__ import annotations

import argparse
import sys

from otaman_cli.onboard.doctor import cmd_doctor
from otaman_cli.onboard.users import cmd_add_user, cmd_list_users, cmd_whoami


def build_onboard_parser(parser: argparse.ArgumentParser) -> None:
    """Attach onboard subcommands to ``parser`` (a sub-parser from main.py)."""
    sub = parser.add_subparsers(dest="onboard_cmd", required=True)

    # add-user
    p_add = sub.add_parser("add-user", help="add a user")
    p_add.add_argument("email", help="user's email address")
    p_add.add_argument(
        "--role", required=True,
        help="role(s), comma-separated; bare names get otaman: prefix "
             "(e.g. 'developer,approver' → ['otaman:developer','otaman:approver'])",
    )
    p_add.add_argument("--display-name", help="display name; defaults to email-local-part")
    p_add.add_argument("--unix-user", help="Unix username; defaults to email-local-part")
    p_add.add_argument("--telegram-id", type=int, help="Telegram numeric user id (optional)")
    p_add.add_argument("--state-dir", help="override state dir; default $OTAMAN_STATE_DIR or /var/otaman")
    p_add.add_argument("--apply", action="store_true", help="actually do it (default is dry-run)")
    p_add.set_defaults(func=cmd_add_user)

    # list-users
    p_list = sub.add_parser("list-users", help="list registered users")
    p_list.add_argument("--state-dir", help="override state dir")
    p_list.add_argument("--json", action="store_true", help="emit JSON instead of table")
    p_list.set_defaults(func=cmd_list_users)

    # whoami
    p_whoami = sub.add_parser("whoami", help="print the calling user's otaman identity")
    p_whoami.add_argument("--state-dir", help="override state dir")
    p_whoami.set_defaults(func=cmd_whoami)

    # doctor
    p_doctor = sub.add_parser("doctor", help="diagnostic checks for onboarding state")
    p_doctor.add_argument("--state-dir", help="override state dir")
    p_doctor.set_defaults(func=cmd_doctor)


def main(argv: list[str] | None = None) -> int:
    """Standalone entrypoint (mainly for testing).

    In production this gets called from otaman_cli/main.py's dispatch
    via build_onboard_parser().
    """
    parser = argparse.ArgumentParser(prog="otaman onboard")
    build_onboard_parser(parser)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
