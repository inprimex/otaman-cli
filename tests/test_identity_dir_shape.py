"""Tests for directory-shape .otaman handling (fix-otaman-meta-directory-collision, tasks 1.1, 1.3).

Covers:
a) Walk past file-.otaman without agent: (existing behavior preserved)
b) Walk past directory-.otaman without agent file
c) Resolve from directory-.otaman with agent file containing 'human'
d) .otaman/agent with empty / whitespace-only content treated as missing
e) otaman init --update writes .otaman/agent to directory-shape target without touching siblings
"""

from __future__ import annotations

import json
from pathlib import Path

from otaman_cli.identity import _read_otaman_agent_field

# ---------------------------------------------------------------------------
# (a) File-shape without agent: — walk continues (existing behavior preserved)


def test_file_without_agent_keeps_walking(tmp_path: Path) -> None:
    child = tmp_path / "child"
    child.mkdir()
    (child / ".otaman").write_text("../meta\n", encoding="utf-8")  # no agent: field
    (tmp_path / ".otaman").write_text("agent: parent-agent\n", encoding="utf-8")
    assert _read_otaman_agent_field(child) == "parent-agent"


# ---------------------------------------------------------------------------
# (b) Directory-shape without agent file — walk continues


def test_dir_without_agent_file_keeps_walking(tmp_path: Path) -> None:
    child = tmp_path / "child"
    child.mkdir()
    dotoman_dir = child / ".otaman"
    dotoman_dir.mkdir()
    # No agent file inside
    (dotoman_dir / "last-user-activity").write_text("2026-05-28\n")  # sibling runtime file
    (tmp_path / ".otaman").write_text("agent: parent-agent\n", encoding="utf-8")
    assert _read_otaman_agent_field(child) == "parent-agent"


# ---------------------------------------------------------------------------
# (c) Directory-shape with agent file resolves correctly


def test_dir_with_agent_file_resolves(tmp_path: Path) -> None:
    dotoman_dir = tmp_path / ".otaman"
    dotoman_dir.mkdir()
    (dotoman_dir / "agent").write_text("human\n", encoding="utf-8")
    assert _read_otaman_agent_field(tmp_path) == "human"


def test_dir_agent_file_takes_first_line(tmp_path: Path) -> None:
    dotoman_dir = tmp_path / ".otaman"
    dotoman_dir.mkdir()
    (dotoman_dir / "agent").write_text("human\nextra-line\n", encoding="utf-8")
    assert _read_otaman_agent_field(tmp_path) == "human"


def test_nested_cwd_resolves_dir_shape_agent(tmp_path: Path) -> None:
    """CWD deep inside a dir-shape repo still resolves."""
    dotoman_dir = tmp_path / ".otaman"
    dotoman_dir.mkdir()
    (dotoman_dir / "agent").write_text("human\n", encoding="utf-8")
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    assert _read_otaman_agent_field(nested) == "human"


# ---------------------------------------------------------------------------
# (d) .otaman/agent with empty / whitespace-only content — treated as missing


def test_dir_agent_file_empty_keeps_walking(tmp_path: Path) -> None:
    child = tmp_path / "child"
    child.mkdir()
    dotoman_dir = child / ".otaman"
    dotoman_dir.mkdir()
    (dotoman_dir / "agent").write_text("", encoding="utf-8")  # empty
    (tmp_path / ".otaman").write_text("agent: root-agent\n", encoding="utf-8")
    assert _read_otaman_agent_field(child) == "root-agent"


def test_dir_agent_file_whitespace_keeps_walking(tmp_path: Path) -> None:
    child = tmp_path / "child"
    child.mkdir()
    dotoman_dir = child / ".otaman"
    dotoman_dir.mkdir()
    (dotoman_dir / "agent").write_text("   \n\n  \n", encoding="utf-8")  # whitespace only
    (tmp_path / ".otaman").write_text("agent: root-agent\n", encoding="utf-8")
    assert _read_otaman_agent_field(child) == "root-agent"


# ---------------------------------------------------------------------------
# (e) otaman init --update writes .otaman/agent without touching siblings


def _make_dir_shape_project(tmp_path: Path, agent_name: str = "human") -> Path:
    """Create a minimal project where meta has dir-shape .otaman."""
    parent = tmp_path / "platform"
    parent.mkdir()
    meta = parent / "platform-meta"
    meta.mkdir()
    (meta / ".agents").mkdir()
    (meta / "platform.yaml").write_text(
        "project: platform\nrepos:\n"
        "  - name: svc-api\n    path: ../svc-api\n    owner: api-agent\n",
        encoding="utf-8",
    )
    # Meta has a directory-shape .otaman with runtime files
    dotoman_dir = meta / ".otaman"
    dotoman_dir.mkdir()
    (dotoman_dir / "last-user-activity").write_text("2026-05-28T12:00:00Z\n")
    (dotoman_dir / "sessions").mkdir()

    # Repo has a file-shape .otaman
    repo = parent / "svc-api"
    repo.mkdir()
    (repo / ".otaman").write_text("../platform-meta\n", encoding="utf-8")

    return meta


def _cli_env() -> dict:
    """Explicit subprocess env: strip the isolate_bus sandbox pin so the CLI
    resolves this test's fixture tree; propagate sys.path for sibling
    checkouts."""
    import os
    import sys

    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    for _var in ("OTAMAN_ROOT", "MAESTRO_ROOT"):
        env.pop(_var, None)
    return env


def test_init_update_writes_dir_shape_agent_file(tmp_path: Path) -> None:
    """--update must create .otaman/agent for dir-shape meta target."""
    import subprocess
    import sys

    meta = _make_dir_shape_project(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", "init", "--update"],
        capture_output=True,
        text=True,
        cwd=str(meta),
        env=_cli_env(),
    )

    agent_file = meta / ".otaman" / "agent"
    assert agent_file.is_file(), f"Expected .otaman/agent to be created; stdout={result.stdout}"
    assert agent_file.read_text(encoding="utf-8").strip() == "human"


def test_init_update_dir_shape_leaves_siblings_untouched(tmp_path: Path) -> None:
    """Existing runtime files inside .otaman/ must not be modified."""
    import subprocess
    import sys

    meta = _make_dir_shape_project(tmp_path)
    activity_file = meta / ".otaman" / "last-user-activity"
    original_content = activity_file.read_text(encoding="utf-8")

    subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", "init", "--update"],
        capture_output=True,
        text=True,
        cwd=str(meta),
        env=_cli_env(),
    )

    assert activity_file.read_text(encoding="utf-8") == original_content


def test_init_update_dir_shape_idempotent(tmp_path: Path) -> None:
    """Running --update twice on dir-shape meta produces same result, no error."""
    import subprocess
    import sys

    meta = _make_dir_shape_project(tmp_path)

    for _ in range(2):
        r = subprocess.run(
            [sys.executable, "-m", "otaman_cli.main", "init", "--update"],
            capture_output=True,
            text=True,
            cwd=str(meta),
            env=_cli_env(),
        )
        assert r.returncode == 0

    agent_file = meta / ".otaman" / "agent"
    assert agent_file.read_text(encoding="utf-8").strip() == "human"


# ---------------------------------------------------------------------------
# defaultMode: auto in settings.local.json


def _make_settings_project(tmp_path: Path) -> tuple[Path, Path]:
    """Returns (root/meta dir, settings.local.json path) for a simple project layout."""
    parent = tmp_path / "platform"
    parent.mkdir()
    meta = parent / "platform-meta"
    meta.mkdir()
    repo = parent / "svc"
    repo.mkdir()
    claude_dir = repo / ".claude"
    claude_dir.mkdir()
    settings_path = claude_dir / "settings.local.json"
    return meta, settings_path


def test_ensure_settings_default_mode_writes_field(tmp_path: Path) -> None:
    """_ensure_settings_default_mode adds defaultMode:auto to existing settings.local.json."""
    from otaman_cli.commands.init import _ensure_settings_default_mode

    meta, settings_path = _make_settings_project(tmp_path)
    settings_path.write_text(
        json.dumps({"permissions": {"allow": ["Bash(git:*)"]}}), encoding="utf-8"
    )

    config = {"repos": [{"path": "../svc", "owner": "svc-agent"}]}
    _ensure_settings_default_mode(meta, config)

    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert data["permissions"]["defaultMode"] == "auto"
    assert "Bash(git:*)" in data["permissions"]["allow"]  # existing entry preserved


def test_ensure_settings_default_mode_idempotent(tmp_path: Path) -> None:
    from otaman_cli.commands.init import _ensure_settings_default_mode

    meta, settings_path = _make_settings_project(tmp_path)
    settings_path.write_text(
        json.dumps({"permissions": {"defaultMode": "auto", "allow": []}}), encoding="utf-8"
    )
    mtime_before = settings_path.stat().st_mtime

    config = {"repos": [{"path": "../svc", "owner": "svc-agent"}]}
    _ensure_settings_default_mode(meta, config)

    # File should not have been rewritten (defaultMode already correct)
    assert settings_path.stat().st_mtime == mtime_before
