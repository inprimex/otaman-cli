"""argparse subcommand wiring for `otaman onboard`."""

from __future__ import annotations

import argparse
import sys

from otaman_cli.onboard.doctor import cmd_doctor
from otaman_cli.onboard.users import cmd_add_user, cmd_list_users, cmd_whoami


def _cmd_program_init(args: argparse.Namespace) -> int:
    """Dispatch to the program-init runner.

    DEPRECATED: prefer `otaman init`, which handles all starting states
    (empty dir, sibling repos, existing platform.yaml) via smart routing.
    Removed in the next release cycle following cli-init-smart-entry-point ship.
    """
    print(
        "\nℹ  'otaman onboard program-init' is deprecated.\n"
        "   Run 'otaman init' instead — it handles all starting states.\n",
        file=sys.stderr,
    )
    from otaman_cli.onboard.program_init import run_program_init
    return run_program_init(args)


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

    # program-init
    # DEPRECATED: superseded by `otaman init` (cli-init-smart-entry-point).
    # Kept as alias for one release cycle; remove after that ship.
    p_prog = sub.add_parser(
        "program-init",
        help=(
            "DEPRECATED — use `otaman init` instead. Interactive program "
            "initialisation wizard — generates platform.yaml, scaffolds "
            "companion repos, configures roles and processes"
        ),
    )
    p_prog.add_argument(
        "--program", "-p",
        metavar="SLUG",
        help="program slug (kebab-case); asked interactively if omitted",
    )
    p_prog.add_argument(
        "--questions-yaml",
        metavar="PATH",
        help=(
            "override path to program-init-questions.yaml "
            "(default: ../otaman-meta/onboarding/program-init-questions.yaml)"
        ),
    )
    p_prog.add_argument(
        "--mode",
        type=int,
        choices=[1, 2],
        help="override Mode detection (1 = local, 2 = Mode 2+/Zitadel)",
    )
    p_prog.add_argument(
        "--dry-run",
        action="store_true",
        help="preview companion-repo scaffolding without writing to disk",
    )
    p_prog.add_argument(
        "--output-dir",
        metavar="PATH",
        help="override output directory for generated platform.yaml",
    )
    p_prog.set_defaults(func=_cmd_program_init)


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
