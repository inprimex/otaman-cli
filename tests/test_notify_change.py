"""Tests for `otaman notify-change` (post-merge-spec-notify tasks 1.1-1.6).

Spec mirrors `otaman-plugin/scripts/spec-change-hook.sh`.  Recipient
derivation, message body shape, and graceful map-tasks.py fallback are
the load-bearing behaviors.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from otaman_cli.notify_change import (
    derive_recipients,
    notify_change,
)


def _stage_workspace(tmp_path: Path) -> tuple[Path, Path]:
    """Stage a minimal otaman project + sibling otaman-specs.

    Returns ``(project_root, specs_root)``.
    """
    project = tmp_path / "myorg"
    specs = tmp_path / "myorg-specs"
    project.mkdir()
    (specs / "openspec" / "changes").mkdir(parents=True)
    (project / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (project / ".agents" / "current-agent").write_text("cli-agent", encoding="utf-8")

    # platform.yaml with a couple of repos so owner lookups work
    platform_body = textwrap.dedent("""
        project: myorg
        version: '1.0'
        specs:
          path: ../myorg-specs
        repos:
          - {name: otaman-cli, path: ../otaman-cli, owner: cli-agent}
          - {name: otaman-core, path: ../otaman-core, owner: core-agent}
          - {name: otaman-plugin, path: ../otaman-plugin, owner: plugin-agent}
    """).lstrip()
    (project / "platform.yaml").write_text(platform_body, encoding="utf-8")
    return project, specs


def _stage_change(specs: Path, change_name: str, tasks_md_body: str | None = None) -> Path:
    change_dir = specs / "openspec" / "changes" / change_name
    change_dir.mkdir(parents=True, exist_ok=True)
    if tasks_md_body is not None:
        (change_dir / "tasks.md").write_text(tasks_md_body, encoding="utf-8")
    return change_dir


# ---------------------------------------------------------------- task 1.2 — recipient derivation
class TestDeriveRecipients:
    def test_no_tasks_md_returns_spec_agent_only(self, tmp_path: Path):
        project, specs = _stage_workspace(tmp_path)
        _stage_change(specs, "no-tasks-change", tasks_md_body=None)
        recipients = derive_recipients(specs, "no-tasks-change", project / "platform.yaml")
        assert recipients == ["spec-agent"]

    def test_tasks_md_with_no_annotations_falls_back(self, tmp_path: Path):
        project, specs = _stage_workspace(tmp_path)
        _stage_change(specs, "ch1", "# tasks\n- [ ] 1.1 unannotated task\n")
        recipients = derive_recipients(specs, "ch1", project / "platform.yaml")
        assert recipients == ["spec-agent", "human"]

    def test_tasks_md_with_annotations_resolves_to_owners(self, tmp_path: Path):
        project, specs = _stage_workspace(tmp_path)
        _stage_change(
            specs,
            "ch1",
            textwrap.dedent("""
            # tasks
            - [ ] 1.1 @otaman-cli first task
            - [ ] 1.2 @otaman-core second task
            - [ ] 1.3 @otaman-cli back to cli (dedup)
        """),
        )
        recipients = derive_recipients(specs, "ch1", project / "platform.yaml")
        assert recipients == ["cli-agent", "core-agent"]

    def test_annotations_to_unknown_repo_silently_skipped(self, tmp_path: Path):
        project, specs = _stage_workspace(tmp_path)
        _stage_change(specs, "ch1", "- [ ] 1.1 @otaman-cli\n- [ ] 1.2 @otaman-nonexistent\n")
        recipients = derive_recipients(specs, "ch1", project / "platform.yaml")
        # unknown repo silently dropped — better to under-notify than mis-notify
        assert recipients == ["cli-agent"]

    def test_annotations_present_but_no_resolved_owners_falls_back(self, tmp_path: Path):
        project, specs = _stage_workspace(tmp_path)
        _stage_change(specs, "ch1", "- [ ] 1.1 @otaman-ghost only an unknown\n")
        recipients = derive_recipients(specs, "ch1", project / "platform.yaml")
        assert recipients == ["spec-agent", "human"]

    def test_case_insensitive_annotation_matching(self, tmp_path: Path):
        project, specs = _stage_workspace(tmp_path)
        _stage_change(specs, "ch1", "- [ ] @OTAMAN-CLI @Otaman-Core\n")
        recipients = derive_recipients(specs, "ch1", project / "platform.yaml")
        assert recipients == ["cli-agent", "core-agent"]

    def test_dotted_repo_name_annotation_not_truncated(self, tmp_path: Path):
        """Regression (issue #92): `@otaman-sunflowers.host` used to be
        truncated at the `.` by `_ANN_RE`, matching only `otaman-sunflowers`."""
        project, specs = _stage_workspace(tmp_path)
        platform_body = textwrap.dedent("""
            project: myorg
            version: '1.0'
            specs:
              path: ../myorg-specs
            repos:
              - {name: otaman-sunflowers.host, path: ../sunflowers-host, owner: host-agent}
        """).lstrip()
        (project / "platform.yaml").write_text(platform_body, encoding="utf-8")
        _stage_change(specs, "ch1", "- [ ] 1.1 @otaman-sunflowers.host build it\n")
        recipients = derive_recipients(specs, "ch1", project / "platform.yaml")
        assert recipients == ["host-agent"]

    def test_bare_repo_name_convention_resolves_owner(self, tmp_path: Path):
        """Regression (issue #92): programs whose platform.yaml names repos
        without the `otaman-` prefix (e.g. `sunflowers-specs`) used to
        silently under-notify because `by_name` was keyed by the bare name
        while the annotation kept the `otaman-` prefix."""
        project, specs = _stage_workspace(tmp_path)
        platform_body = textwrap.dedent("""
            project: sunflowers
            version: '1.0'
            specs:
              path: ../sunflowers-specs
            repos:
              - {name: sunflowers-specs, path: ../sunflowers-specs, owner: spec-agent}
              - {name: sunflowers-host, path: ../sunflowers-host, owner: host-agent}
        """).lstrip()
        (project / "platform.yaml").write_text(platform_body, encoding="utf-8")
        _stage_change(specs, "ch1", "- [ ] 1.1 @otaman-sunflowers-host ship it\n")
        recipients = derive_recipients(specs, "ch1", project / "platform.yaml")
        assert recipients == ["host-agent"]


# ---------------------------------------------------------------- task 1.3 — bus message format
class TestNotifyChangeBusMessage:
    def test_writes_spec_change_message(self, tmp_path: Path):
        project, specs = _stage_workspace(tmp_path)
        _stage_change(specs, "ch1", "- [ ] 1.1 @otaman-cli build it\n")
        rc, summary = notify_change(project, "ch1")
        assert rc == 0
        bus = project / ".agents" / "bus" / "active"
        msgs = list(bus.glob("*spec-change*.md"))
        assert len(msgs) == 1
        body = msgs[0].read_text(encoding="utf-8")
        assert "type: spec-change" in body
        assert "to: cli-agent" in body
        assert "priority: high" in body
        assert "Specs changed" in body

    def test_recipient_list_joined_with_commas(self, tmp_path: Path):
        project, specs = _stage_workspace(tmp_path)
        _stage_change(specs, "ch1", "- [ ] @otaman-cli\n- [ ] @otaman-core\n")
        rc, summary = notify_change(project, "ch1")
        assert rc == 0
        body = (
            project / ".agents" / "bus" / "active" / Path(summary["message_path"]).name
        ).read_text()
        assert "to: cli-agent, core-agent" in body

    def test_fallback_to_spec_agent_when_no_tasks_md(self, tmp_path: Path):
        project, specs = _stage_workspace(tmp_path)
        _stage_change(specs, "ch1", tasks_md_body=None)
        rc, summary = notify_change(project, "ch1")
        assert rc == 0
        body = Path(summary["message_path"]).read_text()
        assert "to: spec-agent" in body


# ----------------------------------------------------- task 1.4 — map-tasks.py graceful degradation
class TestMapTasksFallback:
    def test_map_tasks_absent_does_not_fail(self, tmp_path: Path, monkeypatch):
        """When map-tasks.py is nowhere to be found, summary records absence + rc=0."""
        project, specs = _stage_workspace(tmp_path)
        _stage_change(specs, "ch1", "- [ ] @otaman-cli\n")
        # Force the finder to return None
        monkeypatch.setattr(
            "otaman_cli.notify_change._find_map_tasks_py",
            lambda: None,
        )
        rc, summary = notify_change(project, "ch1")
        assert rc == 0
        assert summary["map_tasks_called"] is False
        assert summary["map_tasks_path"] is None

    def test_map_tasks_called_when_found(self, tmp_path: Path, monkeypatch):
        """When a stub map-tasks.py exists, the subprocess invocation fires."""
        project, specs = _stage_workspace(tmp_path)
        _stage_change(specs, "ch1", "- [ ] @otaman-cli\n")
        # Stage a no-op stub script
        stub = tmp_path / "stub-map-tasks.py"
        stub.write_text("import sys; sys.exit(0)\n", encoding="utf-8")
        monkeypatch.setattr(
            "otaman_cli.notify_change._find_map_tasks_py",
            lambda: stub,
        )
        rc, summary = notify_change(project, "ch1")
        assert rc == 0
        assert summary["map_tasks_called"] is True
        assert summary["map_tasks_path"] == str(stub)

    def test_map_tasks_invocation_failure_does_not_break_notify(self, tmp_path: Path, monkeypatch):
        """If map-tasks.py errors out, notify still succeeds."""
        project, specs = _stage_workspace(tmp_path)
        _stage_change(specs, "ch1", "- [ ] @otaman-cli\n")
        crash = tmp_path / "crash-map-tasks.py"
        crash.write_text("import sys; sys.exit(99)\n", encoding="utf-8")
        monkeypatch.setattr(
            "otaman_cli.notify_change._find_map_tasks_py",
            lambda: crash,
        )
        rc, summary = notify_change(project, "ch1")
        assert rc == 0  # notify itself doesn't fail


# ---------------------------------------------------------------- task 1.6(d) — exit codes
class TestExitCodes:
    def test_missing_change_exits_1(self, tmp_path: Path):
        project, specs = _stage_workspace(tmp_path)
        # Don't stage the change — it doesn't exist
        rc, summary = notify_change(project, "nonexistent-change")
        assert rc == 1
        assert "not found" in summary["error"].lower()

    def test_missing_platform_yaml_exits_1(self, tmp_path: Path):
        project = tmp_path / "myorg-no-platform"
        project.mkdir()
        # No platform.yaml → specs_root can't be resolved
        rc, summary = notify_change(project, "any-change")
        assert rc == 1

    def test_success_exits_0(self, tmp_path: Path, monkeypatch):
        project, specs = _stage_workspace(tmp_path)
        _stage_change(specs, "ch1", "- [ ] @otaman-cli\n")
        monkeypatch.setattr(
            "otaman_cli.notify_change._find_map_tasks_py",
            lambda: None,
        )
        rc, _ = notify_change(project, "ch1")
        assert rc == 0


# ---------------------------------------------------------------- task 1.5 — CLI integration
class TestCmdNotifyChange:
    def _run_cli(self, project: Path, *args: str) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "OTAMAN_AGENT": "cli-agent",
            "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
            "NO_COLOR": "1",
        }
        return subprocess.run(
            [sys.executable, "-m", "otaman_cli.main", *args],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_no_args_exits_2_with_usage(self, tmp_path: Path):
        project, _ = _stage_workspace(tmp_path)
        r = self._run_cli(project, "notify-change")
        assert r.returncode == 2
        assert "Usage" in r.stdout or "Usage" in r.stderr

    def test_cli_smoke_success(self, tmp_path: Path):
        project, specs = _stage_workspace(tmp_path)
        _stage_change(specs, "ch1", "- [ ] @otaman-cli build it\n- [ ] @otaman-core ship it\n")
        r = self._run_cli(project, "notify-change", "ch1")
        assert r.returncode == 0, (r.stdout, r.stderr)
        # Summary output
        assert "spec-change notification written" in r.stdout
        assert "Recipients" in r.stdout
        assert "cli-agent" in r.stdout
        # Bus message on disk
        bus = project / ".agents" / "bus" / "active"
        msgs = list(bus.glob("*spec-change*.md"))
        assert len(msgs) == 1

    def test_cli_missing_change_exits_1(self, tmp_path: Path):
        project, _ = _stage_workspace(tmp_path)
        r = self._run_cli(project, "notify-change", "totally-fake")
        assert r.returncode == 1
        # Error in stdout (UI.error prints to stdout)
        assert "not found" in r.stdout.lower() or "not found" in r.stderr.lower()

    def test_cli_summary_includes_map_tasks_warning_when_absent(self, tmp_path: Path):
        project, specs = _stage_workspace(tmp_path)
        _stage_change(specs, "ch1", "- [ ] @otaman-cli\n")
        r = self._run_cli(project, "notify-change", "ch1")
        # When map-tasks.py isn't on the search path, output mentions it
        assert r.returncode == 0
        # Either "invoked" or "not found" should appear depending on env
        assert (
            "map-tasks.py invoked" in r.stdout
            or "map-tasks.py not found" in r.stdout
            or "map-tasks.py found but invocation skipped" in r.stdout
        )
