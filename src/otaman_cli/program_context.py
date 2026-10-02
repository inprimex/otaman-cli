"""program-crud-and-context-resolution 1.2 (cli half) — the program in context.

Core owns resolution (`otaman_core.program_context.resolve_program`, core #111) and
its ruled precedence: explicit parameter → cwd walk → TTY picker callback →
non-interactive refusal. Core is deliberately TTY-agnostic, so the two halves that
are the CLI's live here and nowhere else:

**The picker.** Core calls back with the enumerated programs; this supplies the `-i`
selection UX (questionary where it is available, a numbered prompt where it is not)
and returns the chosen program. Its PRESENCE is the "on a TTY" signal in core's
chain, so it is passed only when the session is actually interactive — a picker on a
pipe would hang a script that should have been refused.

**The refusal.** `ProgramContextError` carries the enumerated programs so a surface
renders the ruled shape without re-enumerating: name them and the `--program` form,
and advise `otaman init` ONLY when there are none (init-advice-only-at-zero — the
resolver does not create programs; `init`/`scan` own that).

**The workspace root** is derived, not configured: from inside a program tree it is
four levels above the program dir (`<ws>/orgs/<org>/programs/<program>`), and from
anywhere else it is the human's home — which is where a human launches `otaman -i`
from, and the same assumption the console's picker already makes.

One thing is NOT delegated, and deliberately: whether an enumerated directory is a
PROGRAM. Core's enumeration tests the path SHAPE, and on this machine that reports
three programs where one exists — `orgs/otaman-dev/programs/` also holds two stray
directories (one with `LICENSE`/`SECURITY.md`, one with `scripts/`) left by a botched
copy. So `rows()` annotates each candidate with whether a program can actually be
READ there, using the gate the console picker has used since picker-canonical-
precedence, and the listing says which are not programs instead of offering debris as
if it were. Reported to core-agent; when `enumerate_programs` learns the predicate the
annotation becomes uniformly `True` and this comment is what to delete.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Rendered where a candidate directory has no readable program in it.
NOT_A_PROGRAM = "no program metadata (not a program?)"


def _core() -> Any | None:
    """core's program-context module, or None on a bundle that predates it."""
    try:
        from otaman_core import program_context
    except Exception:  # noqa: BLE001 - absent → the caller refuses with a remedy
        return None
    needed = ("resolve_program", "enumerate_programs", "program_of_path", "ProgramContextError")
    return program_context if all(hasattr(program_context, n) for n in needed) else None


def unavailable() -> str:
    """The refusal when the bundle has no resolver — a remedy, not a traceback."""
    return (
        "this otaman-core does not carry the program-context resolver "
        "(program-crud-and-context-resolution 1.1) — update the bundle"
    )


def workspace_root(cwd: Path | None = None) -> Path:
    """The directory whose `orgs/` tree holds the programs.

    From inside a program tree, derived from the program dir (four levels up); from
    anywhere else, the human's home — where `otaman -i` is launched from, and the same
    assumption the console picker makes. `OTAMAN_WORKSPACE` overrides both, for a
    machine that keeps `orgs/` somewhere else.
    """
    override = os.environ.get("OTAMAN_WORKSPACE")
    if override:
        return Path(override).expanduser()
    core = _core()
    here = (cwd or Path.cwd()).resolve()
    if core is not None:
        found = core.program_of_path(here)
        if found is not None:
            # <ws>/orgs/<org>/programs/<program>
            return found.path.parent.parent.parent.parent
    return Path.home()


def interactive(stream: Any = None) -> bool:
    """Whether to offer the picker at all.

    A TTY check and nothing else: core's chain treats the picker's presence as the
    interactivity signal, so this is the one place that decides it. `OTAMAN_NO_PICKER`
    forces it off for a script that runs attached to a terminal.
    """
    if os.environ.get("OTAMAN_NO_PICKER"):
        return False
    target = stream if stream is not None else sys.stdin
    try:
        return bool(target.isatty())
    except Exception:  # noqa: BLE001 - a stream that cannot say is not a TTY
        return False


def picker(programs: list[Any]) -> Any | None:
    """Core's `Picker`: choose among *programs*, or None to decline.

    Archived programs are offered but MARKED — hiding them would make a human who
    archived one yesterday think it is gone, and core's enumeration deliberately keeps
    them for the same reason.
    """
    if not programs:
        return None
    labels = [
        f"{p.name}  ({p.org}){'  [archived]' if getattr(p, 'archived', False) else ''}"
        for p in programs
    ]
    try:
        import questionary

        answer = questionary.select("Which program?", choices=labels).ask()
    except Exception:  # noqa: BLE001 - no questionary (or no tty): numbered fallback
        answer = None
        print("Which program?")
        for index, label in enumerate(labels, start=1):
            print(f"  {index}. {label}")
        try:
            raw = input(f"  Choice [1-{len(labels)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(labels):
            return programs[int(raw) - 1]
        return None
    if answer is None:
        return None
    return programs[labels.index(answer)] if answer in labels else None


def resolve(
    *,
    explicit: str | None = None,
    cwd: Path | None = None,
    allow_picker: bool | None = None,
) -> Any:
    """The program in context, by core's precedence. Raises core's error, or RuntimeError.

    *allow_picker* defaults to :func:`interactive`; pass False from a surface that must
    never prompt (a hook, a scripted path) and True only when a TTY is certain.
    """
    core = _core()
    if core is None:
        raise RuntimeError(unavailable())
    here = (cwd or Path.cwd()).resolve()
    offer = interactive() if allow_picker is None else allow_picker
    return core.resolve_program(
        explicit=explicit,
        cwd=here,
        workspace_root=workspace_root(here),
        picker=picker if offer else None,
    )


@dataclass(frozen=True)
class Refusal:
    """A refusal, split by how each part should be SHOWN.

    Three roles, not one blob of text: what went wrong, what the alternatives are, and
    what to do next. A surface that printed the whole thing at one severity would shout
    the program list and whisper the remedy, or the reverse.
    """

    error: str
    candidates: tuple[str, ...] = ()
    advice: str = ""


def refusal(error: Any) -> Refusal:
    """The ruled refusal: name the programs and the `--program` form.

    `init` is advised ONLY when there are none. With programs present, telling a human
    to create one is advice for a problem they do not have — they are standing next to
    the thing they meant to target and need its NAME.

    A candidate with no readable program is marked here too. Core's enumeration feeds
    this list, and offering a human a directory that holds only `LICENSE` as somewhere
    to "target one" wastes the one message they get.
    """
    programs = list(getattr(error, "programs", []) or [])
    if not programs:
        return Refusal(
            error=str(error),
            advice="No programs exist on this machine yet — `otaman init` creates one.",
        )
    candidates = []
    for program in programs:
        marks = []
        if getattr(program, "archived", False):
            marks.append("archived")
        if not _readable(getattr(program, "path", Path("/nonexistent"))):
            marks.append(NOT_A_PROGRAM)
        suffix = f"  [{'; '.join(marks)}]" if marks else ""
        candidates.append(f"{program.name}  ({program.org}){suffix}")
    return Refusal(
        error=str(error),
        candidates=tuple(candidates),
        advice="Target one with `--program <name>`, or run from inside its directory.",
    )


def refusal_lines(error: Any) -> list[str]:
    """`refusal` flattened, for a caller that just wants the text."""
    parts = refusal(error)
    lines = [parts.error]
    if parts.candidates:
        lines.append("Available programs:")
        lines.extend(f"  - {c}" for c in parts.candidates)
    if parts.advice:
        lines.append(parts.advice)
    return lines


@dataclass(frozen=True)
class Row:
    """One row of `otaman program list`."""

    name: str
    org: str
    path: Path
    state: str
    readable: bool

    @property
    def note(self) -> str:
        return "" if self.readable else NOT_A_PROGRAM


def _readable(program_dir: Path) -> bool:
    """Whether a program can actually be READ at *program_dir*.

    The gate the console picker has used since picker-canonical-precedence: a meta dir
    holding a platform.yaml. Not a redefinition of what a program is — an annotation
    for the listing, because core's enumeration tests the path shape and this machine
    has two stray directories that pass it.
    """
    try:
        for child in sorted(program_dir.iterdir()):
            if child.is_dir() and (child / "platform.yaml").is_file():
                return True
    except OSError:
        return False
    return False


def rows(cwd: Path | None = None) -> list[Row]:
    """Every enumerated program, annotated with whether it is readable."""
    core = _core()
    if core is None:
        raise RuntimeError(unavailable())
    return [
        Row(
            name=program.name,
            org=program.org,
            path=program.path,
            state=program.state,
            readable=_readable(program.path),
        )
        for program in core.enumerate_programs(workspace_root(cwd))
    ]


__all__ = [
    "NOT_A_PROGRAM",
    "Refusal",
    "Row",
    "interactive",
    "picker",
    "refusal",
    "refusal_lines",
    "resolve",
    "rows",
    "unavailable",
    "workspace_root",
]
