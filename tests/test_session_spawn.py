"""Tests for `otaman session spawn` CLI."""

from __future__ import annotations

import base64
import io
import json
import urllib.error
from pathlib import Path

import pytest

from otaman_cli import session_spawn


# ---- Helpers ----------------------------------------------------------


def _make_jwt(payload: dict) -> str:
    """Construct an unsigned JWT (header.payload.signature) for testing."""
    def b64(d):
        return base64.urlsafe_b64encode(
            json.dumps(d).encode("utf-8")
        ).rstrip(b"=").decode("ascii")
    return f"{b64({'alg':'none'})}.{b64(payload)}.signature"


def _write_token(path: Path, *, sub="user-42"):
    path.parent.mkdir(parents=True, exist_ok=True)
    token = _make_jwt({"sub": sub, "iss": "https://x", "aud": "y"})
    path.write_text(
        json.dumps({"access_token": token, "expires_at": 9_999_999_999,
                    "issuer": "https://x", "client_id": "c"}),
        encoding="utf-8",
    )


def _write_runner_endpoint(path: Path, *, port=8091, token="RTOK"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"host=127.0.0.1\nport={port}\ntoken={token}\npid=999\n",
        encoding="utf-8",
    )


class _StubOpener:
    """Minimal urllib opener stub."""
    def __init__(self, *, response=None, raise_exc=None):
        self.response = response
        self.raise_exc = raise_exc
        self.calls = []
    def open(self, req, timeout=None):
        self.calls.append((req.full_url, req.data, dict(req.headers)))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


class _StubResponse:
    def __init__(self, body, status=200):
        self._body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.status = status
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


# ---- jwt_sub ----------------------------------------------------------


class TestJwtSub:
    def test_extracts_sub_claim(self):
        token = _make_jwt({"sub": "user-42", "iss": "https://x"})
        assert session_spawn.jwt_sub(token) == "user-42"

    def test_missing_sub_returns_none(self):
        token = _make_jwt({"iss": "https://x"})
        assert session_spawn.jwt_sub(token) is None

    def test_malformed_token_returns_none(self):
        assert session_spawn.jwt_sub("not.a.jwt-payload") is None
        assert session_spawn.jwt_sub("xxx") is None
        assert session_spawn.jwt_sub("") is None

    def test_empty_sub_returns_none(self):
        token = _make_jwt({"sub": ""})
        assert session_spawn.jwt_sub(token) is None


# ---- load_token + load_runner_endpoint --------------------------------


class TestLoadFiles:
    def test_load_token_returns_access_token(self, tmp_path):
        cache = tmp_path / "token.cache"
        _write_token(cache, sub="user-1")
        token = session_spawn.load_token(cache)
        assert token is not None
        assert session_spawn.jwt_sub(token) == "user-1"

    def test_load_token_missing_file_returns_none(self, tmp_path):
        assert session_spawn.load_token(tmp_path / "nope") is None

    def test_load_runner_endpoint_parses_fields(self, tmp_path):
        ep = tmp_path / "runner.endpoint"
        _write_runner_endpoint(ep, port=9090, token="abc")
        host, port, token = session_spawn.load_runner_endpoint(ep)
        assert (host, port, token) == ("127.0.0.1", 9090, "abc")

    def test_load_runner_endpoint_missing_returns_none(self, tmp_path):
        assert session_spawn.load_runner_endpoint(tmp_path / "nope") is None

    def test_load_runner_endpoint_incomplete_returns_none(self, tmp_path):
        ep = tmp_path / "runner.endpoint"
        ep.write_text("host=127.0.0.1\n", encoding="utf-8")  # no port, no token
        assert session_spawn.load_runner_endpoint(ep) is None


# ---- build_spawn_body -------------------------------------------------


class TestBuildBody:
    def _args(self, **overrides):
        import argparse
        ns = argparse.Namespace(
            agent="backend-agent", repo="auth-service",
            project_root="/tmp/proj", mode="interactive",
            harness="claude-code", account=None, worktree=None,
            initial_prompt=None, env=[],
        )
        for k, v in overrides.items():
            setattr(ns, k, v)
        return ns

    def test_minimal_body(self):
        body = session_spawn.build_spawn_body(self._args(), user_id="user-42")
        assert body["agent"] == "backend-agent"
        assert body["repo"] == "auth-service"
        assert body["mode"] == "interactive"
        assert body["harness"] == "claude-code"
        assert body["user"] == "user-42"
        # Optional fields not present
        assert "account" not in body
        assert "worktree" not in body
        assert "initial_prompt" not in body
        assert "env" not in body

    def test_all_optional_fields(self):
        body = session_spawn.build_spawn_body(
            self._args(
                account="dev", worktree="/tmp/wt",
                initial_prompt="hi", env=["A=1", "B=2"],
            ),
            user_id="user-42",
        )
        assert body["account"] == "dev"
        assert body["worktree"].endswith("wt")
        assert body["initial_prompt"] == "hi"
        assert body["env"] == {"A": "1", "B": "2"}

    def test_env_skips_malformed_entries(self):
        body = session_spawn.build_spawn_body(
            self._args(env=["A=1", "no-equals", "B=2"]),
            user_id="u",
        )
        assert body["env"] == {"A": "1", "B": "2"}


# ---- main() flow ------------------------------------------------------


class TestMain:
    def test_no_token_returns_1(self, tmp_path, capsys, monkeypatch):
        rc = session_spawn.main([
            "--agent", "a", "--repo", "r", "--project-root", "/tmp/p",
            "--token-cache", str(tmp_path / "missing"),
            "--runner-endpoint", str(tmp_path / "missing-runner"),
        ])
        assert rc == 1
        assert "otaman login" in capsys.readouterr().err

    def test_no_runner_returns_1(self, tmp_path, capsys):
        cache = tmp_path / "token.cache"
        _write_token(cache, sub="user-X")
        rc = session_spawn.main([
            "--agent", "a", "--repo", "r", "--project-root", "/tmp/p",
            "--token-cache", str(cache),
            "--runner-endpoint", str(tmp_path / "no-runner"),
        ])
        assert rc == 1
        assert "runner endpoint" in capsys.readouterr().err

    def test_happy_path_prints_session_id(self, tmp_path, capsys, monkeypatch):
        cache = tmp_path / "token.cache"
        _write_token(cache, sub="user-A")
        ep = tmp_path / "runner.endpoint"
        _write_runner_endpoint(ep, token="RTOK")

        opener = _StubOpener(response=_StubResponse(json.dumps({
            "session_id": "sess-xyz", "mode": "interactive", "pid": 1234,
            "attach": {"host": "127.0.0.1", "backend": "tmux", "session_name": "n"},
        })))
        # Monkeypatch the urlopen path so we can inject opener
        import otaman_cli.session_spawn as ss
        orig_post = ss.post_spawn
        def stub_post(*, host, port, token, body, opener=None, timeout=30.0):
            return orig_post(host=host, port=port, token=token, body=body,
                             opener=opener or globals()["_OPENER_FOR_TEST"], timeout=timeout)
        # Instead, patch post_spawn entirely:
        captured = {}
        def fake_post(*, host, port, token, body, opener=None, timeout=30.0):
            captured.update(body=body, host=host, port=port, token=token)
            return 200, {
                "session_id": "sess-xyz", "mode": "interactive", "pid": 1234,
                "attach": {"host": "127.0.0.1", "backend": "tmux", "session_name": "n"},
            }
        monkeypatch.setattr(ss, "post_spawn", fake_post)

        rc = session_spawn.main([
            "--agent", "backend-agent", "--repo", "auth-service",
            "--project-root", "/tmp/proj",
            "--token-cache", str(cache),
            "--runner-endpoint", str(ep),
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "sess-xyz" in out
        assert "user-A" in out
        # Verify the body the runner saw
        assert captured["body"]["user"] == "user-A"
        assert captured["body"]["agent"] == "backend-agent"
        assert captured["body"]["repo"] == "auth-service"
        assert captured["host"] == "127.0.0.1"
        assert captured["token"] == "RTOK"

    def test_output_json_emits_full_response(self, tmp_path, capsys, monkeypatch):
        cache = tmp_path / "token.cache"
        _write_token(cache, sub="user-A")
        ep = tmp_path / "runner.endpoint"
        _write_runner_endpoint(ep)

        import otaman_cli.session_spawn as ss
        monkeypatch.setattr(ss, "post_spawn", lambda **kw: (200, {
            "session_id": "S1", "mode": "interactive", "pid": 99,
            "attach": None,
        }))
        rc = session_spawn.main([
            "--agent", "a", "--repo", "r", "--project-root", "/tmp/p",
            "--token-cache", str(cache), "--runner-endpoint", str(ep),
            "--output-json",
        ])
        assert rc == 0
        resp = json.loads(capsys.readouterr().out)
        assert resp["session_id"] == "S1"

    def test_runner_error_returns_1(self, tmp_path, capsys, monkeypatch):
        cache = tmp_path / "token.cache"
        _write_token(cache, sub="user-A")
        ep = tmp_path / "runner.endpoint"
        _write_runner_endpoint(ep)

        import otaman_cli.session_spawn as ss
        monkeypatch.setattr(ss, "post_spawn", lambda **kw: (400, {"error": "bad repo"}))
        rc = session_spawn.main([
            "--agent", "a", "--repo", "r", "--project-root", "/tmp/p",
            "--token-cache", str(cache), "--runner-endpoint", str(ep),
        ])
        assert rc == 1
        assert "bad repo" in capsys.readouterr().err
