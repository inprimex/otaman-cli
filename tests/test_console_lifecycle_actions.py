"""console-lifecycle-actions 1.2 — one-key RATIFY + ARCHIVE from the lifecycle table.

Committed writes (D2, the spec-approved pattern): ratify mints the ratified
approval + commits; archive runs only when the gate is ALLOWED, moves the change
under archive/<date>-<name> + commits. Offered per D1 (the row's state is the
menu). Textual pilots skip cleanly without the console extra.
"""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess

import pytest
import yaml

from otaman_cli.console import bus
from otaman_cli.console import lifecycle as L

_HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
_textual = pytest.mark.skipif(not _HAS_TEXTUAL, reason="needs the 'console' extra (Textual)")


@pytest.fixture
def program(tmp_path, monkeypatch):
    root = tmp_path / "prog"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    specs = root / "specs"
    (specs / "openspec" / "changes").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nspecs:\n  path: specs\n", encoding="utf-8"
    )
    # a real git repo so committed writes work (no remote → push pending)
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@x.io"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", "-C", str(specs), *args], check=True, capture_output=True)
    monkeypatch.delenv("OTAMAN_HUMAN", raising=False)
    return bus.Program(name="demo", root=root)


def _change(program, name, *, stage="dispatched", ticks=(True,), approved_by=None):
    d = program.root / "specs" / "openspec" / "changes" / name
    d.mkdir(parents=True)
    oy = {"stage": stage}
    if approved_by:
        oy["approved_by"] = approved_by
    (d / ".openspec.yaml").write_text(yaml.safe_dump(oy), encoding="utf-8")
    (d / "tasks.md").write_text(
        "\n".join(f"- [{'x' if t else ' '}] t{i}" for i, t in enumerate(ticks)) + "\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(program.root / "specs"), "add", "-A"], capture_output=True)
    subprocess.run(
        ["git", "-C", str(program.root / "specs"), "commit", "-q", "-m", f"seed {name}"],
        capture_output=True,
    )
    return d


def _git_log(program) -> str:
    return subprocess.run(
        ["git", "-C", str(program.root / "specs"), "log", "-1", "--format=%s"],
        capture_output=True,
        text=True,
    ).stdout.strip()


# ---------------------------------------------------------------------------
# ratify_change


def test_ratify_writes_marker_and_commits(program):
    d = _change(program, "stuck", ticks=(True, True))
    ok, msg = L.ratify_change(program, "stuck", by="roman", reason="founder-mode unblock")
    assert ok and "committed" in msg
    data = yaml.safe_load((d / ".openspec.yaml").read_text())
    assert data["stage"] == "approved" and data["ratified"] is True
    assert "roman" in data["approved_by"] and data["ratified_at"]
    assert "ratify stuck" in _git_log(program)


def test_ratify_requires_identity_and_reason(program):
    _change(program, "c")
    assert L.ratify_change(program, "c", by="", reason="x")[0] is False
    assert L.ratify_change(program, "c", by="roman", reason="  ")[0] is False


def test_ratify_unknown_change(program):
    assert L.ratify_change(program, "ghost", by="roman", reason="x")[0] is False


# ---------------------------------------------------------------------------
# archive_change


def test_archive_moves_and_commits_when_gate_allows(program):
    # approved change → archive gate allows
    _change(program, "done", ticks=(True, True), approved_by="ratified: roman — shipped")
    ok, msg = L.archive_change(program, "done")
    assert ok and "committed" in msg
    changes = program.root / "specs" / "openspec" / "changes"
    assert not (changes / "done").exists()
    archived = list((changes / "archive").iterdir())
    assert archived and archived[0].name.endswith("-done")
    assert yaml.safe_load((archived[0] / ".openspec.yaml").read_text())["stage"] == "archived"
    assert "archive done" in _git_log(program)


def test_archive_refused_when_gate_blocks(program):
    # unapproved delta change → archive gate blocks; nothing moves
    _change(program, "unappr", ticks=(True, True))
    ok, msg = L.archive_change(program, "unappr")
    assert ok is False and "gate" in msg
    changes = program.root / "specs" / "openspec" / "changes"
    assert (changes / "unappr").exists()  # untouched


def test_ratify_then_archive_full_loop(program):
    _change(program, "loop", ticks=(True, True))
    assert L.ratify_change(program, "loop", by="roman", reason="ship")[0] is True
    ok, _ = L.archive_change(program, "loop")
    assert ok is True
    changes = program.root / "specs" / "openspec" / "changes"
    assert not (changes / "loop").exists() and (changes / "archive").exists()


# ---------------------------------------------------------------------------
# screen bindings (D1 authorization)


@_textual
def test_row_ratify_action_on_ratify_blocked_row(program, monkeypatch):
    monkeypatch.setenv("OTAMAN_HUMAN", "roman")
    _change(program, "blocked-x", ticks=(True, True))  # done, unapproved → ratify-blocked
    from otaman_cli.console.app import LifecycleScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(LifecycleScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await app.screen.run_action("ratify")
            await pilot.pause()
            await pilot.press("enter")  # empty reason → refused, no crash
            await pilot.pause()
            # provide a reason this time
            await app.screen.run_action("ratify")
            await pilot.pause()
            for ch in "ship it":
                await pilot.press(ch if ch != " " else "space")
            await pilot.press("enter")
            await pilot.pause()
            await app.action_quit()

    asyncio.run(go())
    data = yaml.safe_load(
        (
            program.root / "specs" / "openspec" / "changes" / "blocked-x" / ".openspec.yaml"
        ).read_text()
    )
    assert data.get("ratified") is True  # ratified via the button


@_textual
def test_row_archive_action_on_approved_complete_row(program):
    _change(program, "arch-me", ticks=(True, True), approved_by="ratified: roman — ok")
    from otaman_cli.console.app import LifecycleScreen, OtamanConsole

    async def go():
        app = OtamanConsole([program], search_root=program.root)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(LifecycleScreen(program))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await app.screen.run_action("archive")
            await pilot.pause()
            await app.action_quit()

    asyncio.run(go())
    changes = program.root / "specs" / "openspec" / "changes"
    assert not (changes / "arch-me").exists() and (changes / "archive").exists()
