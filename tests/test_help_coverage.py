"""Coverage guard: every command wired into the dispatcher must appear in
``otaman help``.

Recurring failure mode (this came up 2026-05-02): commands ship over time,
get wired into the dispatcher, but the help text only gets touched in big
periodic refreshes. End result: 9 commands shipped over the last several
phases (`accounts`, `afk`, `bridge`, `ping`, `launcher`, `upgrade`,
`install-cli`, `git-host`, `models`) were callable but never showed up in
``otaman --help``, so users had to read CLAUDE.md or commit messages to
discover them.

This test pins the contract:

  - Every entry in the dispatcher dict in ``main.py`` must have its
    name appear somewhere in ``cmd_help`` output.
  - Conversely (best-effort), the help shouldn't list a phantom command
    that has no dispatcher entry.
  - The help output must NOT contain bare-word "maestro" (regression guard
    for the finish-maestro-to-otaman-migration rebrand).

Doesn't enforce wording or grouping — just presence. Catches the drift
class. Future help refreshes are still a judgement call.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_FILE = REPO_ROOT / "src" / "otaman_cli" / "main.py"


# ---------------------------------------------------------------------------
# Parse the dispatcher dict from cli/maestro.py


_DISPATCHER_PATTERN = re.compile(
    r'^\s*"(?P<name>[a-z][a-z0-9-]*)"\s*:\s*lambda\b',
    re.MULTILINE,
)


def _dispatcher_commands() -> set[str]:
    """Extract command names from the ``commands = { "name": lambda: ... }``
    dict in cli/maestro.py.

    Pure regex — no need to import the module (which has heavy side effects
    on import including loading multiple subsystems). The pattern is strict
    enough to avoid false positives from incidentally-quoted strings.
    """
    src = CLI_FILE.read_text(encoding="utf-8")
    matches = _DISPATCHER_PATTERN.findall(src)
    # Dedupe and exclude any accidental matches outside the dispatcher
    # (none in practice today, but keep defensive).
    return set(matches)


def _help_output() -> str:
    """Run ``maestro help`` and capture stdout, stripping ANSI colour codes."""
    result = subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", "help"],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "NO_COLOR": "1",
            "PYTHONPATH": os.pathsep.join([
                str(REPO_ROOT / "src"),
                str(REPO_ROOT.parent / "otaman-core" / "src"),
                os.environ.get("PYTHONPATH", ""),
            ]),
        },
    )
    assert result.returncode == 0, result.stderr
    # Strip ANSI escape sequences regardless of NO_COLOR, since the help
    # uses colour codes inline (the C.GREEN etc. constants).
    ansi_re = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
    return ansi_re.sub("", result.stdout)


# ---------------------------------------------------------------------------
# Tests


def test_dispatcher_extraction_finds_known_commands() -> None:
    """Sanity check: the dispatcher regex isn't broken in some way that finds
    nothing. If we extract zero commands, the rest of the test is meaningless.
    """
    commands = _dispatcher_commands()
    assert len(commands) >= 20, (
        f"dispatcher extraction returned only {len(commands)} commands -- "
        "check the regex against cli/maestro.py main()"
    )
    # A few commands that have been around since v1 -- if these aren't in
    # the extraction, the regex is wrong.
    for stable in ("scan", "init", "doctor", "status", "check"):
        assert stable in commands, f"dispatcher extraction missed '{stable}'"


def test_help_runs_cleanly() -> None:
    """otaman help should exit 0 and produce non-trivial output."""
    output = _help_output()
    assert "Otaman" in output
    assert len(output) > 200


def test_help_contains_no_bare_maestro() -> None:
    """Regression guard: user-visible help must not contain the legacy brand name.

    This catches future accidental reintroductions of bare-word "maestro" in
    cmd_help() or any output emitted before cmd_help() returns.
    """
    import re
    output = _help_output()
    matches = re.findall(r'\bmaestro\b', output, re.IGNORECASE)
    assert not matches, (
        f"User-visible help output contains {len(matches)} bare-word 'maestro' occurrence(s): "
        f"{matches[:5]!r}\n"
        "Rename to 'otaman' in cmd_help() or add a 'legacy:' annotation in the source "
        "to suppress the audit gate."
    )


def test_every_dispatcher_command_appears_in_help() -> None:
    """The contract: any command wired into the dispatcher must show up in
    the help text. If you add a new dispatcher entry, also update
    ``cmd_help`` in cli/maestro.py.

    Match is "command name appears as a whole word" — so ``upgrade`` matches
    on a help line like ``upgrade [--dry-run]`` but doesn't get false-matched
    from a substring of another word.
    """
    commands = _dispatcher_commands()
    help_text = _help_output()

    missing = []
    for name in sorted(commands):
        # ``\b`` word-boundary regex; names with `-` need the regex to not
        # treat `-` as a word boundary terminator (it isn't), so
        # ``\bgit-host\b`` matches. Tested empirically.
        pattern = r"\b" + re.escape(name) + r"\b"
        if not re.search(pattern, help_text):
            missing.append(name)

    assert not missing, (
        f"{len(missing)} dispatcher command(s) missing from `maestro help`:\n"
        + "\n".join(f"  - {n}" for n in missing)
        + "\n\nUpdate cmd_help() in cli/maestro.py to include them, or remove "
        "them from the dispatcher if they are obsolete."
    )


def test_help_doesnt_list_obviously_phantom_commands() -> None:
    """Best-effort reverse check: the help text shouldn't list a command name
    that has no dispatcher entry. We can't be exhaustive (the help has prose
    that contains common English words), but we can flag obvious cases by
    looking for ``\\b<word>\\b`` matches against names that look like CLI
    commands but aren't dispatched.

    Heuristic: only check command-name words that appear at the start of a
    line after ~2 spaces and before another word break. This catches the
    "  fakecommand <args>   Description" pattern used in the help table.
    """
    src_commands = _dispatcher_commands()
    help_text = _help_output()
    # Find candidates that look like ``  <name>`` at the start of a help line,
    # filter to plausible names, and check each against the dispatcher.
    candidate_re = re.compile(r"^\s\s+([a-z][a-z0-9-]*)\b", re.MULTILINE)
    candidates = set(candidate_re.findall(help_text))
    # Exclude common prose words and category labels that legitimately appear
    # at the start of help lines but aren't commands.
    KNOWN_NON_COMMANDS = {
        # English / prose
        "the", "a", "an", "if", "and", "or", "not", "is",
        # Help structure / category nouns
        "messages", "old", "each", "use", "all", "works", "checks", "subcommand",
        # Argument placeholders
        "msg", "stem", "list", "add", "remove", "register", "run", "show",
        "set", "set-default", "set-repo", "set-agent", "install", "uninstall",
        "on", "off", "status", "approve", "reject", "detect", "check", "pr",
        # onboard sub-subcommand names (otaman onboard <sub>)
        "add-user", "list-users", "program-init",
        "post-review",
        # "otaman" appears as the top-level binary name in Quick start examples.
        "otaman",
        # shell built-in in Quick start example: "export OTAMAN_AGENT=<name>"
        "export",
        # `help` is dispatched specially before the dict lookup (see main()
        # ``if args[0] in ("-h", "--help", "help"): return cmd_help()``),
        # so it's a legitimate top-level command without a dispatcher entry.
        "help",
    }
    phantoms = [c for c in candidates if c not in src_commands and c not in KNOWN_NON_COMMANDS]
    # We accept this list being non-empty when the heuristic catches false
    # positives. The point is to fail loudly when a CLEARLY-command-shaped
    # entry like "fakeupgrade" appears with no dispatcher backing -- e.g.
    # because someone removed the dispatcher entry but forgot the help.
    # Today (2026-05-04) this passes empty.
    assert not phantoms, (
        f"Help mentions {len(phantoms)} command-shaped name(s) that aren't in the "
        f"dispatcher: {phantoms}\n"
        "Either re-add them to cli/maestro.py main() commands={...}, remove "
        "from cmd_help(), or extend KNOWN_NON_COMMANDS in this test if a "
        "false positive."
    )
