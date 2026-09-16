"""status-heartbeat 1.2/1.3/1.4 — a dead session must stop claiming work.

Measured 2026-09-16 07:58 UTC: `otaman status` reported 4 working / 1 waiting
while `updated_at == since` for EVERY record — nothing had been touched since
its state was set. cli's "working" record was 8h15m old; deploy had been
"working" 7.5h with its last commit 13h old. The surface could not tell
"working for eight hours" from "died eight hours ago" and reported the louder
one, so spec-agent had to diff git commit times against status timestamps to
decide whether to nudge.

Heartbeats (plugin 1.1) keep a live record fresh; the staleness rule is the
render-time half, so a crashed session cannot present as busy even when nothing
refreshes it again.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from otaman_cli.status.models import AgentStatus, State
from otaman_cli.status.staleness import (
    DEFAULT_TTL_SECONDS,
    age_seconds,
    human_age,
    is_stale,
    last_seen,
    render_state,
    stale_note,
    ttl_seconds,
)

NOW = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)


def _rec(state: str, *, age_s: int, task: str | None = "1.1 a task") -> AgentStatus:
    stamp = (NOW - timedelta(seconds=age_s)).isoformat(timespec="seconds").replace("+00:00", "Z")
    return AgentStatus(
        agent="some-agent",
        state=State(state) if state in {s.value for s in State} else state,
        task=task,
        since=stamp,
        updated_at=stamp,
    )


# ---------------------------------------------------------------------------
# 1.4 — fresh renders active, stale renders STALE with last-seen


def test_fresh_working_renders_active():
    r = _rec("working", age_s=60)
    assert is_stale(r, now=NOW) is False
    assert render_state(r, now=NOW) == "working"
    assert stale_note(r, now=NOW) == ""


def test_stale_working_renders_stale_with_last_seen():
    """The incident's own record: working, 8h15m untouched."""
    r = _rec("working", age_s=29700)
    assert is_stale(r, now=NOW) is True
    assert render_state(r, now=NOW) == "STALE"
    note = stale_note(r, now=NOW)
    assert "STALE" in note
    assert "was working" in note  # what was claimed
    assert "8h15m" in note  # and when it was last true


def test_stale_waiting_also_renders_stale():
    r = _rec("waiting", age_s=29700)
    assert render_state(r, now=NOW) == "STALE"


def test_a_heartbeat_keeps_a_live_session_fresh_across_the_ttl():
    """The whole point of the belt-and-braces design: a session alive but quiet
    past the TTL stays honest BECAUSE its record was refreshed."""
    stamp_old = (
        (NOW - timedelta(seconds=DEFAULT_TTL_SECONDS * 3))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    refreshed = (NOW - timedelta(seconds=30)).isoformat(timespec="seconds").replace("+00:00", "Z")
    r = AgentStatus(
        agent="a",
        state=State.WORKING,
        task="1.1 t",
        since=stamp_old,  # entered the state long ago...
        updated_at=refreshed,  # ...but the heartbeat touched it just now
    )
    assert is_stale(r, now=NOW) is False
    assert render_state(r, now=NOW) == "working"


@pytest.mark.parametrize("state", ["idle", "blocked"])
def test_idle_and_blocked_are_exempt(state):
    """An idle agent claims nothing; a blocked one states a fact that stays true
    without anyone refreshing it."""
    r = _rec(state, age_s=999999, task=None)
    assert is_stale(r, now=NOW) is False
    assert render_state(r, now=NOW) == state


def test_an_unknown_state_is_exempt():
    """The live fleet has `human` written as state `afk`, 89 days old, and that
    is correct — a deliberate human state, not an unheard-from session. Calling
    it STALE would be the same confidently-wrong failure pointed the other way.

    Belt and braces here too: through the file backend `afk` is coerced to
    `idle` by `AgentStatus.from_dict` (unknown states fall back), so the live
    path is exempt for that reason as well. This pins the rule for a state that
    reaches the renderer un-coerced, so a future state added to the enum's
    vocabulary but not to STALEABLE cannot start accusing agents of being dead.
    """
    r = AgentStatus(agent="human", state="afk", updated_at="2026-06-19T00:00:00Z")
    assert is_stale(r, now=NOW) is False
    assert render_state(r, now=NOW) == "afk"
    # and the coerced path is exempt too
    coerced = AgentStatus.from_dict(
        {"agent": "human", "state": "afk", "updated_at": "2026-06-19T00:00:00Z"}
    )
    assert coerced.state is State.IDLE
    assert is_stale(coerced, now=NOW) is False


def test_an_unparseable_stamp_is_not_an_accusation():
    r = AgentStatus(agent="a", state=State.WORKING, task="t", updated_at="not-a-date")
    assert age_seconds(r, now=NOW) is None
    assert is_stale(r, now=NOW) is False  # doubt the reader, not the agent


def test_exactly_at_the_ttl_is_not_yet_stale():
    assert is_stale(_rec("working", age_s=DEFAULT_TTL_SECONDS), now=NOW) is False
    assert is_stale(_rec("working", age_s=DEFAULT_TTL_SECONDS + 1), now=NOW) is True


# ---------------------------------------------------------------------------
# TTL configuration


def test_ttl_defaults_when_unset(tmp_path):
    (tmp_path / "platform.yaml").write_text("project: p\nversion: '1.0'\n", encoding="utf-8")
    assert ttl_seconds(tmp_path) == DEFAULT_TTL_SECONDS


@pytest.mark.parametrize(
    "doc",
    [
        "project: p\nversion: '1.0'\nagent_presence_ttl_seconds: 60\n",
        "project: p\nversion: '1.0'\nplatform:\n  agent_presence_ttl_seconds: 60\n",
    ],
)
def test_ttl_is_configurable_top_level_and_nested(tmp_path, doc):
    (tmp_path / "platform.yaml").write_text(doc, encoding="utf-8")
    from otaman_cli.yaml_fast import clear_cache

    clear_cache()
    assert ttl_seconds(tmp_path) == 60


@pytest.mark.parametrize("bad", ["0", "-5", "'abc'", "null"])
def test_a_broken_ttl_falls_back_rather_than_disabling_the_rule(tmp_path, bad):
    """A typo in the TTL must not silently switch off dead-session detection."""
    (tmp_path / "platform.yaml").write_text(
        f"project: p\nversion: '1.0'\nagent_presence_ttl_seconds: {bad}\n", encoding="utf-8"
    )
    from otaman_cli.yaml_fast import clear_cache

    clear_cache()
    assert ttl_seconds(tmp_path) == DEFAULT_TTL_SECONDS


def test_a_short_ttl_makes_a_recent_record_stale(tmp_path):
    (tmp_path / "platform.yaml").write_text(
        "project: p\nversion: '1.0'\nagent_presence_ttl_seconds: 30\n", encoding="utf-8"
    )
    from otaman_cli.yaml_fast import clear_cache

    clear_cache()
    assert is_stale(_rec("working", age_s=60), ttl=ttl_seconds(tmp_path), now=NOW) is True


# ---------------------------------------------------------------------------
# formatting


@pytest.mark.parametrize(
    ("secs", "text"),
    [(0, "0s"), (45, "45s"), (90, "1m"), (3600, "1h0m"), (29700, "8h15m"), (90000, "1d1h")],
)
def test_human_age_formats(secs, text):
    assert human_age(secs) == text


def test_last_seen_names_the_time():
    assert last_seen(_rec("working", age_s=29700), now=NOW) == "last seen 8h15m ago"


# ---------------------------------------------------------------------------
# 1.3 — working requires a task


def _fake_root(tmp_path, monkeypatch, existing_task=None):
    """A project whose backend holds one record, with set-status wired to it."""
    root = tmp_path / "proj"
    (root / ".agents" / "status").mkdir(parents=True)
    (root / "platform.yaml").write_text("project: p\nversion: '1.0'\n", encoding="utf-8")
    monkeypatch.setattr("otaman_cli.commands.status_cluster.find_project_root", lambda: root)
    monkeypatch.setattr(
        "otaman_cli.commands.status_cluster.resolve_agent_identity",
        lambda _r, explicit=None: "cli-agent",
    )
    if existing_task is not None:
        from otaman_cli.status import get_backend

        get_backend(root).write(
            AgentStatus(agent="cli-agent", state=State.WORKING, task=existing_task)
        )
    return root


def test_working_with_no_task_anywhere_is_refused(tmp_path, monkeypatch, capsys):
    from otaman_cli.commands.status_cluster import cmd_set_status

    _fake_root(tmp_path, monkeypatch)
    rc = cmd_set_status(["working"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "requires a task" in out
    assert "--task" in out  # names the fix


def test_working_with_a_task_is_recorded(tmp_path, monkeypatch):
    from otaman_cli.commands.status_cluster import cmd_set_status
    from otaman_cli.status import get_backend

    root = _fake_root(tmp_path, monkeypatch)
    assert cmd_set_status(["working", "--task", "1.2 staleness"]) == 0
    assert get_backend(root).read("cli-agent").task == "1.2 staleness"


def test_omitting_task_is_fine_when_one_is_already_on_the_record(tmp_path, monkeypatch):
    """The measured reason this is a resulting-task check, not a --task check:
    re-affirming work already recorded is legitimate and must not be refused."""
    from otaman_cli.commands.status_cluster import cmd_set_status
    from otaman_cli.status import get_backend

    root = _fake_root(tmp_path, monkeypatch, existing_task="1.1 earlier work")
    assert cmd_set_status(["working"]) == 0
    assert get_backend(root).read("cli-agent").task == "1.1 earlier work"


def test_a_whitespace_only_task_is_still_bare(tmp_path, monkeypatch, capsys):
    from otaman_cli.commands.status_cluster import cmd_set_status

    _fake_root(tmp_path, monkeypatch)
    assert cmd_set_status(["working", "--task", "   "]) == 2
    assert "requires a task" in capsys.readouterr().out


@pytest.mark.parametrize("state", ["idle", "waiting", "blocked"])
def test_other_states_do_not_require_a_task(tmp_path, monkeypatch, state):
    """1.3 scopes the refusal to `working` — the state that claims active work."""
    from otaman_cli.commands.status_cluster import cmd_set_status

    _fake_root(tmp_path, monkeypatch)
    assert cmd_set_status([state]) == 0


def test_the_internal_complete_hook_bypasses_the_verb(tmp_path, monkeypatch):
    """Measurement, pinned: `otaman complete` writes working/task=None
    deliberately ("per spec" in its own comment) by constructing AgentStatus
    directly. The verb-level refusal must not reach it — that call is reported
    to spec-agent for a ruling, not changed here."""
    import inspect

    from otaman_cli.commands import complete

    src = inspect.getsource(complete)
    assert "state=State.WORKING" in src
    assert "cmd_set_status" not in src  # it does not go through the verb
