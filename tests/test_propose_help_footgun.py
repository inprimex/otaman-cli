"""`otaman propose --help` (and `team --help`) must NOT be parsed as content.

Post-mortem (Roman, 2026-08-31): `otaman propose --help` parsed `--help` as the
positional title and filed a real spec-change-request + blocked entry titled
"--help". `propose`/`team` turn their positional into content with no required
flag to gate them, so `-h`/`--help` must win over positional parsing and
short-circuit to usage with NO side effect.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = str(Path(__file__).parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from otaman_cli.commands.propose_team import (  # noqa: E402
    _help_requested,
    cmd_propose,
    cmd_team,
)


def _project(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / ".agents" / "bus" / "active").mkdir(parents=True)
    (tmp_path / ".agents" / "current-agent").write_text("cli-agent", encoding="utf-8")
    (tmp_path / "platform.yaml").write_text(
        "project: tst\nversion: '1.0'\nedition: ce\nmode: 1\n"
        "repos:\n  - {name: tst, path: ., owner: cli-agent}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OTAMAN_AGENT", "cli-agent")
    monkeypatch.delenv("OTAMAN_ROOT", raising=False)
    monkeypatch.delenv("MAESTRO_ROOT", raising=False)
    return tmp_path


def _bus_files(root: Path) -> list[Path]:
    return list((root / ".agents" / "bus" / "active").glob("*.md"))


def _blocked_files(root: Path) -> list[Path]:
    d = root / ".agents" / "blocked"
    return list(d.glob("*.md")) if d.exists() else []


def test_help_requested_unit():
    assert _help_requested(["--help"])
    assert _help_requested(["-h"])
    assert _help_requested(["real", "title", "--help"])
    assert not _help_requested(["real", "title"])
    assert not _help_requested([])


def test_propose_help_writes_nothing(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch)
    rc = cmd_propose(["--help"])
    assert rc == 0
    assert _bus_files(root) == [], "propose --help must NOT file a spec-change-request"
    assert _blocked_files(root) == [], "propose --help must NOT write a blocked entry"
    assert "Usage: otaman propose" in capsys.readouterr().out


def test_propose_dash_h_writes_nothing(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    rc = cmd_propose(["-h"])
    assert rc == 0
    assert _bus_files(root) == []
    assert _blocked_files(root) == []


def test_team_help_no_side_effect(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch)
    rc = cmd_team(["--help"])
    assert rc == 0
    assert _bus_files(root) == []
    assert "Usage: otaman team" in capsys.readouterr().out


def test_propose_real_title_still_files_request(tmp_path, monkeypatch):
    # regression: the normal path must still write exactly one request
    root = _project(tmp_path, monkeypatch)
    rc = cmd_propose(["Add", "user", "pagination"])
    assert rc == 0
    files = _bus_files(root)
    assert len(files) == 1 and files[0].name.endswith("spec-change-request.md")
    assert "--help" not in files[0].read_text(encoding="utf-8")
