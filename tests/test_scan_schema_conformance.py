"""scan-schema-conformance 1.1 — scan output can never diverge from the live schema.

The bug: scan (plugin discover_repos) stamped ``is_spec_repo: true`` onto spec
repos' ``repos[]`` entries, but the live platform schema declares
``repos.items`` as ``additionalProperties: false`` — so a scan-generated draft
FAILED ``otaman init``'s own validation. These lock that shut: retired fields are
stripped from scan output and tolerantly on init/validate, and a conformance
check asserts every scan-shaped draft's keys are ones the LIVE schema accepts
(incl. the spec-repo / pmeets shape).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from otaman_cli.onboard.schema_fields import strip_retired_fields


def _live_schema() -> dict:
    """Load the live platform schema shipped in otaman-core (production source)."""
    import otaman_core

    p = Path(otaman_core.__file__).parent / "schemas" / "platform-schema.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _conformance_errors(draft: dict, schema: dict) -> list[str]:
    """Keys in *draft* that the live schema's closed field sets would reject —
    the exact failure mode (unknown key under additionalProperties:false)."""
    errs: list[str] = []
    props = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        errs += [f"top-level.{k}" for k in draft if k not in props]
    items = props.get("repos", {}).get("items", {})
    repo_props = set(items.get("properties", {}))
    if items.get("additionalProperties") is False:
        for r in draft.get("repos") or []:
            if isinstance(r, dict):
                errs += [f"repos[].{k}" for k in r if k not in repo_props]
    return errs


def _pmeets_draft() -> dict:
    """A scan-shaped draft for a spec-repo project (the pmeets shape) — every key
    is one the live schema accepts."""
    return {
        "project": "pmeets",
        "version": "1.0",
        "repos": [
            {"name": "pmeets-specs", "path": "../pmeets-specs", "owner": "spec-agent"},
            {"name": "pmeets-api", "path": "../pmeets-api", "owner": "backend-agent"},
        ],
        "specs": {"path": "../pmeets-specs/openspec", "format": "openspec"},
    }


# ---------------------------------------------------------------------------
# the live schema really does close the repo-entry field set


def test_live_schema_repos_entry_is_closed():
    schema = _live_schema()
    items = schema["properties"]["repos"]["items"]
    assert items["additionalProperties"] is False  # the marker the fix depends on
    assert {"name", "path", "owner"}.issubset(items["properties"])
    assert "is_spec_repo" not in items["properties"]  # never was an allowed key


# ---------------------------------------------------------------------------
# strip_retired_fields


def test_strip_removes_is_spec_repo_from_repo_entries():
    doc = _pmeets_draft()
    doc["repos"][0]["is_spec_repo"] = True
    removed = strip_retired_fields(doc)
    assert removed == ["is_spec_repo"]
    assert "is_spec_repo" not in doc["repos"][0]


def test_strip_counts_multiple_repo_hits():
    doc = _pmeets_draft()
    doc["repos"][0]["is_spec_repo"] = True
    doc["repos"][1]["is_spec_repo"] = True
    removed = strip_retired_fields(doc)
    assert removed == ["is_spec_repo (x2 repo entries)"]


def test_strip_is_idempotent_and_clean_is_noop():
    assert strip_retired_fields(_pmeets_draft()) == []


# ---------------------------------------------------------------------------
# conformance check catches the exact bug and the strip fixes it


def test_clean_pmeets_draft_conforms():
    assert _conformance_errors(_pmeets_draft(), _live_schema()) == []


def test_is_spec_repo_draft_is_caught_then_stripped_conforms():
    schema = _live_schema()
    draft = _pmeets_draft()
    draft["repos"][0]["is_spec_repo"] = True
    errs = _conformance_errors(draft, schema)
    assert errs == ["repos[].is_spec_repo"]  # the check catches the drift
    strip_retired_fields(draft)
    assert _conformance_errors(draft, schema) == []  # ...and the strip fixes it


# ---------------------------------------------------------------------------
# the same via the PRODUCTION validator, when jsonschema is available


def test_live_validator_rejects_then_accepts():
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        pytest.skip("jsonschema not installed in this environment")
    from otaman_core.validate_platform import validate_with_jsonschema

    schema = _live_schema()
    draft = _pmeets_draft()
    draft["repos"][0]["is_spec_repo"] = True
    assert validate_with_jsonschema(draft, schema)  # the live validator errors
    strip_retired_fields(draft)
    assert validate_with_jsonschema(draft, schema) == []  # clean after the strip


# ---------------------------------------------------------------------------
# the two removal sites: scan output (post_scan) + init/validate migration


def test_post_scan_strips_is_spec_repo_from_draft(tmp_path):
    from otaman_cli.onboard import post_scan

    otaman_dir = tmp_path / "pmeets-otaman"
    otaman_dir.mkdir()
    draft = otaman_dir / "platform.yaml.draft"
    doc = _pmeets_draft()
    doc["repos"][0]["is_spec_repo"] = True  # as older discover_repos stamps it
    doc["launcher"] = {"local": {"enabled": True}}
    draft.write_text(yaml.safe_dump(doc), encoding="utf-8")

    result = post_scan.run(
        draft, scan_root=tmp_path, otaman_dir=otaman_dir, program_slug="pmeets", interactive=False
    )
    assert "is_spec_repo" in result.retired_fields_stripped
    after = yaml.safe_load(draft.read_text("utf-8"))
    assert all("is_spec_repo" not in r for r in after["repos"])  # scan output is clean


def test_normalizer_strips_is_spec_repo_with_hint(tmp_path):
    from otaman_cli.main import _normalize_ce_platform_yaml_for_validation

    pf = tmp_path / "platform.yaml"
    doc = _pmeets_draft()
    doc["repos"][0]["is_spec_repo"] = True
    pf.write_text(yaml.safe_dump(doc), encoding="utf-8")

    norm_path, hints = _normalize_ce_platform_yaml_for_validation(pf)
    try:
        assert norm_path != pf  # a change was made → validation copy written
        assert any("is_spec_repo" in h for h in hints)  # the human is told
        normalized = yaml.safe_load(norm_path.read_text("utf-8"))
        assert all("is_spec_repo" not in r for r in normalized["repos"])
        # the on-disk source is NEVER modified by the normalizer
        assert yaml.safe_load(pf.read_text("utf-8"))["repos"][0]["is_spec_repo"] is True
    finally:
        if norm_path != pf:
            norm_path.unlink()
