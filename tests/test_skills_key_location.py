"""`skills` belongs under `program.processes` (cofounder-agent 20260919T232423).

The wizard wrote a TOP-LEVEL `skills:` key; the resolver
(`otaman_plugin.skill_packs.resolve_active_skills`) reads
`program.processes.skills`. Nothing reconciled them, so answering the
skill-profile question in `otaman init` activated NOTHING — silently. Measured
against the live tech-startup pack: the legacy shape resolves 0 skills, the
nested shape resolves 8.

Worse, Home counted the top-level key, so the console displayed a cheerful
non-zero skills count sourced from a key with no effect — the reported part
"most likely to burn an hour of someone's debugging".

Which key is canonical was not really open: `platform_gen`'s own comment says
processes nest under `program:` and that platform-schema.yaml rejects unknown
top-level keys, and the resolver already reads the nested one.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest
import yaml

from otaman_cli.console.app import HomeScreen
from otaman_cli.console.bus import Program
from otaman_cli.console.home import build_home_summary
from otaman_cli.onboard.program_init.platform_gen import _build_platform_yaml
from otaman_cli.yaml_fast import clear_cache

ANSWERS = {
    "program_name": "demo",
    "primary_repo": ".",
    "skill_profile": "tech-startup-cofounder",
    "extra_skills": ["risk-reviewer"],
}


def _nested(doc) -> dict | None:
    program = doc.get("program")
    processes = program.get("processes") if isinstance(program, dict) else None
    return processes.get("skills") if isinstance(processes, dict) else None


# ---------------------------------------------------------------------------
# the write side


def test_the_wizard_writes_the_key_the_resolver_reads():
    doc = _build_platform_yaml(ANSWERS)
    assert _nested(doc) == {"profile": "tech-startup-cofounder", "extra": ["risk-reviewer"]}


def test_the_wizard_no_longer_writes_a_top_level_skills_key():
    """platform-schema.yaml rejects unknown top-level keys, and the resolver
    never looks there."""
    assert "skills" not in _build_platform_yaml(ANSWERS)


def test_skills_nests_beside_the_other_processes():
    """It is a PROCESS — it belongs with them, not in its own top-level island."""
    doc = _build_platform_yaml({**ANSWERS, "processes": ["outcomes", "solutions"]})
    processes = doc["program"]["processes"]
    assert "skills" in processes
    assert "outcomes" in processes  # the sibling block survived


def test_no_profile_and_no_extras_writes_nothing():
    """An empty answer wrote `skills: {profile: None, extra: []}`, which is
    noise; the neighbouring blocks already omit when empty."""
    doc = _build_platform_yaml({**ANSWERS, "skill_profile": "", "extra_skills": []})
    assert _nested(doc) is None
    assert "skills" not in doc


def test_extras_alone_still_write():
    doc = _build_platform_yaml({**ANSWERS, "skill_profile": "", "extra_skills": ["risk-reviewer"]})
    assert _nested(doc) == {"profile": "", "extra": ["risk-reviewer"]}


# ---------------------------------------------------------------------------
# Home tells the truth


def _skills_line(cfg_extra: dict) -> str:
    root = pathlib.Path(tempfile.mkdtemp()) / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    doc = {"project": "d", "version": "1.0", "repos": []}
    doc.update(cfg_extra)
    (root / "platform.yaml").write_text(yaml.dump(doc), encoding="utf-8")
    clear_cache()
    summary = build_home_summary(Program(name="d", root=root))
    body = HomeScreen._body_text(summary)
    return next(line for line in body.splitlines() if "skills:" in line)


NESTED = {"program": {"processes": {"skills": {"profile": "p", "extra": ["a"]}}}}
LEGACY = {"skills": {"profile": "p", "extra": ["a"]}}


def test_home_counts_the_effective_location():
    assert "skills: 2" in _skills_line(NESTED)


def test_home_does_not_count_an_inert_legacy_key_as_active():
    """THE BUG: a non-zero count for skills that never load."""
    line = _skills_line(LEGACY)
    assert "skills: 0" in line


def test_home_names_the_inert_key_instead_of_silently_showing_zero():
    """A bare 0 beside a populated-looking platform.yaml is what sends someone
    hunting; the line says what is wrong and where to move it."""
    line = _skills_line(LEGACY)
    assert "inert" in line
    assert "program.processes.skills" in line


def test_home_says_nothing_extra_when_there_is_no_legacy_key():
    assert "inert" not in _skills_line(NESTED)
    assert "inert" not in _skills_line({})


def test_nested_wins_when_both_are_present():
    """A migrated file may carry both; the effective one decides and no
    warning fires."""
    line = _skills_line({**NESTED, **LEGACY})
    assert "skills: 2" in line
    assert "inert" not in line


@pytest.mark.parametrize("junk", [{"skills": "not-a-mapping"}, {"program": "not-a-mapping"}])
def test_malformed_config_degrades_to_zero(junk):
    assert "skills: 0" in _skills_line(junk)
