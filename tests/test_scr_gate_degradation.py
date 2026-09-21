"""The SCR gate degrades to "no gate", never to a broken verb.

The template lives in `otaman_core.scr_template`, and cli cannot express "a
core that HAS it" as a dependency: core shipped it without a version bump and
no repo pins otaman-core, so 0.3.0 exists both with and without the module. A
version pin would be false assurance about the one skew that actually occurs.

plugin-agent hit the same gap and solved it at the call site; this is their
pattern. The argument that decided it: bare imports meant `otaman propose` and
`otaman send` would raise ImportError at call time on a laggard bundle. Losing
the quality gate is recoverable; losing `propose` is not — and it would fail at
the exact moment someone is trying to report a problem.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

import otaman_cli.scr_gate as gate


@pytest.fixture
def laggard(monkeypatch):
    """An install whose otaman-core predates the shared template."""
    monkeypatch.setattr(gate, "scr_template", lambda: None)


def _root():
    root = pathlib.Path(tempfile.mkdtemp()) / "meta"
    (root / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (root / ".agents" / "blocked").mkdir(parents=True)
    (root / "platform.yaml").write_text("project: d\nversion: '1.0'\nrepos: []\n", encoding="utf-8")
    return root


def _wire_propose(monkeypatch, root):
    import otaman_cli.commands.propose_team as PT

    monkeypatch.setattr(PT, "find_project_root", lambda: root)
    monkeypatch.setattr(PT, "resolve_agent_identity", lambda _r: "cli-agent")
    return PT


def _wire_send(monkeypatch, root):
    import otaman_cli.commands.bus_messaging as BM

    active = root / ".agents" / "bus" / "active"
    monkeypatch.setattr(BM, "find_project_root", lambda: root)
    monkeypatch.setattr(BM, "resolve_agent_identity", lambda r, explicit=None: "cli-agent")
    monkeypatch.setattr(BM, "_resolve_bus_paths", lambda r: (active, active / "acks"))
    return BM, active


# ---------------------------------------------------------------------------
# the probe


def test_the_accessor_returns_the_module_when_present():
    assert gate.scr_template() is not None


def test_a_missing_core_module_reads_as_absent(monkeypatch):
    import builtins

    real = builtins.__import__

    def boom(name, *args, **kwargs):
        if name == "otaman_core":
            raise ImportError("simulated laggard core")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", boom)
    assert gate.scr_template() is None


def test_a_partially_updated_core_reads_as_absent(monkeypatch):
    """The skew no version expression can describe: core 0.3.0 WITH the module
    versus core 0.3.0 WITHOUT it — and the half-updated case between them."""
    import otaman_core.scr_template as module

    monkeypatch.delattr(module, "is_hollow", raising=True)
    assert gate.scr_template() is None


# ---------------------------------------------------------------------------
# what a laggard install must still be able to do


def test_propose_still_files_without_the_template(laggard, monkeypatch):
    """THE POINT. This path used to ImportError — at the moment someone is
    trying to report a problem."""
    root = _root()
    PT = _wire_propose(monkeypatch, root)
    rc = PT.cmd_propose(["a problem worth reporting", "-d", "the observed behaviour"])
    assert rc == 0
    written = list((root / ".agents" / "bus" / "active").glob("*spec-change-request*.md"))
    assert len(written) == 1
    assert "the observed behaviour" in written[0].read_text(encoding="utf-8")


def test_send_still_sends_without_the_template(laggard, monkeypatch):
    root = _root()
    BM, active = _wire_send(monkeypatch, root)
    rc = BM.cmd_send(
        ["human", "--type", "spec-change-request", "--subject", "x", "--body", "TODO\n"]
    )
    assert rc == 0
    assert len(list(active.glob("*.md"))) == 1


def test_the_gate_still_refuses_when_the_template_IS_present(monkeypatch):
    """Degradation must not become the normal path — with core present, the
    hollow-SCR refusal still fires."""
    root = _root()
    BM, active = _wire_send(monkeypatch, root)
    rc = BM.cmd_send(
        ["human", "--type", "spec-change-request", "--subject", "x", "--body", "TODO\n"]
    )
    assert rc == 2
    assert list(active.glob("*.md")) == []


def test_propose_still_refuses_a_hollow_scr_when_present(monkeypatch):
    root = _root()
    PT = _wire_propose(monkeypatch, root)
    assert PT.cmd_propose(["a thing"]) == 1


def test_no_call_site_imports_the_template_bare():
    """A new bare import would reintroduce the ImportError-at-call-time bug."""
    import inspect

    from otaman_cli.commands import bus_messaging, propose_team
    from otaman_cli.console import app

    for module in (propose_team, bus_messaging, app):
        src = inspect.getsource(module)
        assert "from otaman_core.scr_template import" not in src, module.__name__
        assert "scr_gate" in src, module.__name__
