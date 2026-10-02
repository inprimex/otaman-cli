"""`otaman init` prints WHICH program it is about to write into, before writing.

`destructive-command-safety` requires a DESTRUCTIVE-CROSS-DIRECTORY command to print
its fully-resolved target and, for a project root, an identity check — the resolved
`platform.yaml` `project:` name and repo count — before any mutation. `otaman init`
resolves a root and then writes `.agents/`, `launcher/`, `policy/` and a `.otaman`
marker into every declared repo, and it printed none of that.

Both init incidents of 2026-10-02 were operators who did not know which program they
had just initialised. The echo does not prevent the write; it makes the wrong target
visible while the mistake is still one keystroke old. So what is pinned here is the
CONTENT (path, project, repo count, the per-repo targets) and the ORDER (before the
first write), because an echo after the fact is decoration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_cli.commands import init as INIT


@pytest.fixture
def program(tmp_path: Path) -> Path:
    meta = tmp_path / "acme-otaman"
    meta.mkdir()
    (tmp_path / "svc-api").mkdir()
    (tmp_path / "svc-web").mkdir()
    (meta / "platform.yaml").write_text(
        "project: acme\nversion: '1.0'\nrepos:\n"
        "  - name: svc-api\n    path: ../svc-api\n    owner: backend-agent\n"
        "  - name: svc-web\n    path: ../svc-web\n    owner: web-agent\n",
        encoding="utf-8",
    )
    return meta / "platform.yaml"


def test_the_echo_names_the_program_and_its_repo_count(program, capsys):
    INIT._echo_resolved_target(program)

    out = capsys.readouterr()
    text = out.out + out.err
    assert str(program.parent) in text, "the fully-resolved target root must be printed"
    assert "acme" in text, "the identity check must name the project"
    assert "repos: 2" in text, "the identity check must carry the repo count"


def test_the_echo_resolves_each_repo_path(program, capsys):
    """`../svc-api` tells the operator nothing about WHERE it landed; the resolved
    path does, and resolution is the step that goes wrong when init runs from the
    wrong directory."""
    INIT._echo_resolved_target(program)

    text = capsys.readouterr().out
    assert str((program.parent / ".." / "svc-api").resolve()) in text
    assert str((program.parent / ".." / "svc-web").resolve()) in text
    assert ".otaman marker" in text, "say what will be written into them"


def test_an_unreadable_platform_yaml_still_gets_the_path(tmp_path, capsys):
    """The echo is never the thing that fails: the validator rules on the file."""
    bad = tmp_path / "meta" / "platform.yaml"
    bad.parent.mkdir()
    bad.write_text("project: [unclosed\n", encoding="utf-8")

    INIT._echo_resolved_target(bad)  # must not raise

    text = capsys.readouterr().out
    assert str(bad.parent) in text
    assert "could not be read" in text


def test_the_echo_runs_before_the_first_write(program, monkeypatch, capsys):
    """Order, not just presence: the echo must precede `generate-agent-config.py`.

    Pinned by observing the sequence through cmd_init itself — the echo is only
    useful while the mistake is still undone, so an echo that happens to be printed
    after the generator ran would satisfy a content-only assertion and nothing else.
    """
    events: list[str] = []

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run_script(name, *args, **kwargs):
        events.append(f"script:{name}")
        return _Result()

    def fake_echo(config_path):
        events.append("echo")

    monkeypatch.setattr(INIT, "run_script", fake_run_script)
    monkeypatch.setattr(INIT, "_echo_resolved_target", fake_echo)
    monkeypatch.setattr(INIT, "_init_preflight", lambda a: None)
    monkeypatch.setattr(INIT, "_cmd_init_update", lambda *a, **k: 0)
    monkeypatch.setattr(INIT, "_scaffold_launcher_after_init", lambda *a, **k: None)
    monkeypatch.setattr(INIT, "_ensure_openspec_cli", lambda *a, **k: None)
    monkeypatch.setattr(INIT, "_refuse_inside_a_repo_of_a_program", lambda a: None)

    INIT.cmd_init([str(program), "--skip-doctor"])

    assert "echo" in events, f"the echo never ran: {events}"
    writes = [e for e in events if e.startswith("script:generate")]
    assert writes, f"the generator never ran, so the order is untested: {events}"
    assert events.index("echo") < events.index(writes[0]), (
        f"the echo must come before the first write: {events}"
    )
