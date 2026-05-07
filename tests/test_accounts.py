"""Tests for scripts/accounts.py — account CRUD against launch-settings.yaml."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

# accounts is provided by the otaman_cli package
from otaman_cli.accounts import (
    _MARKER_BEGIN,
    _MARKER_END,
    _parse_user_ids,
    add_account,
    configure_telegram,
    detect_shell,
    install_shell_aliases,
    list_accounts,
    load_settings,
    remove_account,
    render_accounts_table,
    render_aliases_block,
    resolve_rc_path,
    resolve_settings_path,
)


@pytest.fixture
def settings_file(tmp_path):
    """Path to a launch-settings.yaml inside a maestro folder."""
    maestro = tmp_path / "my-maestro"
    maestro.mkdir()
    (maestro / "platform.yaml").write_text("project: test\n")
    return maestro / "launch-settings.yaml"


class TestResolveSettingsPath:
    def test_cli_override_wins(self, tmp_path, monkeypatch):
        custom = tmp_path / "custom.yaml"
        monkeypatch.chdir(tmp_path)
        assert resolve_settings_path(str(custom)) == custom.resolve()

    def test_falls_back_to_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = resolve_settings_path(None)
        assert result == tmp_path / "launch-settings.yaml"


class TestAddAccount:
    def test_creates_file_when_absent(self, settings_file):
        add_account(settings_file, "personal", "~/.claude-personal", label="Personal")
        assert settings_file.exists()
        data = load_settings(settings_file)
        assert data["accounts"]["personal"]["config_dir"] == "~/.claude-personal"
        assert data["accounts"]["personal"]["label"] == "Personal"

    def test_adds_to_empty_file(self, settings_file):
        settings_file.write_text("", encoding="utf-8")
        add_account(settings_file, "personal", "~/.claude-personal")
        data = load_settings(settings_file)
        assert "personal" in data["accounts"]

    def test_adds_to_file_without_accounts_block(self, settings_file):
        settings_file.write_text(
            "# My launcher config\n"
            "active_connection: local\n"
            "connections:\n"
            "  local:\n"
            "    type: local\n",
            encoding="utf-8",
        )
        add_account(settings_file, "personal", "~/.claude-personal")
        content = settings_file.read_text(encoding="utf-8")
        # Existing content preserved verbatim (comments included)
        assert "# My launcher config" in content
        assert "active_connection: local" in content
        # New account appended
        assert "accounts:" in content
        assert "personal:" in content

    def test_appends_to_existing_accounts_block(self, settings_file):
        settings_file.write_text(
            "accounts:\n"
            "  personal:\n"
            "    config_dir: ~/.claude-personal\n"
            "\n"
            "active_connection: local\n",
            encoding="utf-8",
        )
        add_account(settings_file, "riseapps", "~/.claude-riseapps")
        data = load_settings(settings_file)
        assert set(data["accounts"].keys()) == {"personal", "riseapps"}
        # Downstream keys still present
        assert data["active_connection"] == "local"

    def test_duplicate_name_raises(self, settings_file):
        add_account(settings_file, "personal", "~/.claude-personal")
        with pytest.raises(ValueError, match="already exists"):
            add_account(settings_file, "personal", "~/.claude-other")

    def test_invalid_name_raises(self, settings_file):
        with pytest.raises(ValueError, match="alphanumeric"):
            add_account(settings_file, "bad name!", "~/.claude-x")

    def test_empty_config_dir_raises(self, settings_file):
        with pytest.raises(ValueError, match="config_dir is required"):
            add_account(settings_file, "personal", "")

    def test_preserves_comments_in_other_sections(self, settings_file):
        """Line-based edit must not disturb comments outside the accounts block."""
        original = (
            "# Top comment\n"
            "accounts:\n"
            "  personal:\n"
            "    config_dir: ~/.claude-personal\n"
            "\n"
            "# Connection setup\n"
            "connections:\n"
            "  local:\n"
            "    type: local  # inline note\n"
        )
        settings_file.write_text(original, encoding="utf-8")
        add_account(settings_file, "riseapps", "~/.claude-riseapps")
        content = settings_file.read_text(encoding="utf-8")
        assert "# Top comment" in content
        assert "# Connection setup" in content
        assert "# inline note" in content


class TestListAccounts:
    def test_empty_when_no_file(self, settings_file):
        assert list_accounts(settings_file) == []

    def test_basic_listing(self, settings_file):
        add_account(settings_file, "personal", "~/.claude-personal", label="Personal")
        add_account(settings_file, "riseapps", "~/.claude-riseapps")
        records = list_accounts(settings_file)
        assert len(records) == 2
        names = {r["name"] for r in records}
        assert names == {"personal", "riseapps"}

    def test_sorted_by_name(self, settings_file):
        add_account(settings_file, "zulu", "~/.claude-zulu")
        add_account(settings_file, "alpha", "~/.claude-alpha")
        names = [r["name"] for r in list_accounts(settings_file)]
        assert names == ["alpha", "zulu"]

    def test_shows_connection_usage(self, settings_file):
        """used_by field aggregates connections referencing each account."""
        settings_file.write_text(
            yaml.dump(
                {
                    "accounts": {
                        "personal": {"config_dir": "~/.claude-personal"},
                        "riseapps": {"config_dir": "~/.claude-riseapps"},
                    },
                    "connections": {
                        "local": {"type": "local", "account": "personal"},
                        "lan": {"type": "ssh", "account": "riseapps"},
                        "mesh": {"type": "ssh", "account": "riseapps"},
                    },
                }
            ),
            encoding="utf-8",
        )
        records = list_accounts(settings_file)
        by_name = {r["name"]: r for r in records}
        assert by_name["personal"]["used_by"] == ["local"]
        assert by_name["riseapps"]["used_by"] == ["lan", "mesh"]


class TestRenderTable:
    def test_empty_message(self):
        assert "no accounts" in render_accounts_table([]).lower()

    def test_includes_headers(self, settings_file):
        add_account(settings_file, "personal", "~/.claude-personal", label="P")
        out = render_accounts_table(list_accounts(settings_file))
        assert "NAME" in out
        assert "CONFIG_DIR" in out
        assert "LABEL" in out
        assert "USED BY" in out
        assert "personal" in out


class TestRemoveAccount:
    def test_removes_existing_account(self, settings_file):
        add_account(settings_file, "personal", "~/.claude-personal")
        add_account(settings_file, "riseapps", "~/.claude-riseapps")
        remove_account(settings_file, "personal")
        data = load_settings(settings_file)
        assert set(data["accounts"].keys()) == {"riseapps"}

    def test_cleans_up_empty_accounts_block(self, settings_file):
        """Last account removed → entire `accounts:` header drops."""
        add_account(settings_file, "personal", "~/.claude-personal")
        remove_account(settings_file, "personal")
        data = load_settings(settings_file)
        # accounts key should be absent or at least not have stale header
        content = settings_file.read_text(encoding="utf-8")
        assert "accounts:" not in content or data.get("accounts") in (None, {})

    def test_missing_account_raises(self, settings_file):
        add_account(settings_file, "personal", "~/.claude-personal")
        with pytest.raises(KeyError, match="not defined"):
            remove_account(settings_file, "ghost")

    def test_missing_file_raises(self, settings_file):
        with pytest.raises(FileNotFoundError):
            remove_account(settings_file, "anything")

    def test_refuses_when_referenced_by_connection(self, settings_file):
        settings_file.write_text(
            yaml.dump(
                {
                    "accounts": {"personal": {"config_dir": "~/.claude-personal"}},
                    "connections": {
                        "local": {"type": "local", "account": "personal"},
                    },
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(RuntimeError, match="still referenced"):
            remove_account(settings_file, "personal")

    def test_force_overrides_reference_check(self, settings_file):
        settings_file.write_text(
            yaml.dump(
                {
                    "accounts": {"personal": {"config_dir": "~/.claude-personal"}},
                    "connections": {
                        "local": {"type": "local", "account": "personal"},
                    },
                }
            ),
            encoding="utf-8",
        )
        results = remove_account(settings_file, "personal", force=True)
        assert any("removed despite references" in r.lower() for r in results)
        data = load_settings(settings_file)
        assert "personal" not in (data.get("accounts") or {})


class TestRoundTrip:
    def test_add_then_list_then_remove(self, settings_file):
        add_account(settings_file, "personal", "~/.claude-personal", label="P")
        add_account(settings_file, "riseapps", "~/.claude-riseapps")
        records = list_accounts(settings_file)
        assert len(records) == 2
        remove_account(settings_file, "personal")
        records = list_accounts(settings_file)
        assert [r["name"] for r in records] == ["riseapps"]

    def test_add_remove_add_same_name(self, settings_file):
        """Must be able to re-add after remove (state fully cleaned)."""
        add_account(settings_file, "personal", "~/.claude-personal")
        remove_account(settings_file, "personal")
        add_account(settings_file, "personal", "~/.claude-personal-new")
        data = load_settings(settings_file)
        assert data["accounts"]["personal"]["config_dir"] == "~/.claude-personal-new"


class TestDetectShell:
    def test_windows_returns_powershell(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Windows")
        assert detect_shell() == "powershell"

    def test_unix_honors_shell_env(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setenv("SHELL", "/usr/bin/fish")
        assert detect_shell() == "fish"
        monkeypatch.setenv("SHELL", "/bin/zsh")
        assert detect_shell() == "zsh"
        monkeypatch.setenv("SHELL", "/bin/bash")
        assert detect_shell() == "bash"

    def test_unix_fallback_to_bash(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setenv("SHELL", "")
        assert detect_shell() == "bash"


class TestResolveRcPath:
    def test_bash(self):
        p = resolve_rc_path("bash")
        assert p.name == ".bashrc"

    def test_zsh(self):
        p = resolve_rc_path("zsh")
        assert p.name == ".zshrc"

    def test_fish(self):
        p = resolve_rc_path("fish")
        assert p.name == "config.fish"
        assert "fish" in str(p)

    def test_powershell(self, monkeypatch):
        monkeypatch.setenv("USERPROFILE", str(Path.home()))
        p = resolve_rc_path("powershell")
        assert p.name == "Profile.ps1"
        assert "PowerShell" in str(p)

    def test_override(self, tmp_path):
        custom = tmp_path / "custom.rc"
        p = resolve_rc_path("bash", str(custom))
        assert p == custom.resolve()

    def test_unknown_shell_raises(self):
        with pytest.raises(ValueError):
            resolve_rc_path("tcsh")


class TestRenderAliasesBlock:
    def _records(self):
        return [
            {"name": "personal", "config_dir": "~/.claude-personal",
             "label": "Personal", "used_by": []},
            {"name": "riseapps", "config_dir": "~/.claude-riseapps",
             "label": "", "used_by": []},
        ]

    def test_bash_functions(self):
        out = render_aliases_block(self._records(), "bash")
        assert _MARKER_BEGIN in out
        assert _MARKER_END in out
        assert "claude-personal()" in out
        assert "claude-riseapps()" in out
        assert "CLAUDE_CONFIG_DIR='~/.claude-personal'" in out
        assert 'command claude "$@"' in out

    def test_zsh_same_as_bash(self):
        """bash and zsh use the same function syntax."""
        b = render_aliases_block(self._records(), "bash")
        z = render_aliases_block(self._records(), "zsh")
        assert b == z

    def test_fish_syntax(self):
        out = render_aliases_block(self._records(), "fish")
        assert "function claude-personal" in out
        assert "function claude-riseapps" in out
        assert "end" in out
        assert "$argv" in out

    def test_powershell_syntax(self):
        out = render_aliases_block(self._records(), "powershell")
        assert "function claude-personal" in out
        assert "$env:CLAUDE_CONFIG_DIR" in out
        assert "@args" in out
        # Restore-on-exit semantics so per-function env doesn't leak.
        assert "finally" in out

    def test_preserves_label_as_comment(self):
        out = render_aliases_block(self._records(), "bash")
        assert "(Personal)" in out

    def test_empty_records_returns_empty(self):
        assert render_aliases_block([], "bash") == ""

    def test_single_quote_in_config_dir_escaped(self):
        records = [
            {"name": "weird", "config_dir": "~/it's-weird",
             "label": "", "used_by": []},
        ]
        out = render_aliases_block(records, "bash")
        # POSIX quote-escape sequence
        assert "'~/it'\\''s-weird'" in out


class TestInstallShellAliases:
    @pytest.fixture
    def populated_settings(self, settings_file):
        add_account(settings_file, "personal", "~/.claude-personal", label="Personal")
        add_account(settings_file, "riseapps", "~/.claude-riseapps")
        return settings_file

    def test_writes_to_target(self, populated_settings, tmp_path):
        target = tmp_path / "bashrc"
        install_shell_aliases(populated_settings, "bash", target=target)
        content = target.read_text(encoding="utf-8")
        assert _MARKER_BEGIN in content
        assert "claude-personal()" in content
        assert "claude-riseapps()" in content

    def test_creates_target_if_missing(self, populated_settings, tmp_path):
        target = tmp_path / "nested" / "bashrc"
        install_shell_aliases(populated_settings, "bash", target=target)
        assert target.exists()

    def test_appends_when_markers_absent(self, populated_settings, tmp_path):
        target = tmp_path / "bashrc"
        target.write_text("# Existing content\nalias ll='ls -la'\n", encoding="utf-8")
        install_shell_aliases(populated_settings, "bash", target=target)
        content = target.read_text(encoding="utf-8")
        assert "# Existing content" in content
        assert "alias ll='ls -la'" in content
        assert _MARKER_BEGIN in content
        # Existing content precedes the new block
        assert content.index("alias ll") < content.index(_MARKER_BEGIN)

    def test_replaces_block_when_markers_present(self, populated_settings, tmp_path):
        target = tmp_path / "bashrc"
        install_shell_aliases(populated_settings, "bash", target=target)
        # Now change accounts and re-run
        remove_account(populated_settings, "riseapps")
        add_account(populated_settings, "client-c", "~/.claude-client-c")
        install_shell_aliases(populated_settings, "bash", target=target)
        content = target.read_text(encoding="utf-8")
        assert "claude-personal()" in content
        assert "claude-client-c()" in content
        assert "claude-riseapps" not in content
        # Exactly one marker pair
        assert content.count(_MARKER_BEGIN) == 1
        assert content.count(_MARKER_END) == 1

    def test_preserves_user_content_around_block(self, populated_settings, tmp_path):
        target = tmp_path / "bashrc"
        target.write_text(
            "# Top of rc\n"
            f"{_MARKER_BEGIN}\nold junk\n{_MARKER_END}\n"
            "# Bottom of rc\n",
            encoding="utf-8",
        )
        install_shell_aliases(populated_settings, "bash", target=target)
        content = target.read_text(encoding="utf-8")
        assert "# Top of rc" in content
        assert "# Bottom of rc" in content
        assert "old junk" not in content
        assert "claude-personal()" in content

    def test_no_accounts_raises(self, settings_file, tmp_path):
        """install-shell-aliases on empty settings should error clearly."""
        with pytest.raises(RuntimeError, match="No accounts configured"):
            install_shell_aliases(settings_file, "bash", target=tmp_path / "rc")

    def test_rejects_unknown_shell(self, populated_settings, tmp_path):
        with pytest.raises(ValueError):
            install_shell_aliases(populated_settings, "tcsh", target=tmp_path / "rc")


class TestParseUserIds:
    def test_single(self):
        assert _parse_user_ids("123") == [123]

    def test_multiple(self):
        assert _parse_user_ids("123,456,789") == [123, 456, 789]

    def test_whitespace_tolerated(self):
        assert _parse_user_ids(" 123 , 456 ") == [123, 456]

    def test_empty_returns_empty(self):
        assert _parse_user_ids("") == []

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="must be an integer"):
            _parse_user_ids("123,abc")


class TestConfigureTelegram:
    def _make_base(self, settings_file):
        add_account(settings_file, "personal", "~/.claude-personal", label="Personal")
        return settings_file

    def test_adds_full_transport_config(self, settings_file):
        self._make_base(settings_file)
        configure_telegram(
            settings_file, "personal",
            group_id=-1001234567890,
            allowed_user_ids=[123, 456],
        )
        data = load_settings(settings_file)
        acct = data["accounts"]["personal"]
        assert acct["transport"] == "telegram"
        tc = acct["transport_config"]
        assert tc["group_id"] == -1001234567890
        assert tc["allowed_user_ids"] == [123, 456]
        assert tc["auto_create_topics"] is True
        # Long-form bot_token sources chain
        assert "bot_token" in tc
        assert "sources" in tc["bot_token"]
        source_types = [s["type"] for s in tc["bot_token"]["sources"]]
        assert source_types == ["env", "dotenv", "keyring"]

    def test_default_bot_token_env_from_account_name(self, settings_file):
        self._make_base(settings_file)
        configure_telegram(
            settings_file, "personal",
            group_id=-1001111, allowed_user_ids=[1],
        )
        data = load_settings(settings_file)
        env_source = data["accounts"]["personal"]["transport_config"]["bot_token"]["sources"][0]
        assert env_source == {"type": "env", "name": "MAESTRO_TG_BOT_PERSONAL"}

    def test_custom_bot_token_env(self, settings_file):
        self._make_base(settings_file)
        configure_telegram(
            settings_file, "personal",
            group_id=-1001111, allowed_user_ids=[1],
            bot_token_env="MY_CUSTOM_TOKEN",
        )
        data = load_settings(settings_file)
        envs = [
            s for s in data["accounts"]["personal"]["transport_config"]
                        ["bot_token"]["sources"]
            if s["type"] == "env"
        ]
        assert envs[0]["name"] == "MY_CUSTOM_TOKEN"

    def test_default_topic_id_persisted(self, settings_file):
        self._make_base(settings_file)
        configure_telegram(
            settings_file, "personal",
            group_id=-1001111, allowed_user_ids=[1],
            default_topic_id=42,
        )
        data = load_settings(settings_file)
        assert data["accounts"]["personal"]["transport_config"]["default_topic_id"] == 42

    def test_auto_create_topics_can_be_disabled(self, settings_file):
        self._make_base(settings_file)
        configure_telegram(
            settings_file, "personal",
            group_id=-1001111, allowed_user_ids=[1],
            auto_create_topics=False,
        )
        data = load_settings(settings_file)
        assert data["accounts"]["personal"]["transport_config"]["auto_create_topics"] is False

    def test_preserves_config_dir_and_label(self, settings_file):
        """Re-running configure-telegram must not wipe config_dir / label."""
        self._make_base(settings_file)
        configure_telegram(
            settings_file, "personal",
            group_id=-1001111, allowed_user_ids=[1],
        )
        data = load_settings(settings_file)
        assert data["accounts"]["personal"]["config_dir"] == "~/.claude-personal"
        assert data["accounts"]["personal"]["label"] == "Personal"

    def test_idempotent_overwrites_existing_block(self, settings_file):
        """Running twice with different values replaces in place."""
        self._make_base(settings_file)
        configure_telegram(
            settings_file, "personal",
            group_id=-1001111, allowed_user_ids=[1],
        )
        configure_telegram(
            settings_file, "personal",
            group_id=-1002222, allowed_user_ids=[2, 3],
            default_topic_id=99,
        )
        data = load_settings(settings_file)
        tc = data["accounts"]["personal"]["transport_config"]
        assert tc["group_id"] == -1002222
        assert tc["allowed_user_ids"] == [2, 3]
        assert tc["default_topic_id"] == 99
        # And YAML didn't accumulate stale blocks
        content = settings_file.read_text(encoding="utf-8")
        assert content.count("transport:") == 1
        assert content.count("transport_config:") == 1

    def test_preserves_other_accounts(self, settings_file):
        self._make_base(settings_file)
        add_account(settings_file, "riseapps", "~/.claude-riseapps")
        configure_telegram(
            settings_file, "personal",
            group_id=-1001111, allowed_user_ids=[1],
        )
        data = load_settings(settings_file)
        assert "riseapps" in data["accounts"]
        assert data["accounts"]["riseapps"]["config_dir"] == "~/.claude-riseapps"
        # riseapps shouldn't have telegram config
        assert "transport" not in (data["accounts"]["riseapps"] or {})

    def test_preserves_connections_block(self, settings_file):
        """Line-based edit must not disturb non-accounts sections."""
        settings_file.write_text(
            "accounts:\n"
            "  personal:\n"
            "    config_dir: ~/.claude-personal\n"
            "\n"
            "# Connection setup\n"
            "active_connection: local\n"
            "connections:\n"
            "  local:\n"
            "    type: local\n"
            "    account: personal\n",
            encoding="utf-8",
        )
        configure_telegram(
            settings_file, "personal",
            group_id=-1001111, allowed_user_ids=[1],
        )
        content = settings_file.read_text(encoding="utf-8")
        assert "# Connection setup" in content
        assert "active_connection: local" in content
        assert "account: personal" in content
        # And telegram config landed
        assert "transport: telegram" in content

    def test_replaces_short_form_telegram_block(self, settings_file):
        """Legacy `telegram:` short form is cleaned up when reconfiguring."""
        settings_file.write_text(
            "accounts:\n"
            "  personal:\n"
            "    config_dir: ~/.claude-personal\n"
            "    telegram:\n"
            "      group_id: -1001111\n"
            "      bot_token_env: OLD_VAR\n",
            encoding="utf-8",
        )
        configure_telegram(
            settings_file, "personal",
            group_id=-1002222, allowed_user_ids=[42],
        )
        content = settings_file.read_text(encoding="utf-8")
        # Old short form gone
        assert "OLD_VAR" not in content
        # New long form present
        assert "transport: telegram" in content
        assert "transport_config:" in content
        data = load_settings(settings_file)
        tc = data["accounts"]["personal"]["transport_config"]
        assert tc["group_id"] == -1002222

    def test_missing_file_raises(self, settings_file):
        with pytest.raises(FileNotFoundError):
            configure_telegram(
                settings_file, "personal",
                group_id=-1, allowed_user_ids=[1],
            )

    def test_missing_account_raises(self, settings_file):
        settings_file.write_text(
            "accounts:\n  other: {config_dir: ~/.claude-other}\n",
            encoding="utf-8",
        )
        with pytest.raises(KeyError, match="not defined"):
            configure_telegram(
                settings_file, "missing",
                group_id=-1, allowed_user_ids=[1],
            )

    def test_emits_next_steps(self, settings_file):
        """Status messages include a user-facing 'what to do next' list."""
        self._make_base(settings_file)
        msgs = configure_telegram(
            settings_file, "personal",
            group_id=-1001111, allowed_user_ids=[1],
        )
        full = "\n".join(msgs).lower()
        assert "next steps" in full
        assert "secrets.env" in full
        assert "maestro bridge run" in full
        assert "maestro afk on" in full
