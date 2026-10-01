"""Two repoints onto homes core shipped for them (core #100, #101).

Both were asks I made rather than assumptions, and both removed a second home that
had existed only because core had none yet:

* **#229's `approved_by` composition.** The v-act wrote the attestation string
  itself because core shipped no writer for it. core #100 homed the FORMAT in
  `apply_spec_approved`, and a format with two writers is one a stricter gate parse
  can drift from.
* **sghc 1.2's `security-gates` read.** The block is migrating from `platform.yaml`
  to `verification-gates.yaml` (plugin's csp 1.2). core #101's
  `resolve_security_gates` reads the new home first and falls back, so this call
  site no longer knows which file won. The ordering matters and core kept it:
  reader first, THEN plugin migrates — otherwise there is a window where the block
  is absent from the only place anyone reads.

One thing was deliberately NOT delegated, and the last test says why: PRESENCE.
core's resolver returns an empty config when neither file declares the block, which
makes "nobody configured a ladder" and "a ladder configured as empty" the same
value — the first is not a failure and the second is a finding.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("otaman_core.security_gates")

from otaman_cli import security_gates  # noqa: E402

GATES = """\
security-gates:
  languages:
    python:
      ci-fast:
        tools: [gitleaks]
        blocking: true
"""

PLATFORM = """\
project: demo
version: '1.0'
repos:
  - name: otaman-cli
    owner: cli-agent
    languages: [python]
"""


def _program(tmp_path: Path, *, platform_extra: str = "", gates_file: str = "") -> Path:
    root = tmp_path / "prog"
    root.mkdir(exist_ok=True)
    (root / "platform.yaml").write_text(PLATFORM + platform_extra, encoding="utf-8")
    if gates_file:
        (root / "verification-gates.yaml").write_text(gates_file, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# core #101 — the block is read from whichever home has it.


def test_the_block_is_read_from_platform_yaml(tmp_path):
    """The pre-migration home, which must keep working for every tenant that has
    not migrated — which today is all of them."""
    report = security_gates.evaluate(_program(tmp_path, platform_extra=GATES))

    assert not report.error
    assert [r.repo for r in report.repos] == ["otaman-cli"]
    assert report.repos[0].verdict != security_gates.NOT_CHECKED


def test_the_block_is_read_from_verification_gates_yaml(tmp_path):
    """The NEW home. This is the half that did not work before the repoint: once
    plugin's csp 1.2 moves the block, this reader would have found nothing and
    reported a program with a configured ladder as having none."""
    report = security_gates.evaluate(_program(tmp_path, gates_file=GATES))

    assert not report.error
    assert [r.repo for r in report.repos] == ["otaman-cli"]
    assert report.repos[0].verdict != security_gates.NOT_CHECKED


def test_the_new_home_wins_when_both_declare_it(tmp_path):
    """Precedence is core's, not re-derived here — asserted so a later edit cannot
    quietly invert it during the migration window. The two homes declare DIFFERENT
    layers, so which one won is visible in the resolved ladder."""
    root = _program(
        tmp_path,
        platform_extra="security-gates:\n  languages:\n    python:\n      ci-fast:\n"
        "        tools: [gitleaks]\n",
        gates_file="security-gates:\n  languages:\n    python:\n      ci-medium:\n"
        "        tools: [semgrep]\n",
    )
    report = security_gates.evaluate(root)

    assert not report.error
    assert report.repos[0].layers == ("ci-medium",), (
        "the new home must win: this resolved the platform.yaml block instead"
    )


def test_the_call_site_does_not_choose_the_home(tmp_path):
    """Structural: the whole point of the ask was that this side stops knowing."""
    src = Path(security_gates.__file__).read_text(encoding="utf-8")
    assert "resolve_security_gates" in src


# ---------------------------------------------------------------------------
# What was deliberately NOT delegated.


def test_nothing_declared_anywhere_is_not_a_finding(tmp_path):
    """core's resolver returns an empty config here, which is the same value as a
    ladder declared empty. Those are different facts, so presence is established
    before the parse."""
    report = security_gates.evaluate(_program(tmp_path))

    assert not report.error
    assert report.repos == [], "no ladder declared is nothing to report, not a failure"


def test_a_declared_but_empty_ladder_is_reported_not_silent(tmp_path):
    """The other side of the same distinction: the block EXISTS, so every repo is
    reported — as not-checked, naming why."""
    report = security_gates.evaluate(_program(tmp_path, platform_extra="security-gates: {}\n"))

    assert not report.error
    assert [r.repo for r in report.repos] == ["otaman-cli"]
    assert report.repos[0].verdict == security_gates.NOT_CHECKED


def test_an_unreadable_new_home_is_not_checked_not_absent(tmp_path):
    """A verification-gates.yaml that exists and will not parse must not read as "no
    gates declared" — the fail-open family, at the file the block is moving INTO."""
    report = security_gates.evaluate(_program(tmp_path, gates_file="security-gates: [: : :\n"))

    assert report.error is not None
    assert "could not be read" in report.error
    assert report.repos == []


def test_an_absent_new_home_is_not_an_error(tmp_path):
    """Mid-migration, verification-gates.yaml simply does not exist yet. That is the
    common case and must stay silent."""
    gate_config, error = security_gates._gate_config(_program(tmp_path))

    assert gate_config is None and error is None


def test_a_bundle_predating_the_resolver_still_reads_platform_yaml(tmp_path, monkeypatch):
    """Attribute-probe adoption, not a version pin: an older core has no
    `resolve_security_gates`, and the pre-migration home still works."""
    import otaman_core.security_gates as core

    monkeypatch.delattr(core, "resolve_security_gates", raising=False)
    report = security_gates.evaluate(_program(tmp_path, platform_extra=GATES))

    assert not report.error
    assert [r.repo for r in report.repos] == ["otaman-cli"]


# ---------------------------------------------------------------------------
# core #100 — the attestation format has one writer.


def test_the_v_act_no_longer_composes_the_attestation_string(tmp_path):
    """#229 built the string here because core had no writer. It does now, and a
    format with two writers is one a stricter gate parse can drift from."""
    src = Path(__import__("otaman_cli.console.artifacts", fromlist=["x"]).__file__).read_text(
        encoding="utf-8"
    )

    assert 'updated["approved_by"] =' not in src, "the composition moved to core #100"
    assert "attested_at=" in src, "the act still owns the timestamp"


def test_core_writes_both_signals_and_satisfies_its_own_gate():
    """The repoint is only safe because core's writer produces what the merge gate
    reads — asserted here against core directly, so a core change that broke it
    fails on my side too rather than silently degrading the v-act."""
    from otaman_core.human_roster import HumanRosterEntry
    from otaman_core.spec_lifecycle import SpecPolicy, apply_spec_approved, check_merge_gate

    approver = HumanRosterEntry(name="roman", roles=["founder"])
    updated = apply_spec_approved(
        {"stage": "authored", "scr_approved_by": "roman"},
        approver,
        attested_at="2026-10-01T15:00:00Z",
    )

    assert updated["stage"] == "spec-approved"
    assert "roman" in str(updated.get("approved_by", ""))
    decision = check_merge_gate(updated, SpecPolicy(enforcement="block"))
    assert decision.allowed, (
        "core's writer must satisfy core's gate, or the v-act re-breaks CI the way "
        f"it did on 2026-10-01: {decision.violations}"
    )


def test_the_timestamp_passed_is_the_acts_own(tmp_path):
    """`attested_at` is passed rather than left to core because the timestamp belongs
    to the moment the human approved, not the moment core was called."""
    from otaman_core.human_roster import HumanRosterEntry
    from otaman_core.spec_lifecycle import apply_spec_approved

    stamped = "2026-01-02T03:04:05Z"
    updated = apply_spec_approved(
        {"stage": "authored"},
        HumanRosterEntry(name="roman", roles=["founder"]),
        attested_at=stamped,
    )

    assert stamped in str(updated["approved_by"])
