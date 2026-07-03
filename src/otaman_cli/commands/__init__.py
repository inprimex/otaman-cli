"""Registry-based command dispatch — the F020 decomposition foundation.

`main.py` currently owns every command's implementation, argument parsing,
and its entry in one hand-written ``commands = {"name": lambda: ...}`` dict
(F020: god file; F022: three competing dispatch conventions). This package
is where command groups land as they migrate out, one at a time.

A migrated command group registers a :class:`CommandSpec` here instead of
adding a dict entry in ``main.py``. The dispatcher in ``main.py`` checks
this registry first and falls back to the legacy dict for anything not yet
migrated — a strangler-fig cutover, so the CLI stays fully functional at
every commit and no single PR has to move all ~50 command groups at once.

Nothing is registered here yet; this module lands with the first
migrated command group.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

Handler = Callable[[list[str]], int]


@dataclass(frozen=True)
class CommandSpec:
    """One top-level `otaman <name> ...` command."""

    name: str
    handler: Handler
    help: str = ""


_REGISTRY: dict[str, CommandSpec] = {}


def register(spec: CommandSpec) -> None:
    """Add a command to the registry.

    Raises if `spec.name` is already registered — a duplicate registration
    is a programming error (two modules claiming the same command name),
    not a runtime condition to handle gracefully.
    """
    if spec.name in _REGISTRY:
        raise ValueError(f"Command '{spec.name}' is already registered")
    _REGISTRY[spec.name] = spec


def get(name: str) -> CommandSpec | None:
    return _REGISTRY.get(name)


def registered_names() -> frozenset[str]:
    return frozenset(_REGISTRY)


def dispatch(name: str, args: list[str]) -> int | None:
    """Run the registered handler for `name`, or return None if `name`
    isn't registered so the caller can fall back to the legacy dict.
    """
    spec = _REGISTRY.get(name)
    if spec is None:
        return None
    return spec.handler(args)


# Migrated command modules register themselves as a side effect of import.
# Importing them here means `import otaman_cli.commands` alone is enough to
# populate the registry -- callers (main.py's dispatcher, this package's own
# tests, test_help_coverage.py) never need to know the list of migrated
# modules. Add a line here as each new command group migrates out of main.py.
from otaman_cli.commands import approve as _approve  # noqa: E402,F401
from otaman_cli.commands import blocked as _blocked  # noqa: E402,F401
from otaman_cli.commands import check as _check  # noqa: E402,F401
from otaman_cli.commands import git_host as _git_host  # noqa: E402,F401
from otaman_cli.commands import hitl as _hitl  # noqa: E402,F401
from otaman_cli.commands import outcome as _outcome  # noqa: E402,F401
from otaman_cli.commands import persona as _persona  # noqa: E402,F401
from otaman_cli.commands import pm as _pm  # noqa: E402,F401
from otaman_cli.commands import project as _project  # noqa: E402,F401
from otaman_cli.commands import scan as _scan  # noqa: E402,F401
from otaman_cli.commands import solution as _solution  # noqa: E402,F401
from otaman_cli.commands import upgrade as _upgrade  # noqa: E402,F401
