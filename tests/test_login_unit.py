"""Unit tests for the device-flow login module — no real network."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from otaman_cli.auth.login import (
    CachedToken,
    DeviceFlowConfig,
    LoginError,
    build_scope,
    config_from_env,
    initiate_device_flow,
    load_token,
    poll_for_token,
    save_token,
)


# ---------------------------------------------------------------------------
# build_scope


class TestBuildScope:
    def test_default_scope_when_unset(self):
        cfg = DeviceFlowConfig(issuer="http://x", client_id="c")
        s = build_scope(cfg)
        assert "openid" in s
        assert "profile" in s
        assert "urn:zitadel:iam:org:projects:roles" in s

    def test_project_id_adds_aud_scope(self):
        cfg = DeviceFlowConfig(issuer="http://x", client_id="c", project_id="proj-123")
        s = build_scope(cfg)
        assert "urn:zitadel:iam:org:project:id:proj-123:aud" in s

    def test_custom_scopes_respected(self):
        cfg = DeviceFlowConfig(issuer="http://x", client_id="c", scopes=["openid", "email"])
        s = build_scope(cfg)
        assert "email" in s
        assert s.startswith("openid")

    def test_aud_scope_not_duplicated(self):
        cfg = DeviceFlowConfig(
            issuer="http://x", client_id="c",
            project_id="proj-1",
            scopes=["openid", "urn:zitadel:iam:org:project:id:proj-1:aud"],
        )
        s = build_scope(cfg)
        assert s.count("urn:zitadel:iam:org:project:id:proj-1:aud") == 1


# ---------------------------------------------------------------------------
# CachedToken / save / load


class TestCachedToken:
    def test_roundtrip(self, tmp_path):
        tok = CachedToken(
            access_token="abc", refresh_token="def",
            expires_at=int(time.time() + 3600),
            issuer="http://x", client_id="c1",
        )
        p = save_token(tok, tmp_path / "tok.json")
        assert p.is_file()
        loaded = load_token(p)
        assert loaded == tok

    def test_load_missing_returns_none(self, tmp_path):
        assert load_token(tmp_path / "absent.json") is None

    def test_load_malformed_returns_none(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not-json")
        assert load_token(p) is None

    def test_is_expired_when_past(self):
        tok = CachedToken(
            access_token="x", refresh_token=None,
            expires_at=int(time.time()) - 100,
            issuer="i", client_id="c",
        )
        assert tok.is_expired is True

    def test_is_expired_with_leeway(self):
        tok = CachedToken(
            access_token="x", refresh_token=None,
            expires_at=int(time.time()) + 5,  # well within 30s leeway
            issuer="i", client_id="c",
        )
        assert tok.is_expired is True

    def test_no_expiry_means_not_expired(self):
        tok = CachedToken(
            access_token="x", refresh_token=None,
            expires_at=0, issuer="i", client_id="c",
        )
        assert tok.is_expired is False


# ---------------------------------------------------------------------------
# config_from_env


class TestConfigFromEnv:
    def test_required_vars_missing_raises(self, monkeypatch):
        monkeypatch.delenv("OIDC_ISSUER", raising=False)
        monkeypatch.delenv("OIDC_CLI_CLIENT_ID", raising=False)
        with pytest.raises(LoginError, match="OIDC config missing"):
            config_from_env()

    def test_minimal_config(self, monkeypatch):
        monkeypatch.setenv("OIDC_ISSUER", "https://zitadel.test")
        monkeypatch.setenv("OIDC_CLI_CLIENT_ID", "cli-id")
        monkeypatch.delenv("OIDC_PROJECT_ID", raising=False)
        monkeypatch.delenv("OIDC_EXTERNAL_HOST", raising=False)
        cfg = config_from_env()
        assert cfg.issuer == "https://zitadel.test"
        assert cfg.client_id == "cli-id"
        assert cfg.project_id == ""

    def test_project_id_propagated(self, monkeypatch):
        monkeypatch.setenv("OIDC_ISSUER", "https://x")
        monkeypatch.setenv("OIDC_CLI_CLIENT_ID", "c")
        monkeypatch.setenv("OIDC_PROJECT_ID", "p-123")
        cfg = config_from_env()
        assert cfg.project_id == "p-123"

    def test_token_cache_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OIDC_ISSUER", "https://x")
        monkeypatch.setenv("OIDC_CLI_CLIENT_ID", "c")
        override = tmp_path / "custom-token.cache"
        monkeypatch.setenv("OTAMAN_TOKEN_CACHE", str(override))
        cfg = config_from_env()
        assert cfg.token_path == override

    # ------------------------------------------------------------------- F032
    def test_http_non_loopback_refused_by_default(self, monkeypatch):
        monkeypatch.setenv("OIDC_ISSUER", "http://100.65.57.73:8080")
        monkeypatch.setenv("OIDC_CLI_CLIENT_ID", "c")
        monkeypatch.delenv("OTAMAN_ALLOW_INSECURE_OIDC", raising=False)
        with pytest.raises(LoginError, match="plaintext http"):
            config_from_env()

    def test_http_loopback_allowed_without_escape_hatch(self, monkeypatch):
        monkeypatch.setenv("OIDC_ISSUER", "http://127.0.0.1:8080")
        monkeypatch.setenv("OIDC_CLI_CLIENT_ID", "c")
        monkeypatch.delenv("OTAMAN_ALLOW_INSECURE_OIDC", raising=False)
        cfg = config_from_env()
        assert cfg.issuer == "http://127.0.0.1:8080"

    def test_http_localhost_allowed_without_escape_hatch(self, monkeypatch):
        monkeypatch.setenv("OIDC_ISSUER", "http://localhost:8080")
        monkeypatch.setenv("OIDC_CLI_CLIENT_ID", "c")
        monkeypatch.delenv("OTAMAN_ALLOW_INSECURE_OIDC", raising=False)
        cfg = config_from_env()
        assert cfg.issuer == "http://localhost:8080"

    def test_http_non_loopback_allowed_with_escape_hatch(self, monkeypatch, capsys):
        monkeypatch.setenv("OIDC_ISSUER", "http://100.65.57.73:8080")
        monkeypatch.setenv("OIDC_CLI_CLIENT_ID", "c")
        monkeypatch.setenv("OTAMAN_ALLOW_INSECURE_OIDC", "1")
        cfg = config_from_env()
        assert cfg.issuer == "http://100.65.57.73:8080"
        assert "WARNING" in capsys.readouterr().err

    def test_https_non_loopback_always_allowed(self, monkeypatch):
        monkeypatch.setenv("OIDC_ISSUER", "https://accounts.example.com")
        monkeypatch.setenv("OIDC_CLI_CLIENT_ID", "c")
        monkeypatch.delenv("OTAMAN_ALLOW_INSECURE_OIDC", raising=False)
        cfg = config_from_env()
        assert cfg.issuer == "https://accounts.example.com"

    def test_unknown_scheme_refused(self, monkeypatch):
        monkeypatch.setenv("OIDC_ISSUER", "ftp://x")
        monkeypatch.setenv("OIDC_CLI_CLIENT_ID", "c")
        with pytest.raises(LoginError, match="must use https"):
            config_from_env()


# ---------------------------------------------------------------------------
# Device flow with mocked HTTP


class _FakeHTTPResponse:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestInitiateDeviceFlow:
    def test_happy_path(self, monkeypatch):
        cfg = DeviceFlowConfig(issuer="http://x", client_id="c")
        expected = {
            "device_code": "abc",
            "user_code": "ABCD-1234",
            "verification_uri": "http://x/device",
            "verification_uri_complete": "http://x/device?user_code=ABCD-1234",
            "expires_in": 600,
            "interval": 5,
        }
        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(expected)):
            r = initiate_device_flow(cfg)
        assert r["device_code"] == "abc"

    def test_server_error_payload_raises(self, monkeypatch):
        cfg = DeviceFlowConfig(issuer="http://x", client_id="c")
        body = {"error": "invalid_client", "error_description": "client not found"}
        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(body)):
            with pytest.raises(LoginError, match="client not found"):
                initiate_device_flow(cfg)

    def test_missing_required_field_raises(self, monkeypatch):
        cfg = DeviceFlowConfig(issuer="http://x", client_id="c")
        body = {"device_code": "abc"}  # missing user_code, verification_uri_complete
        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(body)):
            with pytest.raises(LoginError, match="missing"):
                initiate_device_flow(cfg)


class TestPollForToken:
    def test_immediate_success(self, monkeypatch):
        cfg = DeviceFlowConfig(issuer="http://x", client_id="c")
        body = {"access_token": "TOK", "expires_in": 3600}
        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(body)):
            r = poll_for_token(cfg, device_code="dc", interval=1, expires_in=60)
        assert r["access_token"] == "TOK"

    def test_authorization_pending_then_success(self, monkeypatch):
        cfg = DeviceFlowConfig(issuer="http://x", client_id="c")
        responses = [
            _FakeHTTPResponse({"error": "authorization_pending"}),
            _FakeHTTPResponse({"error": "authorization_pending"}),
            _FakeHTTPResponse({"access_token": "TOK", "expires_in": 3600}),
        ]
        with patch("urllib.request.urlopen", side_effect=responses), \
             patch("time.sleep"):  # don't actually sleep in tests
            r = poll_for_token(cfg, device_code="dc", interval=1, expires_in=60)
        assert r["access_token"] == "TOK"

    def test_slow_down_backs_off(self, monkeypatch):
        cfg = DeviceFlowConfig(issuer="http://x", client_id="c")
        responses = [
            _FakeHTTPResponse({"error": "slow_down"}),
            _FakeHTTPResponse({"access_token": "TOK", "expires_in": 3600}),
        ]
        with patch("urllib.request.urlopen", side_effect=responses), \
             patch("time.sleep"):
            r = poll_for_token(cfg, device_code="dc", interval=1, expires_in=60)
        assert r["access_token"] == "TOK"

    def test_access_denied_raises(self, monkeypatch):
        cfg = DeviceFlowConfig(issuer="http://x", client_id="c")
        body = {"error": "access_denied", "error_description": "user said no"}
        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(body)):
            with pytest.raises(LoginError, match="access_denied"):
                poll_for_token(cfg, device_code="dc", interval=1, expires_in=60)

    def test_timeout_when_pending_forever(self, monkeypatch):
        cfg = DeviceFlowConfig(issuer="http://x", client_id="c")
        body = {"error": "authorization_pending"}
        # Fake clock that always exceeds the deadline immediately.
        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(body)), \
             patch("time.sleep"), \
             patch("time.time", side_effect=[0, 999, 999, 999]):
            with pytest.raises(LoginError, match="timed out"):
                poll_for_token(cfg, device_code="dc", interval=1, expires_in=60)
