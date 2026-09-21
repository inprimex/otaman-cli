"""Activation config lives at `program.skills` (skill-activation-config-split).

The key moved twice. It started at the TOP level, where the resolver never
looked, so a wizard answer activated nothing — silently. #170 moved it to
`program.processes.skills`, which fixed activation but put a CONFIG BLOCK in the
REGISTRY slot, and the console duly grew a phantom "skills" registry root that
had to be suppressed with a hardcoded list.

Ruled: they are two different things and get two keys. A registry is rows with
ids and a lifecycle; a pack profile is a switch with neither. The switch moves;
`program.processes.skills` is reserved for the registry, and the suppression
list is deleted — a registry root appears there only when a real registry with a
`path:` sits at it.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest
import yaml

from otaman_cli.onboard.program_init.platform_gen import _build_platform_yaml

ANSWERS = {
    "program_name": "demo",
    "primary_repo": ".",
    "skill_profile": "tech-startup-cofounder",
    "extra_skills": ["risk-reviewer"],
}
CONFIG = {"profile": "tech-startup-cofounder", "extra": ["risk-reviewer"]}


# ---------------------------------------------------------------------------
# 1.2 — the wizard writes the key the resolver reads


def test_the_wizard_writes_program_skills():
    doc = _build_platform_yaml(ANSWERS)
    assert doc["program"]["skills"] == CONFIG


def test_the_wizard_leaves_the_registry_slot_alone():
    """The whole point of the split: config must not sit in the registry's key."""
    doc = _build_platform_yaml(ANSWERS)
    assert "skills" not in (doc["program"].get("processes") or {})


def test_the_wizard_no_longer_writes_the_top_level_key():
    assert "skills" not in _build_platform_yaml(ANSWERS)


def test_no_profile_and_no_extras_writes_nothing():
    doc = _build_platform_yaml({**ANSWERS, "skill_profile": "", "extra_skills": []})
    assert "skills" not in doc.get("program", {})
    assert "skills" not in doc


# ---------------------------------------------------------------------------
# 1.3 — the suppression list is gone; shape decides


def _roots(processes: dict, files: dict | None = None, monkeypatch=None):
    from otaman_cli.console.bus import Program
    from otaman_cli.console.extra_registries import registry_roots
    from otaman_cli.yaml_fast import clear_cache

    base = pathlib.Path(tempfile.mkdtemp())
    root = base / "meta"
    root.mkdir()
    strat = base / "strategy"
    strat.mkdir()
    for name, content in (files or {}).items():
        strat.joinpath(name).write_text(yaml.dump(content), encoding="utf-8")
    root.joinpath("platform.yaml").write_text(
        yaml.dump({"project": "d", "version": "1.0", "program": {"processes": processes}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OTAMAN_STRATEGY_DIR", str(strat))
    clear_cache()
    return [r.id for r in registry_roots(Program(name="d", root=root))]


def test_the_suppression_list_is_deleted():
    """It was a holding action while the naming was open. The ruling closed it,
    so the code must not keep a hardcoded exception list."""
    import otaman_cli.console.extra_registries as mod

    assert not hasattr(mod, "NON_REGISTRY_PROCESSES")


def test_leftover_config_at_the_registry_slot_renders_no_root(monkeypatch):
    """The phantom root #170 created. Shape tells them apart: config has no
    `path:`."""
    assert _roots({"skills": CONFIG}, monkeypatch=monkeypatch) == []


def test_a_real_skills_registry_does_render(monkeypatch):
    roots = _roots(
        {"skills": {"path": "skills.yaml"}},
        {"skills.yaml": {"skills": [{"id": "S1", "name": "python"}]}},
        monkeypatch=monkeypatch,
    )
    assert roots == ["skills"]


def test_other_registries_are_unaffected_by_the_path_rule(monkeypatch):
    """Only `skills` ever held config in the registry slot, so only it needs the
    extra proof — the four dispatched registries still render on `enabled`."""
    assert _roots({"vocabulary": {"enabled": True}}, monkeypatch=monkeypatch) == ["vocabulary"]


def test_a_wizard_program_grows_no_phantom_root(monkeypatch, tmp_path):
    from otaman_cli.console.bus import Program
    from otaman_cli.console.extra_registries import registry_roots
    from otaman_cli.yaml_fast import clear_cache

    root = tmp_path / "wizard"
    root.mkdir()
    root.joinpath("platform.yaml").write_text(
        yaml.dump(_build_platform_yaml(ANSWERS)), encoding="utf-8"
    )
    clear_cache()
    assert registry_roots(Program(name="d", root=root)) == []


# ---------------------------------------------------------------------------
# 1.2 — doctor flags the retired locations, accurately


def _doctor(extra: dict) -> dict:
    import otaman_cli.commands.doctor as D
    from otaman_cli.yaml_fast import clear_cache

    root = pathlib.Path(tempfile.mkdtemp()) / "meta"
    root.mkdir(parents=True)
    doc = {"project": "d", "version": "1.0", "repos": []}
    doc.update(extra)
    root.joinpath("platform.yaml").write_text(yaml.dump(doc), encoding="utf-8")
    clear_cache()
    return D._check_retired_skills_key(root)


def test_doctor_is_silent_when_the_key_is_in_the_right_place():
    assert _doctor({"program": {"skills": CONFIG}})["status"] == "ok"


def test_doctor_flags_the_top_level_key():
    result = _doctor({"skills": CONFIG})
    assert result["status"] == "warn"
    assert "top-level" in result["retired"][0][0]


def test_doctor_flags_config_left_in_the_registry_slot():
    result = _doctor({"program": {"processes": {"skills": CONFIG}}})
    assert result["status"] == "warn"
    assert "processes.skills" in result["retired"][0][0]


def test_doctor_distinguishes_dead_from_still_working():
    """Accuracy that matters: the resolver STILL reads the registry slot during
    plugin's compatibility window (measured: 8 skills activate from it), while
    the top-level key was never read by anything. Calling a working config
    'inert' would send its author chasing a problem they do not have."""
    top = _doctor({"skills": CONFIG})["retired"][0]
    slot = _doctor({"program": {"processes": {"skills": CONFIG}}})["retired"][0]
    assert top[2] is True  # truly dead
    assert slot[2] is False  # on borrowed time


def test_doctor_is_silent_for_a_real_registry_at_the_slot():
    assert (
        _doctor({"program": {"processes": {"skills": {"path": "skills.yaml"}}}})["status"] == "ok"
    )


def test_doctor_says_harmless_when_both_are_present():
    result = _doctor({"program": {"skills": CONFIG, "processes": {"skills": CONFIG}}})
    assert result["status"] == "ok"
    assert result["effective"] is True


def test_doctor_never_fails_the_run():
    """WARN only — a file that predates the move is a misconfiguration to point
    at, not a broken program."""
    import inspect

    import otaman_cli.commands.doctor as D

    src = inspect.getsource(D._check_retired_skills_key)
    assert '"status": "warn"' in src
    assert "never a failure" in src


# ---------------------------------------------------------------------------
# Home counts the effective key and names the current remedy


def _skills_line(extra: dict) -> str:
    from otaman_cli.console.app import HomeScreen
    from otaman_cli.console.bus import Program
    from otaman_cli.console.home import build_home_summary
    from otaman_cli.yaml_fast import clear_cache

    root = pathlib.Path(tempfile.mkdtemp()) / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    doc = {"project": "d", "version": "1.0", "repos": []}
    doc.update(extra)
    root.joinpath("platform.yaml").write_text(yaml.dump(doc), encoding="utf-8")
    clear_cache()
    body = HomeScreen._body_text(build_home_summary(Program(name="d", root=root)))
    return next(line for line in body.splitlines() if "skills:" in line)


def test_home_counts_the_effective_key():
    assert "skills: 2" in _skills_line({"program": {"skills": CONFIG}})


@pytest.mark.parametrize(
    "extra",
    [{"skills": CONFIG}, {"program": {"processes": {"skills": CONFIG}}}],
)
def test_home_flags_either_retired_location(extra):
    line = _skills_line(extra)
    assert "skills: 0" in line
    assert "retired location" in line


def test_home_names_the_current_destination():
    """A remedy pointing at a retired location is worse than none — this line
    said `program.processes.skills`, which was right for exactly one release."""
    line = _skills_line({"skills": CONFIG})
    assert "program.skills" in line
    assert "program.processes.skills" not in line


# ---------------------------------------------------------------------------
# ported from tests/test_skills_key_location.py, which this change superseded


def test_extras_alone_still_write():
    doc = _build_platform_yaml({**ANSWERS, "skill_profile": "", "extra_skills": ["risk-reviewer"]})
    assert doc["program"]["skills"] == {"profile": "", "extra": ["risk-reviewer"]}


@pytest.mark.parametrize(
    "junk",
    [{"program": {"skills": "not-a-mapping"}}, {"program": "not-a-mapping"}, {"skills": "nope"}],
)
def test_malformed_config_degrades_to_zero(junk):
    assert "skills: 0" in _skills_line(junk)
