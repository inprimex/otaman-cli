"""Tests for human-roster tasks 5.1, 5.2, 5.3.

5.1 — `otaman init` wizard prompt collects roster entries iteratively
5.2 — `otaman pm init --roster` resolves pm-user-id via the adapter
5.3 — `otaman doctor` reports pm-sync/roster mismatches as ERROR/WARN
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = str(REPO_ROOT / "src")
CORE_PATH = str(REPO_ROOT.parent / "otaman-core" / "src")
ADAPTERS_PATH = str(REPO_ROOT.parent / "otaman-adapters" / "src")

for p in (SRC_PATH, CORE_PATH, ADAPTERS_PATH):
    if p not in sys.path:
        sys.path.insert(0, p)

from otaman_cli.doctor import check_human_roster  # noqa: E402
from otaman_cli.onboard.program_init.platform_gen import write_platform_yaml  # noqa: E402
from otaman_cli.onboard.program_init.runner import (  # noqa: E402
    _VALID_ROSTER_ROLES,
    _prompt_human_roster,
)
from otaman_cli.pm.cmd_init import _resolve_roster_pm_user_ids  # noqa: E402


# ---------------------------------------------------------------- task 5.1
class TestWizardRosterPrompt:
    """The wizard collects 0..N roster entries when pm-sync is enabled."""

    def test_skipped_when_pm_sync_not_enabled(self, monkeypatch):
        """Without pm-sync, the roster prompt does not fire at all."""

        def _boom(*_a, **_kw):
            raise AssertionError("input() must NOT be called when pm-sync disabled")

        monkeypatch.setattr("builtins.input", _boom)
        answers = {"pm_sync_enabled": False}
        _prompt_human_roster(answers, dry_run=False)
        assert "human_roster" not in answers

    def test_dry_run_does_not_prompt(self, monkeypatch):
        def _boom(*_a, **_kw):
            raise AssertionError("input() must NOT be called in dry-run")

        monkeypatch.setattr("builtins.input", _boom)
        _prompt_human_roster({"pm_sync_enabled": True}, dry_run=True)

    def test_user_says_no_collects_nothing(self, monkeypatch):
        responses = iter(["n"])
        monkeypatch.setattr("builtins.input", lambda *_: next(responses))
        answers = {"pm_sync_enabled": True}
        _prompt_human_roster(answers, dry_run=False)
        assert "human_roster" not in answers

    def test_single_entry_captured(self, monkeypatch):
        responses = iter(
            [
                "y",  # start the roster prompt
                "Roman",
                "r@x.com",  # name, email
                "cofounder,cto",  # roles
                "n",  # don't add another
            ]
        )
        monkeypatch.setattr("builtins.input", lambda *_: next(responses))
        answers = {"pm_sync_enabled": True}
        _prompt_human_roster(answers, dry_run=False)
        assert answers["human_roster"] == [
            {"name": "Roman", "email": "r@x.com", "roles": ["cofounder", "cto"]},
        ]

    def test_two_entries_captured(self, monkeypatch):
        responses = iter(
            [
                "y",
                "Alice",
                "a@x.com",
                "cto",
                "y",
                "Bob",
                "b@x.com",
                "developer,cpo",
                "n",
            ]
        )
        monkeypatch.setattr("builtins.input", lambda *_: next(responses))
        answers = {"pm_sync_enabled": True}
        _prompt_human_roster(answers, dry_run=False)
        assert len(answers["human_roster"]) == 2
        assert answers["human_roster"][0]["name"] == "Alice"
        assert answers["human_roster"][1]["roles"] == ["developer", "cpo"]

    def test_blank_name_finishes_collection(self, monkeypatch):
        responses = iter(
            [
                "y",
                "Alice",
                "a@x.com",
                "cto",
                "y",
                "",  # blank name → finish
            ]
        )
        monkeypatch.setattr("builtins.input", lambda *_: next(responses))
        answers = {"pm_sync_enabled": True}
        _prompt_human_roster(answers, dry_run=False)
        assert len(answers["human_roster"]) == 1

    def test_empty_roles_skips_entry(self, monkeypatch, capsys):
        responses = iter(
            [
                "y",
                "Alice",
                "a@x.com",
                "",  # empty roles → skip
                # The wizard loops back and asks for next entry's name
                "",  # blank → finish
            ]
        )
        monkeypatch.setattr("builtins.input", lambda *_: next(responses))
        answers = {"pm_sync_enabled": True}
        _prompt_human_roster(answers, dry_run=False)
        # No entries collected (the only one was skipped)
        assert "human_roster" not in answers
        # User-visible message about the skip
        assert "skipped" in capsys.readouterr().out.lower()

    def test_unknown_role_warning_but_entry_accepted(self, monkeypatch, capsys):
        """Per spec, unknown role is a doctor WARN, not a wizard hard-fail."""
        responses = iter(
            [
                "y",
                "Alice",
                "a@x.com",
                "cto,sales-lead",  # 'sales-lead' is unknown
                "n",
            ]
        )
        monkeypatch.setattr("builtins.input", lambda *_: next(responses))
        answers = {"pm_sync_enabled": True}
        _prompt_human_roster(answers, dry_run=False)
        # Entry still recorded
        assert len(answers["human_roster"]) == 1
        assert "sales-lead" in answers["human_roster"][0]["roles"]
        # Warning surfaced
        out = capsys.readouterr().out.lower()
        assert "unknown role" in out

    def test_eof_falls_through_safely(self, monkeypatch):
        """Non-TTY (EOF) doesn't crash — empty roster, no exception."""

        def _eof(*_a, **_kw):
            raise EOFError

        monkeypatch.setattr("builtins.input", _eof)
        answers = {"pm_sync_enabled": True}
        _prompt_human_roster(answers, dry_run=False)
        assert "human_roster" not in answers


class TestPlatformYamlEmitsRoster:
    """`write_platform_yaml` writes the `human-roster:` block when answers has entries."""

    def test_roster_present_in_output(self, tmp_path: Path):
        out = tmp_path / "platform.yaml"
        write_platform_yaml(
            {
                "program_name": "tst",
                "primary_repo": ".",
                "human_roster": [
                    {"name": "Roman", "email": "r@x.com", "roles": ["cofounder"]},
                ],
                "mode": 1,
                "active_edition": "ce",
            },
            out,
        )
        doc = yaml.safe_load(out.read_text())
        assert doc.get("human-roster") == [
            {"name": "Roman", "email": "r@x.com", "roles": ["cofounder"]},
        ]

    def test_no_roster_omits_block(self, tmp_path: Path):
        """When no roster collected, the block is not emitted (clean default)."""
        out = tmp_path / "platform.yaml"
        write_platform_yaml(
            {
                "program_name": "tst",
                "primary_repo": ".",
                "mode": 1,
                "active_edition": "ce",
            },
            out,
        )
        doc = yaml.safe_load(out.read_text())
        assert "human-roster" not in doc


# ---------------------------------------------------------------- task 5.2
class TestRosterResolver:
    """`otaman pm init --roster` resolves pm-user-id per entry."""

    def _write_platform(self, tmp_path: Path, roster: list[dict]) -> Path:
        p = tmp_path / "platform.yaml"
        body = yaml.safe_dump(
            {
                "project": "tst",
                "version": "1.0",
                "repos": [{"name": "r", "path": ".", "owner": "x"}],
                "pm-sync": {"provider": "easy8", "base-url": "http://pm.example.com"},
                "human-roster": roster,
            },
            sort_keys=False,
        )
        p.write_text(body, encoding="utf-8")
        return p

    def test_dry_run_does_not_call_adapter(self, tmp_path: Path):
        platform = self._write_platform(
            tmp_path,
            [
                {"name": "Alice", "email": "a@x.com", "roles": ["cto"]},
            ],
        )
        mock_adapter = MagicMock()
        mock_UI = MagicMock()
        _resolve_roster_pm_user_ids(platform, adapter=mock_adapter, dry_run=True, UI=mock_UI)
        # Adapter not called in dry-run
        mock_adapter.get_users.assert_not_called()
        # File unchanged
        doc = yaml.safe_load(platform.read_text())
        assert doc["human-roster"][0].get("pm-user-id") is None

    def test_resolves_by_email_and_writes_back(self, tmp_path: Path, monkeypatch):
        platform = self._write_platform(
            tmp_path,
            [
                {"name": "Alice", "email": "alice@x.com", "roles": ["cto"]},
            ],
        )
        # Mock the adapter exports
        mock_easy8 = MagicMock()

        # Build the HumanRosterEntry stub class + resolver
        class _StubEntry:
            def __init__(self, name, email, roles, pm_user_id=None):
                self.name = name
                self.email = email
                self.roles = roles
                self.pm_user_id = pm_user_id

        mock_easy8.HumanRosterEntry = _StubEntry
        mock_easy8.resolve_pm_user_id = MagicMock(return_value=7)

        monkeypatch.setitem(sys.modules, "otaman_adapters.easy8", mock_easy8)
        mock_UI = MagicMock()
        _resolve_roster_pm_user_ids(
            platform,
            adapter=MagicMock(),
            dry_run=False,
            UI=mock_UI,
        )
        # File now has pm-user-id: 7
        doc = yaml.safe_load(platform.read_text())
        assert doc["human-roster"][0]["pm-user-id"] == 7
        # Success message logged
        ok_calls = [c.args[0] for c in mock_UI.ok.call_args_list]
        assert any("pm-user-id=7" in s for s in ok_calls)

    def test_unresolved_entry_logs_warning_and_continues(self, tmp_path: Path, monkeypatch):
        platform = self._write_platform(
            tmp_path,
            [
                {"name": "Alice", "email": "alice@x.com", "roles": ["cto"]},
                {"name": "Ghost", "email": "ghost@nowhere.x", "roles": ["developer"]},
            ],
        )

        class _StubEntry:
            def __init__(self, name, email, roles, pm_user_id=None):
                self.name = name
                self.email = email
                self.roles = roles
                self.pm_user_id = pm_user_id

        def _resolve(adapter, entry):
            return 7 if entry.email == "alice@x.com" else None

        mock_easy8 = MagicMock()
        mock_easy8.HumanRosterEntry = _StubEntry
        mock_easy8.resolve_pm_user_id = _resolve
        monkeypatch.setitem(sys.modules, "otaman_adapters.easy8", mock_easy8)

        mock_UI = MagicMock()
        _resolve_roster_pm_user_ids(
            platform,
            adapter=MagicMock(),
            dry_run=False,
            UI=mock_UI,
        )
        doc = yaml.safe_load(platform.read_text())
        assert doc["human-roster"][0]["pm-user-id"] == 7
        assert doc["human-roster"][1].get("pm-user-id") is None
        # Warning issued for Ghost
        warn_calls = [c.args[0] for c in mock_UI.warn.call_args_list]
        assert any("Ghost" in s for s in warn_calls)

    def test_already_resolved_entry_skipped(self, tmp_path: Path, monkeypatch):
        platform = self._write_platform(
            tmp_path,
            [
                {"name": "Alice", "email": "a@x.com", "roles": ["cto"], "pm-user-id": 99},
            ],
        )

        class _StubEntry:
            def __init__(self, *a, **k):
                pass

        mock_easy8 = MagicMock()
        mock_easy8.HumanRosterEntry = _StubEntry
        mock_easy8.resolve_pm_user_id = MagicMock(return_value=999)
        monkeypatch.setitem(sys.modules, "otaman_adapters.easy8", mock_easy8)

        mock_UI = MagicMock()
        _resolve_roster_pm_user_ids(
            platform,
            adapter=MagicMock(),
            dry_run=False,
            UI=mock_UI,
        )
        doc = yaml.safe_load(platform.read_text())
        # pm-user-id NOT overwritten
        assert doc["human-roster"][0]["pm-user-id"] == 99
        mock_easy8.resolve_pm_user_id.assert_not_called()

    def test_no_roster_block_silent_noop(self, tmp_path: Path):
        p = tmp_path / "platform.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "project": "tst",
                    "version": "1.0",
                    "repos": [{"name": "r", "path": ".", "owner": "x"}],
                }
            ),
            encoding="utf-8",
        )
        mock_UI = MagicMock()
        _resolve_roster_pm_user_ids(p, adapter=MagicMock(), dry_run=False, UI=mock_UI)
        # No errors, just a muted message
        muted_calls = [c.args[0] for c in mock_UI.muted.call_args_list]
        assert any("No `human-roster`" in s for s in muted_calls)


# ---------------------------------------------------------------- task 5.3
class TestDoctorRosterChecks:
    """`otaman doctor` reports pm-sync/roster issues."""

    def test_pm_sync_without_roster_is_fail(self):
        config = {
            "pm-sync": {"provider": "easy8", "base-url": "http://pm.example.com"},
            # no human-roster
        }
        result = check_human_roster(config)
        assert result["status"] == "fail"
        msgs = [i["message"] for i in result["issues"]]
        assert any("empty" in m.lower() for m in msgs)

    def test_pm_sync_with_empty_roster_is_fail(self):
        config = {
            "pm-sync": {"provider": "easy8"},
            "human-roster": [],
        }
        result = check_human_roster(config)
        assert result["status"] == "fail"

    def test_no_pm_sync_skips_check(self):
        """When pm-sync is absent, the roster is optional → skip."""
        result = check_human_roster({})
        assert result["status"] == "ok"
        assert result["details"].get("skipped")

    def test_missing_pm_user_id_warns(self):
        config = {
            "pm-sync": {"provider": "easy8"},
            "human-roster": [
                {"name": "Alice", "email": "a@x.com", "roles": ["cto"]},  # no pm-user-id
            ],
        }
        result = check_human_roster(config)
        assert result["status"] == "warn"
        msgs = [i["message"] for i in result["issues"]]
        assert any("pm-user-id not set" in m for m in msgs)

    def test_unknown_role_warns(self):
        config = {
            "pm-sync": {"provider": "easy8"},
            "human-roster": [
                {
                    "name": "Alice",
                    "email": "a@x.com",
                    "roles": ["cto", "sales-lead"],
                    "pm-user-id": 7,
                },
            ],
        }
        result = check_human_roster(config)
        # Has at least one warning about the unknown role
        msgs = [i["message"] for i in result["issues"]]
        assert any("sales-lead" in m for m in msgs)

    def test_missing_email_warns(self):
        config = {
            "pm-sync": {"provider": "easy8"},
            "human-roster": [
                {"name": "Alice", "roles": ["cto"], "pm-user-id": 7},  # no email
            ],
        }
        result = check_human_roster(config)
        msgs = [i["message"] for i in result["issues"]]
        assert any("email" in m.lower() for m in msgs)

    def test_empty_roles_list_is_fail(self):
        config = {
            "pm-sync": {"provider": "easy8"},
            "human-roster": [
                {"name": "Alice", "email": "a@x.com", "roles": [], "pm-user-id": 7},
            ],
        }
        result = check_human_roster(config)
        assert result["status"] == "fail"
        msgs = [i["message"] for i in result["issues"]]
        assert any("roles" in m.lower() and "empty" in m.lower() for m in msgs)

    def test_fully_valid_roster_passes(self):
        config = {
            "pm-sync": {"provider": "easy8"},
            "human-roster": [
                {"name": "Alice", "email": "a@x.com", "roles": ["cto"], "pm-user-id": 7},
                {"name": "Roman", "email": "r@x.com", "roles": ["cofounder"], "pm-user-id": 1},
            ],
        }
        result = check_human_roster(config)
        assert result["status"] == "ok"
        assert result["issues"] == []


class TestValidRoles:
    def test_valid_roles_constant(self):
        # `approver` (hitl-default-approver 2.3) is the proposal-rights grant;
        # the rest are the original org roles.
        assert set(_VALID_ROSTER_ROLES) == {"approver", "cofounder", "cto", "cpo", "developer"}
