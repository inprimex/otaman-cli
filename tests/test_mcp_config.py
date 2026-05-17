"""Tests for otaman mcp-config CLI."""

from __future__ import annotations

import io
import json
import time
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from otaman_cli import mcp_config


# ---- Helpers ----------------------------------------------------------


def _write_token(path: Path, *, access_token="tok-abc", expires_at=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"access_token": access_token, "expires_at": expires_at or 0,
            "issuer": "https://x", "client_id": "c"}
    path.write_text(json.dumps(data), encoding="utf-8")


# ---- load_cached_token ------------------------------------------------


class TestLoadCachedToken:
    def test_missing_file_returns_none(self, tmp_path):
        assert mcp_config.load_cached_token(tmp_path / "nope") == (None, None)

    def test_valid_token_not_expired(self, tmp_path):
        cache = tmp_path / "token.cache"
        _write_token(cache, access_token="abc", expires_at=int(time.time()) + 3600)
        token, expired = mcp_config.load_cached_token(cache)
        assert token == "abc"
        assert expired is False

    def test_expired_token_flagged(self, tmp_path):
        cache = tmp_path / "token.cache"
        _write_token(cache, access_token="abc", expires_at=int(time.time()) - 100)
        token, expired = mcp_config.load_cached_token(cache)
        assert token == "abc"
        assert expired is True

    def test_no_expiry_field_means_not_expired(self, tmp_path):
        """Tokens with expires_at=0 are treated as never-expires."""
        cache = tmp_path / "token.cache"
        _write_token(cache, access_token="abc", expires_at=0)
        _, expired = mcp_config.load_cached_token(cache)
        assert expired is False

    def test_malformed_json_returns_none(self, tmp_path):
        cache = tmp_path / "token.cache"
        cache.write_text("not-json", encoding="utf-8")
        assert mcp_config.load_cached_token(cache) == (None, None)


# ---- build_config ------------------------------------------------------


class TestBuildConfig:
    def test_basic_shape(self):
        cfg = mcp_config.build_config(
            bridge_url="http://localhost:8090",
            token="my-token",
            server_name="otaman-bridge",
        )
        assert cfg == {
            "mcpServers": {
                "otaman-bridge": {
                    "type": "http",
                    "url": "http://localhost:8090/mcp",
                    "headers": {"Authorization": "Bearer my-token"},
                },
            },
        }

    def test_bridge_url_trailing_slash_stripped(self):
        cfg = mcp_config.build_config(
            bridge_url="http://localhost:8090/", token="t", server_name="s",
        )
        assert cfg["mcpServers"]["s"]["url"] == "http://localhost:8090/mcp"

    def test_custom_server_name(self):
        cfg = mcp_config.build_config(
            bridge_url="http://x", token="t", server_name="my-name",
        )
        assert "my-name" in cfg["mcpServers"]


# ---- main() -----------------------------------------------------------


class TestMain:
    def test_no_token_returns_1(self, tmp_path, capsys):
        rc = mcp_config.main([
            "--bridge-url", "http://localhost:8090",
            "--token-cache", str(tmp_path / "missing"),
        ])
        assert rc == 1
        err = capsys.readouterr().err
        assert "otaman login" in err

    def test_expired_token_returns_1_without_allow_expired(self, tmp_path, capsys):
        cache = tmp_path / "token.cache"
        _write_token(cache, expires_at=int(time.time()) - 100)
        rc = mcp_config.main([
            "--bridge-url", "http://localhost:8090",
            "--token-cache", str(cache),
        ])
        assert rc == 1
        err = capsys.readouterr().err
        assert "expired" in err

    def test_allow_expired_emits_config(self, tmp_path, capsys):
        cache = tmp_path / "token.cache"
        _write_token(cache, expires_at=int(time.time()) - 100, access_token="stale")
        rc = mcp_config.main([
            "--bridge-url", "http://localhost:8090",
            "--token-cache", str(cache),
            "--allow-expired",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        cfg = json.loads(out)
        assert cfg["mcpServers"]["otaman-bridge"]["headers"]["Authorization"] == "Bearer stale"

    def test_valid_token_stdout_emits_pretty_json(self, tmp_path, capsys):
        cache = tmp_path / "token.cache"
        _write_token(cache, access_token="abc", expires_at=int(time.time()) + 3600)
        rc = mcp_config.main([
            "--bridge-url", "http://localhost:8090",
            "--token-cache", str(cache),
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "\n" in out  # indented
        cfg = json.loads(out)
        assert cfg["mcpServers"]["otaman-bridge"]["url"] == "http://localhost:8090/mcp"

    def test_output_file_writes_and_chmods(self, tmp_path, capsys):
        cache = tmp_path / "token.cache"
        _write_token(cache, access_token="abc", expires_at=int(time.time()) + 3600)
        out_file = tmp_path / "out" / ".mcp.json"
        rc = mcp_config.main([
            "--bridge-url", "http://localhost:8090",
            "--token-cache", str(cache),
            "--output", str(out_file),
        ])
        assert rc == 0
        assert out_file.is_file()
        cfg = json.loads(out_file.read_text())
        assert cfg["mcpServers"]["otaman-bridge"]["headers"]["Authorization"] == "Bearer abc"

    def test_custom_server_name_arg(self, tmp_path, capsys):
        cache = tmp_path / "token.cache"
        _write_token(cache, access_token="t", expires_at=int(time.time()) + 3600)
        mcp_config.main([
            "--bridge-url", "http://localhost:8090",
            "--token-cache", str(cache),
            "--server-name", "custom-name",
        ])
        out = capsys.readouterr().out
        cfg = json.loads(out)
        assert "custom-name" in cfg["mcpServers"]

    def test_indent_zero_emits_single_line(self, tmp_path, capsys):
        cache = tmp_path / "token.cache"
        _write_token(cache, access_token="t", expires_at=int(time.time()) + 3600)
        mcp_config.main([
            "--bridge-url", "http://localhost:8090",
            "--token-cache", str(cache),
            "--indent", "0",
        ])
        out = capsys.readouterr().out.strip()
        assert "\n" not in out  # single line
        json.loads(out)  # still valid JSON
