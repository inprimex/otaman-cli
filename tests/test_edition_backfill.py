"""scan-init-edition-backfill 1.1 — edition-aware org-section backfill logic.

A new program's platform.yaml must carry runner:/terminal:/human-roster:
consistent with the org (the runner applies one program's bootstrap
tenant-wide). These cover the pure planning + the ruamel apply: the primary
resolves as the alphabetically-first registered platform, backfill is
idempotent and byte-consistent, and a missing template is AMBIGUOUS (confirm,
never silently omit) rather than a silent gap.
"""

from __future__ import annotations

import pytest
import yaml

from otaman_cli.onboard.edition_backfill import (
    ORG_IMPLIED_SECTIONS,
    apply_backfill,
    find_primary_platform,
    plan_backfill,
    plan_for_platform,
)

_RUNNER = {
    "harnesses": [{"id": "claude", "binary": "claude"}],
    "agent_bootstrap": {"mcp_config": ".mcp.json", "plugin_dir": "~/.otaman/plugins"},
}
_TERMINAL = {"local_auth": {"enabled": True, "session_ttl": 3600}, "users": []}
_ROSTER = [{"name": "roman", "roles": ["cto", "approver"]}]


def _write_platform(path, name, *, runner=None, terminal=None, roster=None, **extra):
    doc = {"project": name, "version": "1.0", **extra}
    if runner is not None:
        doc["runner"] = runner
    if terminal is not None:
        doc["terminal"] = terminal
    if roster is not None:
        doc["human-roster"] = roster
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A platforms registry (OTAMAN_PLATFORMS_DIR) of symlinks → program platforms."""
    pdir = tmp_path / "registry"
    pdir.mkdir()
    monkeypatch.setenv("OTAMAN_PLATFORMS_DIR", str(pdir))
    monkeypatch.delenv("OTAMAN_HUMAN", raising=False)

    def _register(name, platform_path):
        (pdir / f"{name}.yaml").symlink_to(platform_path)

    return tmp_path, _register


# ---------------------------------------------------------------------------
# find_primary_platform


def test_primary_is_alphabetically_first_registered(registry):
    root, register = registry
    # register out of order; the runner's primary is the alphabetical-first name
    pz = _write_platform(root / "zeta" / "platform.yaml", "zeta", runner=_RUNNER)
    pa = _write_platform(root / "alpha" / "platform.yaml", "alpha", runner={"harnesses": []})
    register("zeta", pz)
    register("alpha", pa)
    primary = find_primary_platform()
    assert primary is not None
    assert primary.name == "alpha"  # first by name, matching the runner
    assert primary.path == pa.resolve()


def test_primary_excludes_the_target_itself(registry):
    root, register = registry
    pa = _write_platform(root / "alpha" / "platform.yaml", "alpha", runner=_RUNNER)
    pb = _write_platform(root / "beta" / "platform.yaml", "beta", runner=_RUNNER)
    register("alpha", pa)
    register("beta", pb)
    # excluding alpha (the would-be primary) falls through to beta
    primary = find_primary_platform(exclude=pa)
    assert primary is not None and primary.name == "beta"


def test_primary_skips_dangling_links(registry):
    root, register = registry
    missing = root / "gone" / "platform.yaml"  # never created → dangling
    pb = _write_platform(root / "beta" / "platform.yaml", "beta", runner=_RUNNER)
    (root / "gone").mkdir(parents=True, exist_ok=True)
    register("aaa-gone", missing)  # alphabetically first but dangling
    register("beta", pb)
    primary = find_primary_platform()
    assert primary is not None and primary.name == "beta"


def test_no_registry_yields_no_template(registry):
    _root, _register = registry  # registry dir exists but empty
    assert find_primary_platform() is None


# ---------------------------------------------------------------------------
# plan_backfill


def test_all_sections_present_is_no_work():
    from otaman_cli.onboard.edition_backfill import TemplateSource

    doc = {"runner": _RUNNER, "terminal": _TERMINAL, "human-roster": _ROSTER}
    plan = plan_backfill(doc, edition="ee", template=TemplateSource("p", None, {}))
    assert plan.has_work is False and plan.additions == {}


def test_missing_sections_backfilled_from_template():
    from otaman_cli.onboard.edition_backfill import TemplateSource

    tmpl = TemplateSource(
        "alpha", None, {"runner": _RUNNER, "terminal": _TERMINAL, "human-roster": _ROSTER}
    )
    plan = plan_backfill({"project": "new"}, edition="ee", template=tmpl)
    assert set(plan.additions) == set(ORG_IMPLIED_SECTIONS)
    assert plan.additions["runner"] == _RUNNER
    assert plan.ambiguous is False and plan.has_work is True
    # deep copy — mutating the plan must not touch the template
    plan.additions["runner"]["harnesses"].append({"id": "x", "binary": "x"})
    assert len(tmpl.sections["runner"]["harnesses"]) == 1


def test_partial_missing_only_fills_the_gap():
    from otaman_cli.onboard.edition_backfill import TemplateSource

    tmpl = TemplateSource("alpha", None, {"runner": _RUNNER, "terminal": _TERMINAL})
    # target already has runner: → only terminal: is backfilled (idempotent)
    plan = plan_backfill({"runner": {"harnesses": ["x"]}}, edition="ee", template=tmpl)
    assert set(plan.additions) == {"terminal"}


def test_missing_with_no_template_is_ambiguous():
    plan = plan_backfill({"project": "new"}, edition="unknown", template=None)
    assert plan.ambiguous is True and plan.additions == {}
    assert plan.has_work is True  # loud, not a silent omit
    assert "no registered org platform" in plan.reason


def test_template_lacking_sections_is_not_ambiguous():
    from otaman_cli.onboard.edition_backfill import TemplateSource

    # a CE org primary with no runner:/terminal: → those aren't org convention
    tmpl = TemplateSource("alpha", None, {"human-roster": _ROSTER})
    plan = plan_backfill({"project": "new"}, edition="ce", template=tmpl)
    assert plan.additions == {"human-roster": _ROSTER}
    assert plan.ambiguous is False  # runner/terminal simply not part of this org


# ---------------------------------------------------------------------------
# apply_backfill (ruamel round-trip)


def test_apply_injects_and_is_idempotent(tmp_path):
    pf = _write_platform(tmp_path / "platform.yaml", "new")
    written = apply_backfill(pf, {"runner": _RUNNER, "terminal": _TERMINAL})
    assert set(written) == {"runner", "terminal"}
    doc = yaml.safe_load(pf.read_text("utf-8"))
    assert doc["runner"] == _RUNNER and doc["terminal"] == _TERMINAL
    assert doc["project"] == "new"  # existing content preserved
    # second apply is a no-op (already present)
    assert apply_backfill(pf, {"runner": {"harnesses": []}}) == []
    assert yaml.safe_load(pf.read_text("utf-8"))["runner"] == _RUNNER  # not overwritten


# ---------------------------------------------------------------------------
# plan_for_platform (end to end against a live registry)


def test_plan_for_platform_end_to_end(registry, tmp_path, monkeypatch):
    root, register = registry
    primary = _write_platform(
        root / "alpha" / "platform.yaml",
        "alpha",
        runner=_RUNNER,
        terminal=_TERMINAL,
        roster=_ROSTER,
    )
    register("alpha", primary)
    # a fresh program with none of the org-implied sections
    newp = _write_platform(root / "pmeets" / "platform.yaml", "pmeets")
    # point edition at a missing file → unknown (does not affect a template hit)
    plan = plan_for_platform(newp, edition_path=tmp_path / "no-edition.yaml")
    assert plan.template is not None and plan.template.name == "alpha"
    assert set(plan.additions) == set(ORG_IMPLIED_SECTIONS)
