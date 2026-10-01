"""`otaman whoami --resolve-only` signals CAPABILITY, not just failure.

plugin reported the blocker (20261001T124643): `check-ownership.sh` exits 0 when
enforcement identity does not resolve, so an agent nobody can name writes
anywhere. They wrote a fail-closed fix and reverted it, because an existing test
encodes a deliberate prior decision — a stale CLI must NOT deny, since an old
bundle is this fleet's designed steady state, not an anomaly.

The hook could not tell the two apart:

  * CANNOT ASK — the CLI is absent, or too old to support the flag. An old build
    does not error: it ignores the unknown flag, prints the full `whoami`
    banner, and exits 0. plugin's resolver rejects that by shape and returns 1.
  * ASKED AND GOT NOTHING — a current build, chain ran, no identity. Exited 1.

Both reached the hook as 1. This gives the second case a code no older build can
emit, which is what makes fail-closed possible without denying every lagging
tenant. The hook's policy on each code stays plugin's.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from otaman_cli.commands.status_cluster import (
    UNRESOLVED_BUT_CAPABLE,
    _cmd_whoami_resolve_only,
)


def test_the_capable_but_unresolved_code_is_not_one():
    """1 is what a cannot-ask path already returns through plugin's resolver.
    Reusing it would leave the two cases indistinguishable, which is the bug."""
    assert UNRESOLVED_BUT_CAPABLE != 1
    assert UNRESOLVED_BUT_CAPABLE != 0


def test_an_unresolved_identity_returns_the_capability_code(monkeypatch):
    import otaman_core.identity as ident

    class _Empty:
        agent = ""

    monkeypatch.setattr(ident, "resolve_enforcement_identity", lambda: _Empty())
    assert _cmd_whoami_resolve_only() == UNRESOLVED_BUT_CAPABLE


def test_a_resolved_identity_prints_only_the_name(monkeypatch, capsys):
    """A shell captures this with $(...) — anything else on stdout corrupts it."""
    import otaman_core.identity as ident

    class _Named:
        agent = "cli-agent"

    monkeypatch.setattr(ident, "resolve_enforcement_identity", lambda: _Named())
    assert _cmd_whoami_resolve_only() == 0
    assert capsys.readouterr().out.strip() == "cli-agent"


def test_nothing_is_printed_when_unresolved(monkeypatch, capsys):
    """The hook captures stdout; a diagnostic here would be read as an identity."""
    import otaman_core.identity as ident

    class _Empty:
        agent = ""

    monkeypatch.setattr(ident, "resolve_enforcement_identity", lambda: _Empty())
    _cmd_whoami_resolve_only()
    assert capsys.readouterr().out.strip() == ""


def test_the_contract_is_documented_where_a_caller_will_look():
    """The hook is a shell script in another repo; the exit codes are the whole
    interface, so they are stated in the function a reader lands on."""
    import inspect

    doc = inspect.getdoc(_cmd_whoami_resolve_only) or ""
    assert "EXIT CODES ARE A CONTRACT" in doc
    assert "3" in doc and "0" in doc


@pytest.mark.skipif(os.name == "nt", reason="exit-code plumbing differs on Windows shells")
def test_end_to_end_through_the_real_entry_point(tmp_path):
    """What the hook actually runs: a subprocess, reading $?.

    Run from a directory with no program marker, so the chain genuinely
    resolves nothing — the case that used to be indistinguishable from an
    absent CLI.
    """
    here = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    core = here.parent / "otaman-core" / "src"
    env["PYTHONPATH"] = os.pathsep.join([str(here / "src"), str(core)])
    env.pop("OTAMAN_AGENT", None)
    result = subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", "whoami", "--resolve-only"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == UNRESOLVED_BUT_CAPABLE, result.stderr[-400:]
    assert result.stdout.strip() == ""
