"""propose-hardening — create-exclusive bus-message writes (no silent overwrite).

Bus stems are second-precision, so two messages created in the same second share
a filename; the old `write_text` path silently overwrote the first (cofounder-agent
lost an SCR to this live). `write_message_exclusive` must never overwrite: it
suffixes -2/-3/… on collision and returns the path actually written. Also asserts
`otaman propose` and `otaman send` no longer clobber a same-stem sibling.
"""

from __future__ import annotations

from otaman_cli.bus_write import write_message_exclusive

# ---------------------------------------------------------------------------
# the helper


def test_writes_to_the_requested_path_when_free(tmp_path):
    p = tmp_path / "20260905T202928-a-to-human-spec-change-request.md"
    out = write_message_exclusive(p, "first")
    assert out == p and p.read_text() == "first"


def test_collision_suffixes_and_preserves_the_first(tmp_path):
    p = tmp_path / "20260905T202928-a-to-human-spec-change-request.md"
    a = write_message_exclusive(p, "first")
    b = write_message_exclusive(p, "second")
    c = write_message_exclusive(p, "third")
    assert a == p
    assert b == tmp_path / "20260905T202928-a-to-human-spec-change-request-2.md"
    assert c == tmp_path / "20260905T202928-a-to-human-spec-change-request-3.md"
    # nothing was overwritten — all three distinct bodies survive
    assert p.read_text() == "first"
    assert b.read_text() == "second"
    assert c.read_text() == "third"


def test_creates_parent_dir(tmp_path):
    p = tmp_path / "nested" / "deep" / "m.md"
    out = write_message_exclusive(p, "x")
    assert out.is_file() and out.read_text() == "x"


def test_raises_when_backstop_exhausted(tmp_path, monkeypatch):
    import otaman_cli.bus_write as bw

    monkeypatch.setattr(bw, "_MAX_COLLISION_SUFFIX", 2)
    p = tmp_path / "m.md"
    assert write_message_exclusive(p, "a").name == "m.md"
    assert write_message_exclusive(p, "b").name == "m-2.md"  # suffix 2 allowed
    try:
        write_message_exclusive(p, "c")  # suffix 3 would exceed backstop → raise
        raise AssertionError("expected FileExistsError")
    except FileExistsError:
        pass


# ---------------------------------------------------------------------------
# integration: propose no longer overwrites a same-second sibling


def _fixed_clock(monkeypatch):
    """Freeze the wall clock to a single second so two writes share a stem."""
    import datetime as _dtmod

    fixed = _dtmod.datetime(2026, 9, 5, 20, 29, 28, tzinfo=_dtmod.timezone.utc)

    class _FixedDT(_dtmod.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr(_dtmod, "datetime", _FixedDT)


def test_propose_same_second_does_not_overwrite(tmp_path, monkeypatch):
    from otaman_cli.commands import propose_team

    root = tmp_path / "prog"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (root / "platform.yaml").write_text("project: demo\n", encoding="utf-8")
    monkeypatch.setattr(propose_team, "find_project_root", lambda *a, **k: root)
    monkeypatch.setattr(propose_team, "resolve_agent_identity", lambda *a, **k: "cli-agent")
    monkeypatch.setattr(
        propose_team,
        "_resolve_bus_paths",
        lambda r: (
            root / ".agents" / "bus" / "active",
            root / ".agents" / "bus" / "active" / "acks",
        ),
    )
    _fixed_clock(monkeypatch)

    active = root / ".agents" / "bus" / "active"
    propose_team.cmd_propose(["First proposal"])
    propose_team.cmd_propose(["Second proposal"])

    scrs = sorted(active.glob("*spec-change-request*.md"))
    assert len(scrs) == 2  # both survived — no silent overwrite
    bodies = "\n".join(f.read_text() for f in scrs)
    assert "First proposal" in bodies and "Second proposal" in bodies
    # and the two blocked entries reference the two distinct stems
    blocked = (root / ".agents" / "blocked" / "cli-agent.md").read_text()
    assert scrs[0].stem in blocked and scrs[1].stem in blocked
