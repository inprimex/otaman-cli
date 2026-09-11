"""spec-gate-hardening 1.1 — per-action gate enforcement.

`spec_policy.enforcement` is a scalar (all actions) or a map keyed
author|merge|dispatch|archive; the gate resolves the acting action's mode, and
doctor flags an unknown key or invalid mode.
"""

from __future__ import annotations

from pathlib import Path

from otaman_cli.commands.doctor import _check_enforcement_map
from otaman_cli.commands.spec import _load_policy, resolve_action_enforcement


def _platform(root: Path, spec_policy_block: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "platform.yaml").write_text(
        "project: t\nversion: '1.0'\nrepos: []\n" + spec_policy_block,
        encoding="utf-8",
    )
    return root


def test_scalar_applies_to_all_actions(tmp_path):
    root = _platform(tmp_path, "spec_policy:\n  enforcement: block\n")
    for a in ("author", "merge", "dispatch", "archive"):
        assert resolve_action_enforcement(root, a) == "block"


def test_map_resolves_per_action(tmp_path):
    root = _platform(
        tmp_path,
        "spec_policy:\n  enforcement:\n    author: warn\n    dispatch: block\n",
    )
    assert resolve_action_enforcement(root, "dispatch") == "block"
    assert resolve_action_enforcement(root, "author") == "warn"
    # a key absent from the map falls back to the default (warn)
    assert resolve_action_enforcement(root, "archive") == "warn"


def test_invalid_mode_falls_back(tmp_path):
    root = _platform(tmp_path, "spec_policy:\n  enforcement:\n    dispatch: bogus\n")
    assert resolve_action_enforcement(root, "dispatch") == "warn"


def test_absent_enforcement_is_default(tmp_path):
    root = _platform(tmp_path, "spec_policy:\n  process:\n    level: solutions\n")
    assert resolve_action_enforcement(root, "dispatch") == "warn"


def test_load_policy_applies_action_mode(tmp_path):
    root = _platform(
        tmp_path,
        "spec_policy:\n  enforcement:\n    author: warn\n    dispatch: block\n",
    )
    assert _load_policy(root, "dispatch").enforcement == "block"
    assert _load_policy(root, "author").enforcement == "warn"
    # no action → scalar/fallback (a map resolves to core's default for display)
    assert _load_policy(root).enforcement in ("warn", "block", "self-waive")


# ---------------------------------------------------------------------------
# doctor validates the map


def test_doctor_flags_unknown_key(tmp_path):
    root = _platform(tmp_path, "spec_policy:\n  enforcement:\n    dispatchh: block\n")
    r = _check_enforcement_map(root)
    assert r["applicable"] is True and r["ok"] is False
    assert "unknown action" in r["detail"] and "dispatchh" in r["detail"]


def test_doctor_flags_invalid_mode(tmp_path):
    root = _platform(tmp_path, "spec_policy:\n  enforcement:\n    dispatch: nope\n")
    r = _check_enforcement_map(root)
    assert r["applicable"] is True and r["ok"] is False
    assert "invalid mode" in r["detail"]


def test_doctor_ok_valid_map(tmp_path):
    root = _platform(
        tmp_path, "spec_policy:\n  enforcement:\n    author: warn\n    dispatch: block\n"
    )
    r = _check_enforcement_map(root)
    assert r["applicable"] is True and r["ok"] is True


def test_doctor_not_applicable_for_scalar(tmp_path):
    root = _platform(tmp_path, "spec_policy:\n  enforcement: block\n")
    assert _check_enforcement_map(root)["applicable"] is False
