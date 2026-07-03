"""Tests for otaman_cli.commands — the F020 strangler-fig command registry.

Foundation for retiring main.py's single `commands = {...}` dict one
command group at a time (see finding F020). These tests cover the
registry mechanics in isolation; they don't exercise any real CLI command
since nothing has migrated yet.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def registry():
    """The commands module with its registry dict isolated per test.

    ``_REGISTRY`` is module-level state shared with the rest of the test
    session (e.g. test_help_coverage.py imports the same module object),
    so tests here must not leak registrations into it -- snapshot/restore
    rather than mutate-and-leave.
    """
    from otaman_cli import commands as c
    saved = dict(c._REGISTRY)
    c._REGISTRY.clear()
    try:
        yield c
    finally:
        c._REGISTRY.clear()
        c._REGISTRY.update(saved)


class TestCommandSpec:

    def test_register_and_get(self, registry) -> None:
        handler = lambda args: 0  # noqa: E731
        spec = registry.CommandSpec(name="frobnicate", handler=handler, help="does a thing")
        registry.register(spec)
        assert registry.get("frobnicate") is spec

    def test_get_unregistered_returns_none(self, registry) -> None:
        assert registry.get("nope") is None

    def test_duplicate_registration_raises(self, registry) -> None:
        handler = lambda args: 0  # noqa: E731
        registry.register(registry.CommandSpec(name="dup", handler=handler))
        with pytest.raises(ValueError, match="already registered"):
            registry.register(registry.CommandSpec(name="dup", handler=handler))

    def test_registered_names(self, registry) -> None:
        handler = lambda args: 0  # noqa: E731
        registry.register(registry.CommandSpec(name="a", handler=handler))
        registry.register(registry.CommandSpec(name="b", handler=handler))
        assert registry.registered_names() == frozenset({"a", "b"})

    def test_registered_names_empty_by_default(self, registry) -> None:
        assert registry.registered_names() == frozenset()


class TestDispatch:

    def test_dispatch_calls_registered_handler_with_args(self, registry) -> None:
        received = []

        def handler(args: list[str]) -> int:
            received.append(args)
            return 7

        registry.register(registry.CommandSpec(name="thing", handler=handler))
        result = registry.dispatch("thing", ["--flag", "value"])
        assert result == 7
        assert received == [["--flag", "value"]]

    def test_dispatch_unregistered_returns_none(self, registry) -> None:
        """None (not 0/non-zero) signals 'not migrated yet' so the caller
        in main.py knows to fall back to the legacy dict rather than
        treating this as a real exit code.
        """
        assert registry.dispatch("never-registered", []) is None


# Commands migrated out of main.py so far, in migration order. Extend this
# tuple as each new F020 migration PR lands -- it's the single source of
# truth both tests below check against.
MIGRATED_COMMANDS = ("outcome", "solution", "persona", "hitl", "project", "pm", "git-host", "upgrade", "blocked", "scan", "check", "approve", "complete", "doctor", "migrate")


class TestMigratedCommands:
    """Regression guard for F020 migrations out of main.py's legacy dispatch
    (early special-case if-branches for outcome/solution/persona/hitl/
    project/pm, and the `commands = {...}` dict entry for git-host). Uses
    the real production registry, not the isolated `registry` fixture.
    """

    def test_migrated_commands_are_registered(self) -> None:
        from otaman_cli import commands as c
        for name in MIGRATED_COMMANDS:
            assert name in c.registered_names(), f"'{name}' did not register on import"

    def test_no_longer_in_legacy_dispatcher_dict(self) -> None:
        """Migrated commands must be gone from the `commands = {...}` dict
        in main.py -- otherwise F022's "duplicate dead entries" problem is
        still there, just with the registry as an extra layer on top.
        """
        import re
        from pathlib import Path

        main_src = Path(__file__).resolve().parent.parent / "src" / "otaman_cli" / "main.py"
        src = main_src.read_text(encoding="utf-8")
        dispatcher_pattern = re.compile(r'^\s*"(?P<name>[a-z][a-z0-9-]*)"\s*:\s*lambda\b', re.MULTILINE)
        dict_names = set(dispatcher_pattern.findall(src))
        for name in MIGRATED_COMMANDS:
            assert name not in dict_names, f"'{name}' is still a dict entry in main.py"
