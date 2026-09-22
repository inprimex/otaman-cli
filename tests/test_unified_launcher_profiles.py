"""unified-launcher-profiles 1.1 + 1.3.

The launcher gains the ABILITY to seat the human console without gaining any
ability to reach it (D1). Most of this file is about what `seat_console` must
REFUSE, because that is the half where a mistake is not a bug but a breach of
the never-inject boundary: a console seated on the fleet socket is a human
session sitting inside the server the fleet can drive.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from otaman_cli.console import seat as seat_mod
from otaman_cli.console.seat import (
    FLEET_REFUSAL,
    PRIVATE_SOCKET,
    SEAT_ALREADY,
    SEAT_CREATED,
    SEAT_REFUSED_SOCKET,
    SEAT_UNAVAILABLE,
    seat_console,
)
from otaman_cli.launch_profiles import (
    LaunchConfig,
    Profile,
    parse,
    validate,
)


class _Tmux:
    """Records every tmux invocation so a test can assert on the WHOLE call."""

    def __init__(self, *, has_session: bool = False, rc: int = 0):
        self.calls: list[list[str]] = []
        self._has = has_session
        self._rc = rc

    def __call__(self, args, **kw):
        self.calls.append(list(args))
        if "has-session" in args:
            return SimpleNamespace(returncode=0 if self._has else 1, stdout="", stderr="")
        return SimpleNamespace(returncode=self._rc, stdout="", stderr="")


@pytest.fixture(autouse=True)
def _tmux_present(monkeypatch):
    monkeypatch.setattr(seat_mod, "tmux_available", lambda: True)


# ---------------------------------------------------------------------------
# 1.3 — the boundary


def test_a_fleet_socket_is_refused_and_nothing_is_created():
    """The breach case. Refusal must be total, not a downgrade."""
    tmux = _Tmux()
    outcome, message = seat_console(socket="default", run=tmux)

    assert outcome == SEAT_REFUSED_SOCKET
    assert tmux.calls == [], "a refused seat still talked to tmux"
    assert message == FLEET_REFUSAL, "refusal must be seat.py's sentence, verbatim (D1)"


@pytest.mark.parametrize("bad", ["default", "fleet", "/tmp/tmux-1000/default", "otaman-fleet"])
def test_every_non_private_socket_is_refused(bad):
    tmux = _Tmux()
    outcome, _ = seat_console(socket=bad, run=tmux)
    assert outcome == SEAT_REFUSED_SOCKET
    assert tmux.calls == []


def test_the_seat_runs_on_the_private_socket_detached():
    tmux = _Tmux()
    outcome, _ = seat_console(run=tmux)

    assert outcome == SEAT_CREATED
    create = [c for c in tmux.calls if "new-session" in c][0]
    assert create[:3] == ["tmux", "-L", PRIVATE_SOCKET], create
    assert "-d" in create, "the launcher seats DETACHED; it has other things to start"


def test_the_seat_never_uses_spawn_or_a_remote_path():
    """Host-local only: agents may be mesh/ssh-remote, the human seat is not."""
    tmux = _Tmux()
    seat_console(run=tmux)
    flat = " ".join(" ".join(c) for c in tmux.calls)
    assert "/spawn" not in flat
    assert "ssh" not in flat


def test_an_existing_seat_is_left_exactly_as_it_is():
    """Idempotent: re-running the launcher must not stack a second console, and
    must never restart the one the human is sitting in."""
    tmux = _Tmux(has_session=True)
    outcome, message = seat_console(run=tmux)

    assert outcome == SEAT_ALREADY
    assert not [c for c in tmux.calls if "new-session" in c], "created a second seat"
    assert not [c for c in tmux.calls if "kill-session" in c], "restarted the human's seat"
    assert "already running" in message


def test_identity_is_inherited_when_the_environment_carries_it():
    tmux = _Tmux()
    seat_console(run=tmux, env={"OTAMAN_HUMAN": "roman"})
    create = [c for c in tmux.calls if "new-session" in c][0]
    assert "OTAMAN_HUMAN=roman" in create


def test_identity_is_never_fabricated_when_absent():
    """The one that matters: inventing a value here forges exactly the
    attestation the console exists to obtain. Absent must stay absent."""
    tmux = _Tmux()
    outcome, message = seat_console(run=tmux, env={})

    assert outcome == SEAT_CREATED
    create = [c for c in tmux.calls if "new-session" in c][0]
    assert not any(part.startswith("OTAMAN_HUMAN=") for part in create), create
    assert "will ask who you are" in message, "an unverified seat must say so"


def test_a_tmux_failure_reports_failure_rather_than_success():
    """no-silent-success: a seat that did not happen must not read as one."""
    tmux = _Tmux(rc=1)
    outcome, message = seat_console(run=tmux)
    assert outcome == SEAT_UNAVAILABLE
    assert "Could not create" in message


def test_missing_tmux_is_reported_not_ignored(monkeypatch):
    monkeypatch.setattr(seat_mod, "tmux_available", lambda: False)
    outcome, _ = seat_console(run=_Tmux())
    assert outcome == SEAT_UNAVAILABLE


# ---------------------------------------------------------------------------
# 1.1 — the schema


def test_an_absent_launch_block_is_none_not_a_default():
    """D3: absent block = legacy behaviour. The caller must be able to SEE that
    rather than infer it from an all-defaults object."""
    assert parse({}) is None
    assert parse({"connections": {}}) is None
    assert parse(None) is None


def test_menu_order_is_full_then_pick_then_profiles_in_declaration_order():
    cfg = parse(
        {
            "launch": {
                "profiles": [
                    {"name": "zeta", "agents": ["a"]},
                    {"name": "alpha", "agents": ["b"]},
                ]
            }
        }
    )
    positions = [(n, key) for n, key, _label in cfg.menu()]
    assert positions == [(1, "full"), (2, "pick"), (3, "zeta"), (4, "alpha")], (
        "profiles must keep DECLARATION order — re-sorting renumbers a menu "
        "operators learn by position"
    )


def test_a_profile_resolves_by_name_and_by_menu_position():
    cfg = parse({"launch": {"profiles": [{"name": "backend", "agents": ["core-agent"]}]}})
    assert cfg.profile("backend").agents == ("core-agent",)
    assert cfg.profile("3").name == "backend"
    assert cfg.profile("nope") is None
    assert cfg.profile("9") is None


def test_the_fleet_socket_is_rejected_at_validation_too():
    """The earlier of the two gates — finding this at validation beats finding
    it when a seat lands somewhere fleet-reachable."""
    cfg = parse({"launch": {"console": {"socket": "default"}}})
    errors = validate(cfg)
    assert errors and any(FLEET_REFUSAL in e for e in errors)


def test_the_private_socket_validates_clean():
    cfg = parse({"launch": {"console": {"socket": PRIVATE_SOCKET}}})
    assert validate(cfg) == []


def test_an_absent_block_validates_clean():
    """Legacy behaviour is a valid configuration, not a missing one."""
    assert validate(None) == []


@pytest.mark.parametrize("name", ["full", "pick"])
def test_a_profile_may_not_shadow_a_reserved_menu_entry(name):
    cfg = parse({"launch": {"profiles": [{"name": name, "agents": ["a"]}]}})
    assert any("reserved" in e for e in validate(cfg))


def test_a_numeric_profile_name_is_rejected():
    cfg = parse({"launch": {"profiles": [{"name": "3", "agents": ["a"]}]}})
    assert any("numeric" in e for e in validate(cfg))


def test_duplicate_profile_names_are_rejected():
    cfg = parse(
        {"launch": {"profiles": [{"name": "x", "agents": ["a"]}, {"name": "x", "agents": ["b"]}]}}
    )
    assert any("duplicate" in e for e in validate(cfg))


def test_a_profile_that_launches_nothing_is_rejected():
    cfg = parse({"launch": {"profiles": [{"name": "empty"}]}})
    assert any("launches nothing" in e for e in validate(cfg))


def test_a_console_only_profile_is_allowed():
    cfg = parse({"launch": {"profiles": [{"name": "just-console", "console": True}]}})
    assert validate(cfg) == []


def test_unknown_agents_are_reported_when_the_roster_is_known():
    cfg = parse({"launch": {"profiles": [{"name": "p", "agents": ["ghost-agent"]}]}})
    assert any("ghost-agent" in e for e in validate(cfg, known_agents={"core-agent"}))


def test_one_malformed_profile_does_not_cost_the_whole_menu():
    """Tolerant parse, strict validate: a tenant with one bad row still gets a
    menu, and is told what is wrong with the row."""
    cfg = parse({"launch": {"profiles": [{"name": "good", "agents": ["a"]}, "not-a-mapping", {}]}})
    assert [p.name for p in cfg.profiles] == ["good"]


def test_a_scalar_agents_value_is_accepted_as_one_agent():
    cfg = parse({"launch": {"profiles": [{"name": "p", "agents": "solo-agent"}]}})
    assert cfg.profiles[0].agents == ("solo-agent",)


def test_include_console_defaults_off():
    assert parse({"launch": {}}).include_console is False
    assert parse({"launch": {"include_console": True}}).include_console is True


def test_menu_labels_mark_a_console_profile():
    cfg = LaunchConfig(profiles=(Profile(name="p", agents=("a",), console=True),))
    assert "console" in cfg.menu()[2][2]
