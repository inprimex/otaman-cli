"""single-bus-per-program tasks 2.1-2.3 — URI targets + cross-program delivery.

Covers the bus-uri-addressing capability's otaman-cli half: the three
input forms, declared-layout target resolution (no walk-up), fail-closed
`bus.boundaries` enforcement with type/agent narrowing, cross-org
rejection, and schema-v2 envelope projections.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from otaman_cli.bus_target import (
    BoundaryError,
    CrossOrgError,
    TargetResolutionError,
    check_boundaries,
    derive_local_context,
    resolve_cross_program_delivery,
    resolve_target_program_root,
)

# ---------------------------------------------------------------------------
# Layout fixtures


def _mk_program(org_root: Path, program: str, meta_name: str | None = None) -> Path:
    """Create orgs/<org>/programs/<program>/<meta>/ with platform.yaml + bus."""
    meta = org_root / "programs" / program / (meta_name or f"{program}-otaman")
    (meta / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (meta / "platform.yaml").write_text(f"project: {program}\nrepos: []\n", encoding="utf-8")
    return meta


@pytest.fixture
def org(tmp_path: Path) -> Path:
    org_root = tmp_path / "orgs" / "acme"
    org_root.mkdir(parents=True)
    return org_root


@pytest.fixture
def sender_root(org: Path) -> Path:
    return _mk_program(org, "alpha")


def _grant(meta: Path, yaml_block: str) -> None:
    platform = meta / "platform.yaml"
    platform.write_text(platform.read_text(encoding="utf-8") + yaml_block, encoding="utf-8")


# ---------------------------------------------------------------------------
# derive_local_context — declared-layout interpretation


def test_context_derived_from_conforming_layout(sender_root: Path, org: Path):
    ctx = derive_local_context(sender_root)
    assert ctx is not None
    assert (ctx.org, ctx.program) == ("acme", "alpha")
    assert ctx.org_root == org.resolve()


def test_context_none_outside_layout(tmp_path: Path):
    loose = tmp_path / "somewhere" / "meta"
    loose.mkdir(parents=True)
    assert derive_local_context(loose) is None


def test_context_none_for_invalid_slugs(tmp_path: Path):
    bad = tmp_path / "orgs" / "Acme_Corp" / "programs" / "alpha" / "alpha-otaman"
    bad.mkdir(parents=True)
    assert derive_local_context(bad) is None


# ---------------------------------------------------------------------------
# resolve_target_program_root — declarations only, never discovery


def test_resolves_conventional_meta_dir(sender_root: Path, org: Path):
    _mk_program(org, "beta")
    ctx = derive_local_context(sender_root)
    assert resolve_target_program_root(ctx, "beta").name == "beta-otaman"


def test_resolves_declared_override_from_org_config(sender_root: Path, org: Path):
    meta = _mk_program(org, "gamma", meta_name="gamma-meta")
    cfg = org / "config"
    cfg.mkdir()
    (cfg / "launch-settings.yaml").write_text(f"programs:\n  - {meta}\n", encoding="utf-8")
    ctx = derive_local_context(sender_root)
    assert resolve_target_program_root(ctx, "gamma") == meta


def test_no_walkup_discovery_of_agents_roots(sender_root: Path, org: Path):
    """A program whose meta dir is neither declared nor conventional is
    unreachable even though a perfectly good .agents root EXISTS — the
    P1 regression guard: declarations only, no scanning."""
    _mk_program(org, "delta", meta_name="weird-name")  # has .agents, wrong name
    ctx = derive_local_context(sender_root)
    with pytest.raises(TargetResolutionError, match="cannot resolve program 'delta'"):
        resolve_target_program_root(ctx, "delta")


def test_unknown_program_error_names_the_fix(sender_root: Path):
    ctx = derive_local_context(sender_root)
    with pytest.raises(TargetResolutionError, match="launch-settings.yaml"):
        resolve_target_program_root(ctx, "ghost")


# ---------------------------------------------------------------------------
# check_boundaries — fail closed, narrowing


def test_no_boundaries_section_refused(sender_root: Path, org: Path):
    target = _mk_program(org, "beta")
    with pytest.raises(BoundaryError, match="no bus.boundaries.allow_from grant"):
        check_boundaries(
            target,
            sender_program="alpha",
            sender_agent="cli-agent",
            msg_type="info",
            target_program="beta",
        )


def test_whole_program_grant_delivers(sender_root: Path, org: Path):
    target = _mk_program(org, "beta")
    _grant(target, "bus:\n  boundaries:\n    allow_from:\n      - program: alpha\n")
    check_boundaries(
        target,
        sender_program="alpha",
        sender_agent="cli-agent",
        msg_type="info",
        target_program="beta",
    )  # no raise


def test_agent_narrowing_refuses_unlisted_sender(org: Path):
    target = _mk_program(org, "beta")
    _grant(
        target,
        "bus:\n  boundaries:\n    allow_from:\n"
        "      - program: alpha\n        agents: [spec-agent]\n",
    )
    with pytest.raises(BoundaryError, match="fall outside the grant"):
        check_boundaries(
            target,
            sender_program="alpha",
            sender_agent="cli-agent",
            msg_type="info",
            target_program="beta",
        )


def test_type_narrowing_refuses_disallowed_type(org: Path):
    target = _mk_program(org, "beta")
    _grant(
        target,
        "bus:\n  boundaries:\n    allow_from:\n"
        "      - program: alpha\n        types: [question, info]\n",
    )
    with pytest.raises(BoundaryError, match="type 'task-assignment'"):
        check_boundaries(
            target,
            sender_program="alpha",
            sender_agent="cli-agent",
            msg_type="task-assignment",
            target_program="beta",
        )


def test_wrong_program_grant_refused_naming_grant(org: Path):
    target = _mk_program(org, "beta")
    _grant(target, "bus:\n  boundaries:\n    allow_from:\n      - program: other\n")
    with pytest.raises(BoundaryError, match="no bus.boundaries.allow_from grant for program"):
        check_boundaries(
            target,
            sender_program="alpha",
            sender_agent="cli-agent",
            msg_type="info",
            target_program="beta",
        )


# ---------------------------------------------------------------------------
# cross-org rejection (2.3)


def test_cross_org_rejected_naming_foreign_org(sender_root: Path):
    ctx = derive_local_context(sender_root)
    with pytest.raises(CrossOrgError, match="cross-org routing not yet implemented") as exc:
        resolve_cross_program_delivery(
            ctx,
            target_program="site",
            target_org="contoso",
            sender_agent="cli-agent",
            msg_type="info",
        )
    assert "contoso" in str(exc.value)


# ---------------------------------------------------------------------------
# End-to-end via the CLI (subprocess, real dispatch)


def _run_send(cwd: Path, agent: str, to: str, *extra: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "OTAMAN_AGENT": agent,
        "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
        "NO_COLOR": "1",
    }
    # Root resolution is marker → env → walk-up; a developer shell's
    # OTAMAN_ROOT/MAESTRO_ROOT would silently redirect every send in these
    # tests to the real workspace bus. Strip them for isolation.
    for var in ("OTAMAN_ROOT", "MAESTRO_ROOT"):
        env.pop(var, None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "otaman_cli.main",
            "send",
            to,
            "--subject",
            "hello there",
            "--body",
            "body text",
            *extra,
        ],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
    )


def _sole_message(meta: Path) -> str:
    msgs = list((meta / ".agents" / "bus" / "active").glob("*.md"))
    assert len(msgs) == 1, [m.name for m in msgs]
    return msgs[0].read_text(encoding="utf-8")


def test_bare_name_send_unchanged_plus_uri_fields(sender_root: Path):
    rc = _run_send(sender_root, "cli-agent", "spec-agent")
    assert rc.returncode == 0, rc.stdout + rc.stderr
    body = _sole_message(sender_root)
    assert "to: spec-agent\n" in body
    assert "to-uri: otaman://acme/alpha/spec-agent\n" in body
    assert "from-uri: otaman://acme/alpha/cli-agent\n" in body
    assert "from_org: acme\n" in body
    assert "to_org: acme\n" in body


def test_bare_name_outside_layout_keeps_legacy_envelope(tmp_path: Path):
    meta = tmp_path / "legacy-meta"
    (meta / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (meta / "platform.yaml").write_text("project: legacy\nrepos: []\n", encoding="utf-8")
    rc = _run_send(meta, "cli-agent", "spec-agent")
    assert rc.returncode == 0, rc.stdout + rc.stderr
    body = _sole_message(meta)
    assert "to: spec-agent\n" in body
    assert "to-uri:" not in body and "from_org:" not in body


def test_shorthand_cross_program_delivers_into_target_bus(sender_root: Path, org: Path):
    target = _mk_program(org, "beta")
    _grant(target, "bus:\n  boundaries:\n    allow_from:\n      - program: alpha\n")
    rc = _run_send(sender_root, "cli-agent", "pm-agent@beta")
    assert rc.returncode == 0, rc.stdout + rc.stderr
    # Nothing written locally; one copy in the target's own bus
    assert list((sender_root / ".agents" / "bus" / "active").glob("*.md")) == []
    body = _sole_message(target)
    assert "to: pm-agent\n" in body
    assert "to-uri: otaman://acme/beta/pm-agent\n" in body
    assert "from-uri: otaman://acme/alpha/cli-agent\n" in body
    assert "to_org: acme\n" in body


def test_full_uri_form_delivers(sender_root: Path, org: Path):
    target = _mk_program(org, "beta")
    _grant(target, "bus:\n  boundaries:\n    allow_from:\n      - program: alpha\n")
    rc = _run_send(sender_root, "cli-agent", "otaman://acme/beta/pm-agent")
    assert rc.returncode == 0, rc.stdout + rc.stderr
    assert "to: pm-agent\n" in _sole_message(target)


def test_cross_program_refused_without_grant(sender_root: Path, org: Path):
    _mk_program(org, "beta")  # no boundaries declared
    rc = _run_send(sender_root, "cli-agent", "pm-agent@beta")
    assert rc.returncode == 1
    out = rc.stdout + rc.stderr
    assert "no bus.boundaries.allow_from grant" in out
    assert (
        list(
            (org / "programs" / "beta" / "beta-otaman" / ".agents" / "bus" / "active").glob("*.md")
        )
        == []
    )


def test_cross_org_uri_rejected_via_cli(sender_root: Path):
    rc = _run_send(sender_root, "cli-agent", "otaman://contoso/site/ops-agent")
    assert rc.returncode == 1
    out = rc.stdout + rc.stderr
    assert "cross-org routing not yet implemented" in out
    assert "contoso" in out


def test_cross_program_form_outside_layout_errors(tmp_path: Path):
    meta = tmp_path / "legacy-meta"
    (meta / ".agents" / "bus" / "active" / "acks").mkdir(parents=True)
    (meta / "platform.yaml").write_text("project: legacy\nrepos: []\n", encoding="utf-8")
    rc = _run_send(meta, "cli-agent", "pm-agent@beta")
    assert rc.returncode == 1
    assert "declared org layout" in (rc.stdout + rc.stderr)


def test_cross_program_explicit_cc_written_in_target_bus(sender_root: Path, org: Path):
    target = _mk_program(org, "beta")
    _grant(target, "bus:\n  boundaries:\n    allow_from:\n      - program: alpha\n")
    rc = _run_send(sender_root, "cli-agent", "pm-agent@beta", "--cc", "qa-agent")
    assert rc.returncode == 0, rc.stdout + rc.stderr
    names = sorted(p.name for p in (target / ".agents" / "bus" / "active").glob("*.md"))
    assert len(names) == 2
    assert any("-to-pm-agent-" in n for n in names)
    assert any("-to-qa-agent-" in n for n in names)
    assert list((sender_root / ".agents" / "bus" / "active").glob("*.md")) == []


def test_cc_comma_list_split_into_recipients(sender_root: Path):
    """landing-agent bug 20260817T115426: `--cc a,b` wrote a CC copy for
    the literal recipient 'a,b'. Commas now split."""
    rc = _run_send(sender_root, "cli-agent", "spec-agent", "--cc", "qa-agent,ops-agent")
    assert rc.returncode == 0, rc.stdout + rc.stderr
    names = sorted(p.name for p in (sender_root / ".agents" / "bus" / "active").glob("*.md"))
    assert len(names) == 3  # primary + 2 cc copies
    assert not any("qa-agent,ops-agent" in n or "qa-agent-ops-agent" in n for n in names)
    assert any("-to-qa-agent-" in n for n in names)
    assert any("-to-ops-agent-" in n for n in names)
