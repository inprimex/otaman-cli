"""Tests for `otaman watchdog` CLI subcommands.

Per runner-agent task assignment 2026-06-16, spec `20260611T140057` task 3.
Covers:

  call_watchdog → HTTP method per action (GET status; POST others),
    auth header, URL shape, payload parsing, 404/503/0-error pass-through.
  format_status → spec-defined 4-line table output.
  cmd_watchdog (dispatcher) → arg validation; --json mode; exit codes
    for 200 / 404 / 503 / 0 / other.
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from otaman_cli.watchdog import (
    call_watchdog,
    cmd_watchdog,
    format_status,
)


# ---------------------------------------------------------------- helpers
def _endpoint_file(
    tmp_path: Path, *, host: str = "127.0.0.1", port: int = 8444, token: str = "tok-xyz"
) -> Path:
    p = tmp_path / "runner.endpoint"
    p.write_text(f"host={host}\nport={port}\ntoken={token}\n", encoding="utf-8")
    return p


class _FakeResponse:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeOpener:
    """Captures the urllib request + returns a canned response."""

    def __init__(self, response: _FakeResponse | Exception):
        self.response = response
        self.last_request = None

    def open(self, req, timeout=None):
        self.last_request = req
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    err = urllib.error.HTTPError(
        url="http://x",
        code=code,
        msg="err",
        hdrs=None,
        fp=io.BytesIO(body),
    )
    return err


# ---------------------------------------------------------------- call_watchdog
class TestCallWatchdog:
    def test_status_uses_get(self, tmp_path: Path):
        ep = _endpoint_file(tmp_path)
        opener = _FakeOpener(
            _FakeResponse(
                200,
                b'{"enabled":true,"running":true,"paused":false,"poll_interval_s":30,"sessions_monitored":[],"last_action":null,"pending_escalations":0}',
            )
        )
        status, payload = call_watchdog("status", endpoint_path=ep, opener=opener)
        assert status == 200
        assert payload["enabled"] is True
        # Verify GET method + URL
        assert opener.last_request.get_method() == "GET"
        assert opener.last_request.full_url == "http://127.0.0.1:8444/watchdog/status"

    def test_start_uses_post(self, tmp_path: Path):
        ep = _endpoint_file(tmp_path)
        opener = _FakeOpener(_FakeResponse(200, b'{"running":true,"paused":false}'))
        status, _ = call_watchdog("start", endpoint_path=ep, opener=opener)
        assert status == 200
        assert opener.last_request.get_method() == "POST"
        assert opener.last_request.full_url.endswith("/watchdog/start")

    def test_pause_uses_post(self, tmp_path: Path):
        ep = _endpoint_file(tmp_path)
        opener = _FakeOpener(_FakeResponse(200, b'{"paused":true}'))
        status, _ = call_watchdog("pause", endpoint_path=ep, opener=opener)
        assert status == 200
        assert opener.last_request.get_method() == "POST"

    def test_resume_uses_post(self, tmp_path: Path):
        ep = _endpoint_file(tmp_path)
        opener = _FakeOpener(_FakeResponse(200, b'{"paused":false}'))
        status, _ = call_watchdog("resume", endpoint_path=ep, opener=opener)
        assert status == 200
        assert opener.last_request.get_method() == "POST"

    def test_authorization_header_sent(self, tmp_path: Path):
        ep = _endpoint_file(tmp_path, token="loopback-bearer-abc")
        opener = _FakeOpener(_FakeResponse(200, b"{}"))
        call_watchdog("status", endpoint_path=ep, opener=opener)
        auth = opener.last_request.get_header("Authorization")
        assert auth == "Bearer loopback-bearer-abc"

    def test_404_returns_structured_error(self, tmp_path: Path):
        ep = _endpoint_file(tmp_path)
        opener = _FakeOpener(_http_error(404, b'{"error":"watchdog not configured"}'))
        status, payload = call_watchdog("status", endpoint_path=ep, opener=opener)
        assert status == 404
        assert payload["error"] == "watchdog not configured"

    def test_503_returns_structured_error(self, tmp_path: Path):
        ep = _endpoint_file(tmp_path)
        opener = _FakeOpener(
            _http_error(503, b'{"error":"watchdog start failed (no ws loop available)"}')
        )
        status, payload = call_watchdog("start", endpoint_path=ep, opener=opener)
        assert status == 503
        assert "no ws loop" in payload["error"]

    def test_missing_endpoint_file_returns_zero_status(self, tmp_path: Path):
        status, payload = call_watchdog("status", endpoint_path=tmp_path / "missing")
        assert status == 0
        assert "missing" in payload["error"]

    def test_malformed_endpoint_file_returns_zero(self, tmp_path: Path):
        p = tmp_path / "bad"
        p.write_text("garbage\n", encoding="utf-8")
        status, payload = call_watchdog("status", endpoint_path=p)
        assert status == 0
        assert "malformed" in payload["error"]

    def test_runner_unreachable_returns_zero(self, tmp_path: Path):
        ep = _endpoint_file(tmp_path)
        opener = _FakeOpener(urllib.error.URLError("Connection refused"))
        status, payload = call_watchdog("status", endpoint_path=ep, opener=opener)
        assert status == 0
        assert "unreachable" in payload["error"]

    def test_unknown_action_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError):
            call_watchdog("dance", endpoint_path=tmp_path / "x")

    def test_non_loopback_http_endpoint_refused(self, tmp_path: Path, monkeypatch):
        """F032 Part B — same bearer-token-over-plaintext-HTTP guard as
        session_spawn.post_spawn; this hits the same endpoint file/token."""
        monkeypatch.delenv("OTAMAN_ALLOW_INSECURE_RUNNER", raising=False)
        ep = _endpoint_file(tmp_path, host="10.0.0.5")
        status, payload = call_watchdog("status", endpoint_path=ep)
        assert status == 0
        assert "plaintext" in payload["error"]

    def test_https_scheme_endpoint_allowed(self, tmp_path: Path):
        ep = tmp_path / "runner.endpoint"
        ep.write_text("host=10.0.0.5\nport=8444\ntoken=tok-xyz\nscheme=https\n", encoding="utf-8")
        opener = _FakeOpener(_FakeResponse(200, json.dumps({"state": "active"}).encode()))
        status, payload = call_watchdog("status", endpoint_path=ep, opener=opener)
        assert status == 200


# ---------------------------------------------------------------- format_status
class TestFormatStatus:
    def test_full_payload_renders_4_lines(self):
        out = format_status(
            {
                "enabled": True,
                "running": True,
                "paused": False,
                "poll_interval_s": 30,
                "sessions_monitored": ["sess-a1", "sess-b2"],
                "last_action": {
                    "session_id": "sess-a1",
                    "pattern_id": "pager-prompt",
                    "action": "respond",
                    "timestamp": "2026-06-16T21:30:01+00:00",
                },
                "pending_escalations": 0,
            }
        )
        lines = out.splitlines()
        assert len(lines) == 4
        assert lines[0].startswith("Watchdog:  running")
        assert "poll: 30s" in lines[0]
        assert lines[1] == "Sessions:  2 monitored"
        assert "pager-prompt" in lines[2]
        assert "respond" in lines[2]
        assert "21:30:01" in lines[2]
        assert lines[3] == "Escalated: 0 pending"

    def test_paused_state_in_output(self):
        out = format_status(
            {
                "enabled": True,
                "running": True,
                "paused": True,
                "poll_interval_s": 30,
                "sessions_monitored": [],
                "last_action": None,
                "pending_escalations": 0,
            }
        )
        assert "paused" in out.splitlines()[0]

    def test_disabled_state_in_output(self):
        out = format_status(
            {
                "enabled": False,
                "running": False,
                "paused": False,
                "poll_interval_s": 30,
                "sessions_monitored": [],
                "last_action": None,
                "pending_escalations": 0,
            }
        )
        assert "disabled" in out.splitlines()[0]

    def test_no_last_action_says_none_yet(self):
        out = format_status(
            {
                "enabled": True,
                "running": True,
                "paused": False,
                "poll_interval_s": 60,
                "sessions_monitored": ["x"],
                "last_action": None,
                "pending_escalations": 0,
            }
        )
        assert "(none yet)" in out

    def test_agent_name_override_in_last_action(self):
        """Caller cross-referenced session_id → agent_name."""
        out = format_status(
            {
                "enabled": True,
                "running": True,
                "paused": False,
                "poll_interval_s": 30,
                "sessions_monitored": ["sess-a1"],
                "last_action": {
                    "session_id": "sess-a1",
                    "pattern_id": "pager",
                    "action": "send",
                    "timestamp": "2026-06-16T21:30:01+00:00",
                },
                "pending_escalations": 1,
            },
            agent_name="frontend-agent",
        )
        last_act_line = out.splitlines()[2]
        assert "frontend-agent" in last_act_line
        assert "sess-a1" not in last_act_line  # the override replaces the id

    def test_pending_escalations_count(self):
        out = format_status(
            {
                "enabled": True,
                "running": True,
                "paused": False,
                "poll_interval_s": 30,
                "sessions_monitored": [],
                "last_action": None,
                "pending_escalations": 5,
            }
        )
        assert "Escalated: 5 pending" in out


# ---------------------------------------------------------------- cmd_watchdog
class TestCmdWatchdog:
    def test_no_args_prints_usage(self, capsys):
        rc = cmd_watchdog([])
        assert rc == 2
        assert "Usage" in capsys.readouterr().out

    def test_unknown_action_rejected(self, capsys):
        rc = cmd_watchdog(["dance"])
        assert rc == 2
        out = capsys.readouterr().out
        assert "Unknown" in out

    def test_status_exit_0_on_success(self, capsys, monkeypatch, tmp_path: Path):
        ep = _endpoint_file(tmp_path)
        monkeypatch.setattr(
            "otaman_cli.watchdog.DEFAULT_RUNNER_ENDPOINT",
            ep,
        )
        opener = _FakeOpener(
            _FakeResponse(
                200,
                b'{"enabled":true,"running":true,"paused":false,"poll_interval_s":30,"sessions_monitored":[],"last_action":null,"pending_escalations":0}',
            )
        )
        monkeypatch.setattr(
            "urllib.request.build_opener",
            lambda *a, **kw: opener,
        )
        rc = cmd_watchdog(["status"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Watchdog:" in out
        assert "running" in out

    def test_404_exits_2_with_hint(self, capsys, monkeypatch, tmp_path: Path):
        ep = _endpoint_file(tmp_path)
        monkeypatch.setattr("otaman_cli.watchdog.DEFAULT_RUNNER_ENDPOINT", ep)
        opener = _FakeOpener(_http_error(404, b'{"error":"watchdog not configured"}'))
        monkeypatch.setattr("urllib.request.build_opener", lambda *a, **kw: opener)
        rc = cmd_watchdog(["status"])
        assert rc == 2
        out = capsys.readouterr().out
        assert "not configured" in out
        assert "Hint" in out

    def test_503_exits_2_with_hint(self, capsys, monkeypatch, tmp_path: Path):
        ep = _endpoint_file(tmp_path)
        monkeypatch.setattr("otaman_cli.watchdog.DEFAULT_RUNNER_ENDPOINT", ep)
        opener = _FakeOpener(
            _http_error(503, b'{"error":"watchdog start failed (no ws loop available)"}')
        )
        monkeypatch.setattr("urllib.request.build_opener", lambda *a, **kw: opener)
        rc = cmd_watchdog(["start"])
        assert rc == 2
        out = capsys.readouterr().out
        assert "start failed" in out
        assert "Hint" in out

    def test_runner_unreachable_exits_1(self, capsys, monkeypatch, tmp_path: Path):
        ep = _endpoint_file(tmp_path)
        monkeypatch.setattr("otaman_cli.watchdog.DEFAULT_RUNNER_ENDPOINT", ep)
        opener = _FakeOpener(urllib.error.URLError("refused"))
        monkeypatch.setattr("urllib.request.build_opener", lambda *a, **kw: opener)
        rc = cmd_watchdog(["status"])
        assert rc == 1
        assert "ERROR" in capsys.readouterr().out

    def test_endpoint_file_missing_exits_1(self, capsys, monkeypatch, tmp_path: Path):
        # Point default at a path that doesn't exist
        monkeypatch.setattr("otaman_cli.watchdog.DEFAULT_RUNNER_ENDPOINT", tmp_path / "missing")
        rc = cmd_watchdog(["status"])
        assert rc == 1
        assert "ERROR" in capsys.readouterr().out

    def test_json_mode_prints_structured(self, capsys, monkeypatch, tmp_path: Path):
        ep = _endpoint_file(tmp_path)
        monkeypatch.setattr("otaman_cli.watchdog.DEFAULT_RUNNER_ENDPOINT", ep)
        opener = _FakeOpener(
            _FakeResponse(
                200,
                b'{"enabled":true,"running":true,"paused":false,"poll_interval_s":30,"sessions_monitored":[],"last_action":null,"pending_escalations":0}',
            )
        )
        monkeypatch.setattr("urllib.request.build_opener", lambda *a, **kw: opener)
        rc = cmd_watchdog(["status", "--json"])
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out.strip())
        assert data["http_status"] == 200
        assert data["payload"]["enabled"] is True

    def test_start_pause_resume_dispatch(self, monkeypatch, tmp_path: Path):
        ep = _endpoint_file(tmp_path)
        monkeypatch.setattr("otaman_cli.watchdog.DEFAULT_RUNNER_ENDPOINT", ep)
        for action in ("start", "pause", "resume"):
            opener = _FakeOpener(
                _FakeResponse(
                    200,
                    b'{"enabled":true,"running":true,"paused":false,"poll_interval_s":30,"sessions_monitored":[],"last_action":null,"pending_escalations":0}',
                )
            )
            monkeypatch.setattr(
                "urllib.request.build_opener", lambda *a, opener=opener, **kw: opener
            )
            rc = cmd_watchdog([action])
            assert rc == 0, f"action={action} failed"
            # Verify the URL the action hit
            assert opener.last_request.full_url.endswith(f"/watchdog/{action}")


# ---------------------------------------------------------------- main dispatcher
class TestMainDispatcher:
    """Smoke that `otaman watchdog <action>` reaches the new command via the main argv loop."""

    def test_main_dispatcher_routes_watchdog(self, monkeypatch, tmp_path: Path):
        """`python -m otaman_cli.main watchdog status` reaches cmd_watchdog."""
        import os
        import subprocess
        import sys

        _endpoint_file(tmp_path)
        env = {
            **os.environ,
            "OTAMAN_AGENT": "cli-agent",
            "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
            "NO_COLOR": "1",
        }
        # No runner running → expect exit 1 with friendly error.  The
        # critical assertion is that "Unknown command" does NOT appear —
        # i.e. the dispatcher accepted `watchdog` as a known surface.
        r = subprocess.run(
            [sys.executable, "-m", "otaman_cli.main", "watchdog", "status"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert "Unknown command" not in r.stdout
        assert "Unknown command" not in r.stderr
