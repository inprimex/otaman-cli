"""cli-not-in-project-guidance 1.1 — one actionable "not in a project" message.

Every command that can't find a platform.yaml now routes through the single
helper, which states what was searched AND recommends the next step by folder
shape (scan when repos are nearby, init otherwise). The grep-guard asserts no
bare literal survives anywhere but the helper — so a future command can't
reintroduce a dead-end error.
"""

from __future__ import annotations

from pathlib import Path

from otaman_cli.identity import not_in_project_message

_LITERAL = "Not in an otaman project"


def test_recommends_scan_when_cwd_is_a_git_repo(tmp_path):
    proj = tmp_path / "work"
    (proj / ".git").mkdir(parents=True)
    msg = not_in_project_message(proj)
    assert "otaman scan" in msg
    assert "otaman init" not in msg


def test_recommends_scan_when_sibling_is_a_git_repo(tmp_path):
    (tmp_path / "repo-a" / ".git").mkdir(parents=True)
    here = tmp_path / "here"
    here.mkdir()
    msg = not_in_project_message(here)
    assert "otaman scan" in msg


def test_recommends_init_when_no_git_nearby(tmp_path):
    here = tmp_path / "solo"
    here.mkdir()
    msg = not_in_project_message(here)
    assert "otaman init" in msg
    assert "otaman scan" not in msg


def test_states_what_was_searched_and_next_step(tmp_path):
    here = tmp_path / "x"
    here.mkdir()
    msg = not_in_project_message(here)
    assert "no platform.yaml" in msg
    assert str(here) in msg  # names where it looked
    assert "re-run this command" in msg.lower()


def test_defaults_to_cwd(monkeypatch, tmp_path):
    here = tmp_path / "cwd-default"
    here.mkdir()
    monkeypatch.chdir(here)
    assert str(here) in not_in_project_message()


# ---------------------------------------------------------------------------
# grep-guard: the bare literal survives ONLY in the helper module


def test_no_bare_literal_survives_outside_helper():
    import otaman_cli

    src = Path(otaman_cli.__file__).parent
    offenders = []
    for py in src.rglob("*.py"):
        if py.name == "identity.py":
            continue  # the canonical helper defines the one message
        try:
            if _LITERAL in py.read_text(encoding="utf-8"):
                offenders.append(str(py.relative_to(src)))
        except OSError:
            continue
    assert offenders == [], (
        f"bare {_LITERAL!r} literal must route through not_in_project_message(): {offenders}"
    )
