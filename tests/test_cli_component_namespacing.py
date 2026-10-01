"""cli-component-namespacing 1.1–1.3 — `otaman cli <verb>`.

Install- and console-related commands were scattered across three shapes:
`otaman install-cli` (hyphenated top-level verb), `otaman -i` (top-level flag), and
`otaman runner <verb>` (component-first, already shipping). Roman hit the scatter on
the sunflowers fresh tenant — `install-cli` broken and `-i` missing the console extra
in one session, reported SEPARATELY because they did not look like one problem.

The delta rules component-first. The two old spellings are kept, and deliberately on
different terms:

* **`otaman install-cli` is a DEPRECATION alias** — still works, prints a notice
  naming the new home, removed by a later change. A tenant mid-install must not get a
  broken command because a name moved.
* **`otaman -i` is a PERMANENT alias** — baked into every generated CLAUDE.local.md on
  this fleet. The delta says "permanently documented", so it prints NO notice:
  warning someone about a spelling that is not going anywhere teaches them to ignore
  warnings.

Both route through the namespaced verb, so the spellings cannot drift in behavior —
which is the failure the scatter produced.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from otaman_cli.commands import cli_group
from otaman_cli.commands.cli_group import DEPRECATION_NOTICE, cmd_cli

MAIN = Path(__import__("otaman_cli.main", fromlist=["x"]).__file__)


def _record(store: dict, key: str):
    """A stand-in that records its argument and returns 0.

    Written out rather than `lambda a: store.setdefault(key, a) or 0` — `setdefault`
    returns the stored VALUE, so a non-empty list came back as the return code and
    three tests failed comparing a list to 0.
    """

    def handler(arg):
        store[key] = arg
        return 0

    return handler


# ---------------------------------------------------------------------------
# 1.1 — install has a component-first home, and the old spelling still works.


def test_cli_install_is_registered_under_the_cli_group():
    from otaman_cli import commands as registry

    assert registry.dispatch("cli", ["--help"]) == 0, "`otaman cli` must be a real command"


def test_cli_install_delegates_to_the_one_implementation(monkeypatch):
    """The SAME handler the old spelling used. Two implementations of an install is
    how the two spellings would come to behave differently while both exist."""
    seen = {}
    import otaman_cli.commands.simple_dispatch as sd

    monkeypatch.setattr(sd, "cmd_install_cli", _record(seen, "args"))

    assert cmd_cli(["install", "--apply", "--prefix", "/tmp/x"]) == 0
    assert seen["args"] == ["--apply", "--prefix", "/tmp/x"], "flags must pass through intact"


def test_the_deprecated_spelling_still_works_and_names_the_new_home(monkeypatch, capsys):
    """THE compatibility requirement. A notice that does not say what to run instead
    only tells the reader they are wrong."""
    import otaman_cli.commands.simple_dispatch as sd

    monkeypatch.setattr(sd, "run_script", lambda *a, **kw: type("R", (), {"returncode": 0})())

    rc = sd.cmd_install_cli_deprecated(["--apply"])
    out = capsys.readouterr().out

    assert rc == 0, "the alias must still perform the install"
    assert "deprecated" in out
    assert "otaman cli install" in out, "the notice must name the new home"


def test_the_namespaced_verb_prints_no_deprecation_notice(monkeypatch, capsys):
    """Only the old spelling is deprecated. A notice on the new home would be a
    warning about the thing the reader was just told to use."""
    import otaman_cli.commands.simple_dispatch as sd

    monkeypatch.setattr(sd, "run_script", lambda *a, **kw: type("R", (), {"returncode": 0})())

    cmd_cli(["install"])
    out = capsys.readouterr().out

    assert "deprecated" not in out.lower()


def test_the_deprecated_spelling_is_still_registered():
    """Registered, not deleted — this change does not remove it (scope guard: the
    removal timing is cli-agent's call, in a LATER change)."""
    from otaman_cli import commands as registry

    assert registry.get("install-cli") is not None


def test_the_registry_help_marks_the_alias_deprecated():
    """`otaman help` is where a reader discovers the move. A still-working alias that
    advertises itself identically to the new home teaches nothing."""
    from otaman_cli import commands as registry

    spec = registry.get("install-cli")
    assert "DEPRECATED" in spec.help
    assert "cli install" in spec.help


# ---------------------------------------------------------------------------
# 1.2 — the console is reachable by both spellings.


def test_cli_interactive_reaches_run_console(monkeypatch):
    seen = {}
    import otaman_cli.console.launch as launch

    monkeypatch.setattr(launch, "run_console", _record(seen, "argv"))

    assert cmd_cli(["interactive", "--no-seat"]) == 0
    assert seen["argv"] == ["--no-seat"]


@pytest.mark.parametrize("flag", ["-i", "--interactive"])
def test_the_permanent_alias_routes_through_the_namespaced_verb(monkeypatch, flag):
    """Routed through `cmd_cli`, not straight to `run_console`: the two spellings
    cannot drift if only one of them knows how to launch the thing."""
    seen = {}
    monkeypatch.setattr(cli_group, "cmd_cli", _record(seen, "args"))

    monkeypatch.setattr("sys.argv", ["otaman", flag, "--no-seat"])
    from otaman_cli.main import main

    assert main() == 0
    assert seen["args"] == ["interactive", "--no-seat"]


@pytest.mark.parametrize("flag", ["-i", "--interactive"])
def test_the_permanent_alias_prints_no_deprecation_notice(monkeypatch, capsys, flag):
    """The delta says "retained as a permanently documented alias". It is in every
    generated CLAUDE.local.md on this fleet; warning about it would be noise that
    teaches operators to skim warnings."""
    import otaman_cli.console.launch as launch

    monkeypatch.setattr(launch, "run_console", lambda argv: 0)

    monkeypatch.setattr("sys.argv", ["otaman", flag])
    from otaman_cli.main import main

    main()
    out = capsys.readouterr().out

    assert "deprecat" not in out.lower()


def test_console_is_accepted_as_a_synonym(monkeypatch):
    """`console` is what the thing is called everywhere else in this repo (console/,
    run_console, the `console` extra). A synonym INSIDE the group is not the new
    top-level spelling the delta forbids."""
    seen = {}
    import otaman_cli.console.launch as launch

    monkeypatch.setattr(launch, "run_console", _record(seen, "argv"))

    assert cmd_cli(["console"]) == 0
    assert seen["argv"] == []


def test_the_missing_extra_error_names_both_spellings():
    """1.2's explicit requirement, and it named NEITHER. Roman hit a missing extra on
    a fresh tenant and reported it separately from the install-cli breakage, because
    nothing connected the message to the command that produced it."""
    from otaman_cli.console.launch import _INSTALL_HINT

    assert "otaman cli interactive" in _INSTALL_HINT
    assert "otaman -i" in _INSTALL_HINT
    assert "otaman-cli[console]" in _INSTALL_HINT, "and how to install it"


def test_the_missing_extra_path_returns_two_and_prints_the_hint(monkeypatch, capsys):
    """End to end through the import guard, so the hint is reachable and not merely
    well-worded."""
    import builtins

    real_import = builtins.__import__

    def no_textual(name, *a, **kw):
        if name == "textual":
            raise ImportError("no textual")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_textual)
    from otaman_cli.console.launch import run_console

    rc = run_console([])
    out = capsys.readouterr().out

    assert rc == 2
    assert "otaman cli interactive" in out and "otaman -i" in out


# ---------------------------------------------------------------------------
# 1.3 — the help audit, and no second pattern creeping back.


def test_both_verbs_are_listed_under_a_cli_group_in_help():
    src = MAIN.read_text(encoding="utf-8")

    assert "cli install" in src
    assert "cli interactive" in src


def test_help_cross_references_the_permanent_alias():
    """A reader who only ever types `-i` has to be able to find the new home from
    it, and a reader of the new home has to learn the alias still works."""
    src = MAIN.read_text(encoding="utf-8")

    assert re.search(r"cli interactive.*alias.*otaman -i", src, re.IGNORECASE | re.DOTALL)
    assert re.search(r"otaman -i.*cli interactive", src, re.IGNORECASE | re.DOTALL)


def test_no_new_top_level_hyphenated_component_verb_is_registered():
    """The delta's third scenario, enforced rather than trusted: a future
    CLI-self-management verb must land under `otaman cli <verb>`.

    `install-cli` is the registered exception — it is the deprecation alias this
    change keeps. Anything else matching the shape is the pattern creeping back.
    """
    from otaman_cli import commands as registry

    allowed = {
        # The deprecation alias this change deliberately keeps (1.1).
        "install-cli",
        # Pre-existing hyphenated verbs that are NOT component self-management:
        # they act on a program's artifacts, not on an otaman component.
        "validate-messages",
        "mcp-config",
        "emergency-halt",
        "credential-helper",
        "notify-change",
        "set-status",
        "check-ownership",
        "owner-paths",
        "sync-repos",
        "install-hooks",
        "program-init",
        "clone-launcher",
        "set-agent",
        # Pre-existing, and NOT component self-management either: a program's
        # acting lock, its knowledge audit, and its git-host integration. Listed
        # with the reason rather than loosened away, so a genuinely new
        # `otaman <thing>-<verb>` still trips this.
        "acting-lock",
        "audit-knowledge",
        "git-host",
    }
    hyphenated = {name for name in registry.registered_names() if "-" in name}
    unexpected = sorted(hyphenated - allowed)

    assert not unexpected, (
        "new hyphenated top-level verb(s) "
        f"{unexpected} — CLI self-management belongs under `otaman cli <verb>`; "
        "if these are not component management, add them to the allow-list with why"
    )


def test_the_group_usage_names_both_aliases():
    """The usage block a reader lands on when they get the verb wrong."""
    joined = "\n".join(cli_group._CLI_USAGE)

    assert "install-cli" in joined and "deprecated" in joined
    assert "otaman -i" in joined and "permanent" in joined


def test_an_unknown_subcommand_refuses_and_shows_the_usage(capsys):
    assert cmd_cli(["bogus"]) == 1
    out = capsys.readouterr().out

    assert "Unknown cli subcommand: bogus" in out
    assert "otaman cli install" in out


def test_no_subcommand_is_an_error_and_help_is_not(capsys):
    assert cmd_cli([]) == 1
    assert "Missing subcommand" in capsys.readouterr().out
    assert cmd_cli(["--help"]) == 0
    assert "Missing subcommand" not in capsys.readouterr().out


def test_the_deprecation_notice_is_one_string_with_one_home():
    """Both the alias handler and the usage block cite it, so there is one wording to
    keep true."""
    assert "otaman cli install" in DEPRECATION_NOTICE
    assert "deprecated" in DEPRECATION_NOTICE
