"""Setup section — shell-out wrappers over the tested CLI verbs (console-ux-redesign wave 2 / D4).

Setup is shell-out-first: rather than re-implement administration inside the TUI,
it VISIBLY runs the existing, tested ``otaman`` verbs against the picked program
and surfaces their results (S7). Every verb here is read-mostly and values-free —
``connection map`` is the values-free credentials map (layers/locations, never
values), ``human list`` is the team roster. Mutating/wizard flows (program-init,
project create/update/delete, connection create/update/delete) build on this same
runner in a later slice; destructive lifecycle transitions stay governed by
program-lifecycle (status change, never raw delete — D4).

The runner is Textual-free and injectable so the execution round-trip is unit
-testable without spawning a real process.
"""

from __future__ import annotations

from dataclasses import dataclass

from otaman_cli.console.bus import Program


@dataclass(frozen=True)
class SetupVerb:
    label: str
    argv: tuple[str, ...]
    note: str = ""


#: The read-first Setup menu (list before mutate, D4). Each entry shells out to a
#: real, tested verb; all output is values-free.
SETUP_VERBS: tuple[SetupVerb, ...] = (
    SetupVerb("Projects — list", ("project", "list")),
    SetupVerb("Repos — re-scan (dry-run)", ("sync-repos", "--dry-run")),
    SetupVerb("Team — roster", ("human", "list")),
    SetupVerb("Connections — list", ("connection", "list")),
    # `--all` is REQUIRED: `connection check` alone is a usage error (it wants
    # `<name>` or `--all`), so the panel showed that error instead of health on
    # every run (deploy-agent, 2026-09-15). The menu has no target to offer, so
    # checking them all is the meaningful read-first action.
    SetupVerb("Connections — health check", ("connection", "check", "--all")),
    SetupVerb("Credentials map (values-free)", ("connection", "map")),
    # console-ia-consolidation 5.2 / D7 — the recorded-but-unbuilt surfaces
    # (policy, connections, blocked, watchdog) are reachable HERE, not from a
    # top-level door each. Connections already lived here; these three join it,
    # so four planned screens cost zero doors.
    SetupVerb("Policy — registered packs", ("policy", "list")),
    SetupVerb("Blocked — registered items", ("blocked", "--list")),
    # `--list`, not `list`: bare `blocked <slug>` REGISTERS, so `blocked list`
    # used to create an entry named "list". Fixed in commands/blocked.py, but
    # the flag form is the one that works on every installed version.
    SetupVerb(
        "Watchdog — status",
        ("watchdog", "status"),
        note="needs a reachable runner endpoint",
    ),
)


def setup_item_label(verb: SetupVerb) -> str:
    """The menu row for *verb*: label, the command it shells out to, and any
    caveat.

    Textual-free so the row is unit-testable without mounting a widget, like the
    runner below. A verb whose result depends on the environment — watchdog
    needs a reachable runner — says so BEFORE it is run, not only in its error
    output; `note` existed but was never rendered (console-ia 5.2).
    """
    tail = f"  — {verb.note}" if verb.note else ""
    return f"{verb.label}   ($ otaman {' '.join(verb.argv)}){tail}"


@dataclass(frozen=True)
class VerbResult:
    argv: tuple[str, ...]
    returncode: int
    output: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def command(self) -> str:
        return "otaman " + " ".join(self.argv)


def _otaman_bin() -> str:
    import shutil
    import sys

    return shutil.which("otaman") or (sys.argv[0] if sys.argv and sys.argv[0] else "otaman")


def run_verb(program: Program, argv, *, runner=None, timeout: int = 60) -> VerbResult:
    """Shell out to ``otaman <argv>`` in *program*'s root and capture the result.

    The console runs the SAME verbs a human would at the shell (D4), scoped to the
    picked program via cwd. *runner* is injectable (defaults to subprocess.run) so
    the round-trip is testable without a real process."""
    import subprocess

    argv = tuple(argv)
    runner = runner or subprocess.run
    cmd = [_otaman_bin(), *argv]
    try:
        r = runner(cmd, cwd=str(program.root), capture_output=True, text=True, timeout=timeout)
        output = ((r.stdout or "") + (r.stderr or "")).strip()
        return VerbResult(argv=argv, returncode=r.returncode, output=output or "(no output)")
    except Exception as exc:  # noqa: BLE001 - surface any spawn failure, never crash the TUI
        return VerbResult(
            argv=argv, returncode=1, output=f"failed to run `otaman {' '.join(argv)}`: {exc}"
        )


__all__ = ["SETUP_VERBS", "SetupVerb", "VerbResult", "run_verb"]
