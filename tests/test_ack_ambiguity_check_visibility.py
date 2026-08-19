"""Bus reliability fixes from the 2026-08 bug reports.

1. fswatch-agent 20260814T213000 — `otaman ack` on an ambiguous partial
   stem used to ack ALL matches, including copies addressed to other
   agents.  Fix: restrict candidates to the calling agent's own copy,
   then reject remaining ambiguity the way `otaman read` does.

2. cofounder-agent 20260811T202643 — a message file `otaman check`
   cannot parse was skipped silently (invisible pending message).  Fix:
   check now warns about unparseable active-bus files.  Regression
   guard: every parseable active file with status: pending addressed to
   the agent MUST appear in check output.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from otaman_cli.commands.bus_messaging import _file_is_for_agent

# ---------------------------------------------------------------------------
# Helpers


@pytest.fixture
def project(tmp_path: Path) -> Path:
    meta = tmp_path / "meta"
    meta.mkdir()
    (meta / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (meta / "platform.yaml").write_text("project: testproj\nrepos: []\n", encoding="utf-8")
    return meta


def _plant(meta: Path, stem: str, fm: dict, body: str = "## Subject: s\n") -> Path:
    lines = "---\n"
    for k, v in fm.items():
        lines += f"{k}: {v}\n"
    lines += "---\n\n" + body
    f = meta / ".agents" / "bus" / "active" / f"{stem}.md"
    f.write_text(lines, encoding="utf-8")
    return f


def _run(meta: Path, agent: str, *argv: str) -> subprocess.CompletedProcess:
    # Propagate this process's import paths so the subprocess finds
    # otaman_cli even when running from a bare venv (sibling-checkout dev
    # setups); harmless in CI where the package is installed.
    env = {
        **os.environ,
        "OTAMAN_AGENT": agent,
        "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
    }
    # A developer shell's OTAMAN_ROOT/MAESTRO_ROOT would redirect the
    # subprocess to the real workspace bus (root chain: marker → env →
    # walk-up). Strip for isolation.
    for var in ("OTAMAN_ROOT", "MAESTRO_ROOT"):
        env.pop(var, None)
    return subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", *argv],
        capture_output=True,
        text=True,
        cwd=str(meta),
        env=env,
    )


def _acks(meta: Path) -> list[str]:
    return sorted(p.name for p in (meta / ".agents" / "bus" / "active" / "acks").glob("*.ack"))


_BASE_FM = {
    "priority": "normal",
    "type": "info",
    "timestamp": "2026-08-14T10:00:00Z",
    "status": "pending",
}


# ---------------------------------------------------------------------------
# _file_is_for_agent — naming shapes from the live bus


def test_primary_to_me_is_mine():
    assert _file_is_for_agent(
        "20260814T100000-spec-agent-to-cli-agent-hello", {"to": "cli-agent"}, "cli-agent"
    )


def test_primary_to_other_is_not_mine():
    assert not _file_is_for_agent(
        "20260814T100000-spec-agent-to-runner-agent-hello", {"to": "runner-agent"}, "cli-agent"
    )


def test_broadcast_is_mine():
    assert _file_is_for_agent("20260814T100000-spec-agent-to-all-hello", {"to": "all"}, "cli-agent")


def test_current_cc_copy_for_me_is_mine():
    # Current fan-out naming: recipient in -to-, frontmatter keeps orig to + x-cc
    fm = {"to": "human", "cc": ["cli-agent", "spec-agent"], "x-cc": True}
    assert _file_is_for_agent("20260814T100000-license-agent-to-cli-agent-notice", fm, "cli-agent")


def test_current_cc_copy_for_other_is_not_mine():
    # Same message, spec-agent's copy — cc list still names me; must not match
    fm = {"to": "human", "cc": ["cli-agent", "spec-agent"], "x-cc": True}
    assert not _file_is_for_agent(
        "20260814T100000-license-agent-to-spec-agent-notice", fm, "cli-agent"
    )


def test_legacy_cc_copy_for_me_is_mine():
    fm = {"to": "human", "cc": ["cli-agent"], "x-cc": True}
    assert _file_is_for_agent(
        "20260814T100000-runner-agent-to-human-cc-cli-agent-plan", fm, "cli-agent"
    )


def test_legacy_cc_copy_for_other_is_not_mine():
    # Human's legacy copy of a message addressed to ME — the -to-cli-agent-
    # segment must not fool the filter
    fm = {"to": "cli-agent", "cc": ["human"], "x-cc": True}
    assert not _file_is_for_agent(
        "20260814T100000-spec-agent-to-cli-agent-cc-human-notice", fm, "cli-agent"
    )


def test_unparseable_frontmatter_falls_back_to_filename():
    assert _file_is_for_agent("20260814T100000-x-to-cli-agent-broken", {}, "cli-agent")


# ---------------------------------------------------------------------------
# cmd_ack — ambiguity + foreign-copy rejection (end-to-end)


def test_ack_ambiguous_prefix_acks_only_my_message(project: Path):
    """fswatch's exact repro: one timestamp prefix, reminders to 4 agents."""
    ts = "20260730T220850"
    for agent in ("cpo-agent", "deploy-agent", "runner-agent", "fswatch-agent"):
        _plant(
            project,
            f"{ts}-cofounder-agent-to-{agent}-reminder-triage-report",
            {**_BASE_FM, "id": f"{ts}-cofounde", "from": "cofounder-agent", "to": agent},
        )
    rc = _run(project, "fswatch-agent", "ack", f"{ts}-cofounde")
    assert rc.returncode == 0
    assert _acks(project) == [
        f"{ts}-cofounder-agent-to-fswatch-agent-reminder-triage-report.fswatch-agent.ack"
    ]


def test_ack_rejects_when_no_match_is_mine(project: Path):
    ts = "20260730T220850"
    for agent in ("cpo-agent", "runner-agent"):
        _plant(
            project,
            f"{ts}-cofounder-agent-to-{agent}-reminder",
            {**_BASE_FM, "id": f"{ts}-cofounde", "from": "cofounder-agent", "to": agent},
        )
    rc = _run(project, "cli-agent", "ack", f"{ts}-cofounde")
    assert rc.returncode == 1
    out = rc.stdout + rc.stderr
    assert "none are addressed to cli-agent" in out
    assert _acks(project) == []


def test_ack_ambiguous_among_my_messages_errors(project: Path):
    """Two DISTINCT messages to me sharing a prefix → error, nothing acked."""
    ts = "20260814T1200"
    _plant(
        project,
        f"{ts}00-spec-agent-to-cli-agent-first",
        {**_BASE_FM, "id": f"{ts}00-spec-age", "from": "spec-agent", "to": "cli-agent"},
    )
    _plant(
        project,
        f"{ts}05-spec-agent-to-cli-agent-second",
        {**_BASE_FM, "id": f"{ts}05-spec-age", "from": "spec-agent", "to": "cli-agent"},
    )
    rc = _run(project, "cli-agent", "ack", "spec-agent-to-cli-agent")
    assert rc.returncode == 1
    assert "Ambiguous stem" in (rc.stdout + rc.stderr)
    assert _acks(project) == []


def test_ack_skips_foreign_cc_copy_of_my_message(project: Path):
    """Primary to me + legacy cc copy for human: only MY file gets acked."""
    ts = "20260723T183147"
    _plant(
        project,
        f"{ts}-spec-agent-to-cli-agent-update-license",
        {**_BASE_FM, "id": f"{ts}-spec-age", "from": "spec-agent", "to": "cli-agent"},
    )
    _plant(
        project,
        f"{ts}-spec-agent-to-cli-agent-cc-human-update-license",
        {
            **_BASE_FM,
            "id": f"{ts}-spec-age",
            "from": "spec-agent",
            "to": "cli-agent",
            "cc": "[human]",
            "x-cc": "true",
        },
    )
    rc = _run(project, "cli-agent", "ack", f"{ts}-spec-age")
    assert rc.returncode == 0
    assert _acks(project) == [f"{ts}-spec-agent-to-cli-agent-update-license.cli-agent.ack"]


def test_ack_single_unambiguous_still_works(project: Path):
    stem = "20260814T100000-spec-agent-to-cli-agent-hello"
    _plant(
        project,
        stem,
        {**_BASE_FM, "id": "20260814T100000-spec-age", "from": "spec-agent", "to": "cli-agent"},
    )
    rc = _run(project, "cli-agent", "ack", stem, "--read")
    assert rc.returncode == 0
    ack = project / ".agents" / "bus" / "active" / "acks" / f"{stem}.cli-agent.ack"
    assert ack.read_text(encoding="utf-8").strip() == "read"


def test_ack_broadcast_works(project: Path):
    stem = "20260814T100000-spec-agent-to-all-announcement"
    _plant(
        project,
        stem,
        {**_BASE_FM, "id": "20260814T100000-spec-age", "from": "spec-agent", "to": "all"},
    )
    rc = _run(project, "cli-agent", "ack", stem)
    assert rc.returncode == 0
    assert _acks(project) == [f"{stem}.cli-agent.ack"]


# ---------------------------------------------------------------------------
# cmd_check — no silent skips (cofounder's regression guard)


def test_check_lists_every_pending_message_addressed_to_agent(project: Path):
    """Every parseable active file with status pending + to: <agent> MUST
    appear in check output — including long truncated-slug stems like the
    one from the original incident."""
    stems = [
        "20260730T205534-plugin-agent-to-cofounder-agent-otaman-meta-triage-report-repo-review-ph",
        "20260730T205711-plugin-agent-to-cofounder-agent-otaman-plugin-report",
        "20260814T100000-spec-agent-to-all-broadcast-note",
    ]
    for i, stem in enumerate(stems):
        to = "all" if "to-all" in stem else "cofounder-agent"
        _plant(
            project,
            stem,
            {**_BASE_FM, "id": stem[:24], "from": "plugin-agent", "to": to},
            body=f"## Subject: message {i}\n",
        )
    out = _run(project, "cofounder-agent", "check").stdout
    for stem in stems:
        assert stem in out, f"pending message invisible in check: {stem}"


def test_check_warns_on_unparseable_file(project: Path):
    good = "20260814T100000-spec-agent-to-cli-agent-fine"
    _plant(project, good, {**_BASE_FM, "id": "x", "from": "spec-agent", "to": "cli-agent"})
    bad = project / ".agents" / "bus" / "active" / "20260814T100001-broken-frontmatter.md"
    bad.write_text("---\nto: [unclosed\n---\n\n## Subject: broken\n", encoding="utf-8")
    out = _run(project, "cli-agent", "check").stdout
    assert good in out  # parseable messages unaffected
    assert "could not be parsed" in out
    assert "20260814T100001-broken-frontmatter.md" in out


def test_check_no_warning_when_all_parseable(project: Path):
    _plant(
        project,
        "20260814T100000-spec-agent-to-cli-agent-fine",
        {**_BASE_FM, "id": "x", "from": "spec-agent", "to": "cli-agent"},
    )
    out = _run(project, "cli-agent", "check").stdout
    assert "could not be parsed" not in out


# ---------------------------------------------------------------------------
# check/ack ownership parity — CC fan-out copies (2026-08-19 live-bus find:
# check surfaced every recipient's copy of a fanned-out CC message because
# each copy carries the full cc: list; the extra copies were permanently
# pending — ack correctly refuses them. check now uses _file_is_for_agent.)


def test_check_shows_only_own_cc_copy_current_naming(project: Path):
    fm = {
        **_BASE_FM,
        "id": "20260818T143518-spec-age",
        "from": "spec-agent",
        "to": "plugin-agent",
        "cc": "[core-agent, cli-agent]",
    }
    primary = "20260818T143518-spec-agent-to-plugin-agent-amendment"
    theirs = "20260818T143518-spec-agent-to-core-agent-amendment"
    mine = "20260818T143518-spec-agent-to-cli-agent-amendment"
    _plant(project, primary, fm)
    _plant(project, theirs, {**fm, "x-cc": "true"})
    _plant(project, mine, {**fm, "x-cc": "true"})

    out = _run(project, "cli-agent", "check").stdout
    assert mine in out, "own CC copy must be visible"
    assert theirs not in out, "another recipient's CC copy must not surface"
    assert primary not in out, "the primary recipient's copy must not surface"


def test_check_shows_only_own_cc_copy_legacy_naming(project: Path):
    fm = {
        **_BASE_FM,
        "id": "20260818T143518-spec-age",
        "from": "spec-agent",
        "to": "plugin-agent",
        "cc": "[core-agent, cli-agent]",
        "x-cc": "true",
    }
    theirs = "20260818T143518-spec-agent-to-plugin-agent-cc-core-agent-amendment"
    mine = "20260818T143518-spec-agent-to-plugin-agent-cc-cli-agent-amendment"
    _plant(project, theirs, fm)
    _plant(project, mine, fm)

    out = _run(project, "cli-agent", "check").stdout
    assert mine in out
    assert theirs not in out


def test_check_ack_agree_on_cc_copy_ownership(project: Path):
    """Everything check surfaces as pending must be ackable by the same
    agent — the incident was 23 permanently-pending copies ack refused."""
    fm = {
        **_BASE_FM,
        "id": "20260818T143518-spec-age",
        "from": "spec-agent",
        "to": "plugin-agent",
        "cc": "[core-agent, cli-agent]",
        "x-cc": "true",
    }
    mine = "20260818T143518-spec-agent-to-cli-agent-amendment"
    _plant(project, "20260818T143518-spec-agent-to-core-agent-amendment", fm)
    _plant(project, mine, fm)

    out = _run(project, "cli-agent", "check").stdout
    assert mine in out
    res = _run(project, "cli-agent", "ack", mine)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _acks(project) == [f"{mine}.cli-agent.ack"]


def test_undesignated_cc_copy_with_me_in_cc_is_mine():
    """bus-server copies named <ts>-<slug> (no -to-/-cc- segment) fall back
    to frontmatter cc: membership."""
    assert _file_is_for_agent(
        "20260608T200000-cc1", {"to": "human", "cc": ["cli-agent"], "x-cc": True}, "cli-agent"
    )


def test_undesignated_cc_copy_without_me_in_cc_is_not_mine():
    assert not _file_is_for_agent(
        "20260608T200000-cc1", {"to": "human", "cc": ["core-agent"], "x-cc": True}, "cli-agent"
    )
