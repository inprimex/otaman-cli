"""scan-init-edition-backfill 1.1 — wiring into `init --update` and `scan` post-processing.

The core planning lives in test_edition_backfill.py; these cover the two CLI
touch points: `init._ensure_org_sections` (backfill an existing platform.yaml)
and `post_scan.run` (backfill the scan draft), both sourcing from the org's
primary registered platform.
"""

from __future__ import annotations

import pytest
import yaml

from otaman_cli.commands.init import _ensure_org_sections
from otaman_cli.onboard import post_scan

_RUNNER = {
    "harnesses": [{"id": "claude", "binary": "claude"}],
    "agent_bootstrap": {"mcp_config": ".mcp.json"},
}
_TERMINAL = {"local_auth": {"enabled": True, "session_ttl": 3600}, "users": []}
_ROSTER = [{"name": "roman", "roles": ["cto", "approver"]}]


def _platform(path, name, **sections):
    doc = {"project": name, "version": "1.0"}
    doc.update(sections)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


@pytest.fixture
def tenant(tmp_path, monkeypatch):
    """A registry with a fully-configured org primary + a hermetic edition file."""
    pdir = tmp_path / "registry"
    pdir.mkdir()
    monkeypatch.setenv("OTAMAN_PLATFORMS_DIR", str(pdir))
    # hermetic edition signal (don't read the real ~/.otaman/edition.yaml)
    import otaman_cli.edition as _ed

    monkeypatch.setattr(_ed, "DEFAULT_EDITION_PATH", tmp_path / "edition.yaml")
    (tmp_path / "edition.yaml").write_text("edition: ee\n", encoding="utf-8")

    primary = _platform(
        tmp_path / "alpha" / "platform.yaml",
        "alpha",
        **{"runner": _RUNNER, "terminal": _TERMINAL, "human-roster": _ROSTER},
    )
    (pdir / "alpha.yaml").symlink_to(primary)
    return tmp_path


# ---------------------------------------------------------------------------
# init._ensure_org_sections


def test_init_backfills_missing_sections(tenant):
    pf = _platform(tenant / "pmeets" / "platform.yaml", "pmeets")  # no org sections
    rc = _ensure_org_sections(pf, interactive=False)
    assert rc == 1
    doc = yaml.safe_load(pf.read_text("utf-8"))
    assert doc["runner"] == _RUNNER
    assert doc["terminal"] == _TERMINAL
    assert doc["human-roster"] == _ROSTER
    # idempotent: a second run is a no-op
    assert _ensure_org_sections(pf, interactive=False) == 0


def test_init_ambiguous_when_no_primary(tmp_path, monkeypatch, capsys):
    # empty registry → no template → ambiguous → WARN, leave absent, never crash
    pdir = tmp_path / "reg"
    pdir.mkdir()
    monkeypatch.setenv("OTAMAN_PLATFORMS_DIR", str(pdir))
    import otaman_cli.edition as _ed

    monkeypatch.setattr(_ed, "DEFAULT_EDITION_PATH", tmp_path / "edition.yaml")
    pf = _platform(tmp_path / "solo" / "platform.yaml", "solo")
    rc = _ensure_org_sections(pf, interactive=False)
    assert rc == 0
    assert "runner" not in yaml.safe_load(pf.read_text("utf-8"))  # left absent, not guessed
    out = capsys.readouterr().out
    assert "no org primary" in out  # loud, not silent


def test_init_does_not_touch_already_present(tenant):
    existing_runner = {"harnesses": [{"id": "mine", "binary": "mine"}]}
    pf = _platform(
        tenant / "has-runner" / "platform.yaml",
        "has-runner",
        **{"runner": existing_runner},
    )
    _ensure_org_sections(pf, interactive=False)
    doc = yaml.safe_load(pf.read_text("utf-8"))
    assert doc["runner"] == existing_runner  # NOT overwritten
    assert doc["terminal"] == _TERMINAL  # the genuinely-missing one is filled


# ---------------------------------------------------------------------------
# post_scan.run — the scan draft


def test_post_scan_backfills_draft(tenant):
    otaman_dir = tenant / "pmeets-otaman"
    otaman_dir.mkdir()
    draft = otaman_dir / "platform.yaml.draft"
    # a draft with the other 4 gaps satisfied, so we isolate the backfill
    draft.write_text(
        yaml.safe_dump(
            {
                "project": "pmeets",
                "repos": [
                    {"name": "pmeets-specs", "path": "./pmeets-specs", "owner": "spec-agent"}
                ],
                "launcher": {"local": {"enabled": True}},
                "specs": {"path": "./pmeets-specs/openspec", "format": "openspec"},
            }
        ),
        encoding="utf-8",
    )
    result = post_scan.run(
        draft, scan_root=tenant, otaman_dir=otaman_dir, program_slug="pmeets", interactive=False
    )
    assert set(result.org_sections_backfilled) == {"runner", "terminal", "human-roster"}
    doc = yaml.safe_load(draft.read_text("utf-8"))
    assert doc["runner"] == _RUNNER and doc["terminal"] == _TERMINAL


def test_post_scan_ambiguous_recorded_in_skipped(tmp_path, monkeypatch):
    pdir = tmp_path / "reg"
    pdir.mkdir()
    monkeypatch.setenv("OTAMAN_PLATFORMS_DIR", str(pdir))  # empty → ambiguous
    import otaman_cli.edition as _ed

    monkeypatch.setattr(_ed, "DEFAULT_EDITION_PATH", tmp_path / "edition.yaml")
    otaman_dir = tmp_path / "p-otaman"
    otaman_dir.mkdir()
    draft = otaman_dir / "platform.yaml.draft"
    draft.write_text(
        yaml.safe_dump(
            {
                "project": "p",
                "repos": [{"name": "p-specs", "path": "./p-specs", "owner": "spec-agent"}],
                "launcher": {"local": {"enabled": True}},
                "specs": {"path": "./p-specs/openspec", "format": "openspec"},
            }
        ),
        encoding="utf-8",
    )
    result = post_scan.run(
        draft, scan_root=tmp_path, otaman_dir=otaman_dir, program_slug="p", interactive=False
    )
    assert result.org_sections_backfilled == []
    assert any("runner:/terminal:/human-roster:" in s for s in result.skipped)  # loud
