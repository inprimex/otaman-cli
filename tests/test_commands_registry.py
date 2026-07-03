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
