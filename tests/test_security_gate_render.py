"""sghc 1.7, cli half — Hook C's report, read at review.

Task 1.7 is one line with two owners: plugin's resolution function emits the
report (plugin #98), this side renders it. plugin asked for one thing in
particular when they handed it over (20261001T104255):

    Blocking is NOT decided on my side. core's `is_blocked` is the single home,
    stated there as existing so the emitter and the renderer cannot disagree —
    so please derive the verdict from it rather than from the layer list, or we
    recreate exactly the drift it was written to prevent.

The first test below is the guard for that: it makes core's derivation disagree
with what reading the layer list would conclude, and requires the render to
follow core. A renderer that scanned the layers itself passes every other test
in this file and fails that one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from otaman_core.security_gate_report import (
    Disagreement,
    LayerVerdict,
    SecurityGateReport,
    Suppression,
    report_to_dict,
)

from otaman_cli import security_gate_render as sgr


def payload(**kwargs) -> str:
    """A serialized report, through core's own serializer."""
    report = SecurityGateReport(
        repo=kwargs.pop("repo", "otaman-cli"),
        pr=kwargs.pop("pr", "#231"),
        layers=kwargs.pop("layers", (LayerVerdict("pre-commit", "pass"),)),
        disagreements=kwargs.pop("disagreements", ()),
        suppressions=kwargs.pop("suppressions", ()),
    )
    assert not kwargs, kwargs
    return json.dumps(report_to_dict(report))


# --------------------------------------------------------------------------
# The verdict is core's, not ours.


def test_the_verdict_follows_core_even_when_the_layer_list_says_otherwise(monkeypatch):
    """The guard plugin asked for.

    Every layer passes, so a renderer deriving the verdict itself would say
    clear. Core says blocked. The render must say blocked — that is the proof
    it asked core instead of reading the layers.
    """
    import otaman_core.security_gate_report as core

    monkeypatch.setattr(core, "is_blocked", lambda report: True)
    loaded = sgr.load(payload(layers=(LayerVerdict("ci-fast", "pass"),)))
    lines = sgr.render_lines(loaded)

    assert "BLOCKED" in lines[0]
    assert sgr.exit_code(loaded) == sgr.BLOCKED


def test_a_clear_report_follows_core_the_same_way(monkeypatch):
    """The inverse: a failing layer, core overridden to clear. Still core's call."""
    import otaman_core.security_gate_report as core

    monkeypatch.setattr(core, "is_blocked", lambda report: False)
    loaded = sgr.load(payload(layers=(LayerVerdict("ci-fast", "fail", ("x",)),)))

    assert "clear" in sgr.render_lines(loaded)[0]
    assert sgr.exit_code(loaded) == sgr.CLEAR


def test_the_render_states_where_the_verdict_came_from():
    """A reviewer acting on BLOCKED is entitled to know nothing here decided it."""
    lines = sgr.render_lines(sgr.load(payload()))
    assert any("otaman_core.security_gate_report.is_blocked" in la for la in lines)


def test_an_unjustified_suppression_blocks_with_no_failing_layer():
    """core's rule, reported through: a bare marker fails the gate on its own."""
    raw = payload(
        layers=(LayerVerdict("ci-medium", "pass"),),
        suppressions=(Suppression("# nosemgrep", "src/x.py:12", False),),
    )
    loaded = sgr.load(raw)
    lines = sgr.render_lines(loaded)

    assert sgr.exit_code(loaded) == sgr.BLOCKED
    assert any("UNJUSTIFIED" in la for la in lines)
    assert any("1 unjustified suppression" in la for la in lines)


# --------------------------------------------------------------------------
# A payload that states its own verdict is checked against core's.


def test_a_payload_whose_stated_verdict_is_wrong_is_named():
    """`report_from_dict` discards `blocked` silently. An emitter that computed
    it itself and got it wrong would otherwise reach the reviewer unremarked."""
    data = json.loads(payload(layers=(LayerVerdict("ci-fast", "fail", ("secret",)),)))
    assert data["blocked"] is True  # core derived it correctly on the way out
    data["blocked"] = False  # an emitter that decided for itself
    loaded = sgr.load(json.dumps(data))

    drift = sgr.verdict_drift(loaded)
    assert drift is not None
    assert "blocked=false" in drift and "blocked=true" in drift
    assert any("!!" in la for la in sgr.render_lines(loaded))
    # core's verdict stands — the payload does not get a vote.
    assert sgr.exit_code(loaded) == sgr.BLOCKED


def test_an_agreeing_payload_raises_nothing():
    """The check must stay silent on every honest report, or it is noise."""
    assert sgr.verdict_drift(sgr.load(payload())) is None


def test_a_payload_with_no_stated_verdict_raises_nothing():
    data = json.loads(payload())
    del data["blocked"]
    assert sgr.verdict_drift(sgr.load(json.dumps(data))) is None


# --------------------------------------------------------------------------
# A layer that did not report is named, not omitted.


def test_layers_absent_from_the_report_are_rendered_as_not_reported():
    """A report carrying one passing layer must not read like a clean run."""
    from otaman_core.security_gates import LAYERS

    lines = sgr.render_lines(sgr.load(payload(layers=(LayerVerdict("pre-commit", "pass"),))))
    rendered = "\n".join(lines)

    for layer in LAYERS:
        assert layer in rendered, f"{layer} vanished from the report"
    assert rendered.count(sgr.NOT_REPORTED) == len(LAYERS) - 1


def test_not_reported_is_distinct_from_core_s_not_run():
    """`not-run` means the gate ran this layer and it did not execute;
    not-reported means the report says nothing about it. Different facts."""
    assert sgr.NOT_REPORTED != "not-run"
    lines = sgr.render_lines(sgr.load(payload(layers=(LayerVerdict("ci-slow", "not-run"),))))
    assert any(la.strip() == "- ci-slow: not-run" for la in lines)


def test_the_canonical_layer_order_is_kept():
    from otaman_core.security_gates import LAYERS

    scrambled = tuple(LayerVerdict(la, "pass") for la in reversed(LAYERS))
    lines = sgr.render_lines(sgr.load(payload(layers=scrambled)))
    seen = [la.strip().split(":")[0][2:] for la in lines if la.startswith("  - ")]
    assert seen == list(LAYERS)


def test_a_report_with_no_layers_at_all_says_so():
    lines = sgr.render_lines(sgr.load(payload(layers=())))
    assert any("the ladder did not run" in la for la in lines)


# --------------------------------------------------------------------------
# Unreadable is not clean.


@pytest.mark.parametrize(
    "raw,fragment",
    [
        ("", "empty payload"),
        ("   \n ", "empty payload"),
        ("not json", "not JSON"),
        ("[1, 2]", "not a security-gate-report object"),
        ('{"repo": ""}', "not a valid security-gate-report"),
        ('{"repo": "r", "layers": [{"layer": "nope", "verdict": "pass"}]}', "unknown layer"),
    ],
)
def test_every_unreadable_payload_refuses_rather_than_rendering_clean(raw, fragment):
    loaded = sgr.load(raw)
    assert loaded.report is None
    assert fragment in loaded.error
    assert sgr.exit_code(loaded) == sgr.CANNOT_RENDER
    assert "NOT EVALUATED" in sgr.render_lines(loaded)[0]


def test_a_bundle_without_the_record_cannot_render_and_does_not_pass(monkeypatch):
    """An install predating core #93 has no record to parse. That is
    not-evaluated, and reporting it as clean is the fail-open class."""
    monkeypatch.setattr(sgr, "_core", lambda: None)
    loaded = sgr.load(payload())

    assert loaded.report is None
    assert "security-gates-hook-c 1.6" in loaded.error
    assert sgr.exit_code(loaded) == sgr.CANNOT_RENDER


def test_the_cannot_render_code_is_not_the_clear_code():
    assert sgr.CANNOT_RENDER not in (sgr.CLEAR,)
    assert sgr.BLOCKED != sgr.CLEAR


# --------------------------------------------------------------------------
# Disagreements reach the human who triages them.


def test_a_disagreement_renders_as_a_triage_flag():
    """The spec's scenario: semgrep flags what the observer calls safe — the PR
    blocks on semgrep AND the dissent renders."""
    raw = payload(
        layers=(LayerVerdict("ci-medium", "fail", ("py.ssrf at loader.py:41",)),),
        disagreements=(Disagreement("py.ssrf at loader.py:41", "vulnerable", "safe", "ci-medium"),),
    )
    lines = sgr.render_lines(sgr.load(raw))
    rendered = "\n".join(lines)

    assert "the deterministic verdict stands" in rendered
    assert "deterministic: vulnerable" in rendered and "observer: safe" in rendered
    assert sgr.exit_code(sgr.load(raw)) == sgr.BLOCKED


def test_no_disagreement_section_when_there_are_none():
    assert not any("disagreement" in la for la in sgr.render_lines(sgr.load(payload())))


# --------------------------------------------------------------------------
# End to end, through the entry point a CI step actually invokes.


def _run(args: list[str], stdin: str = "") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    roots = [str(Path(__file__).resolve().parents[1] / "src")]
    if env.get("PYTHONPATH"):
        roots.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(roots)
    return subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", "review", "security-gate", *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )


def test_end_to_end_a_blocking_report_exits_blocked():
    raw = payload(layers=(LayerVerdict("ci-fast", "fail", ("history secret",)),))
    done = _run(["-"], raw)
    assert done.returncode == sgr.BLOCKED, done.stdout + done.stderr
    assert "BLOCKED" in done.stdout


def test_end_to_end_a_clear_report_exits_zero():
    done = _run(["-"], payload())
    assert done.returncode == sgr.CLEAR, done.stdout + done.stderr


def test_end_to_end_an_unreadable_file_exits_cannot_render(tmp_path):
    done = _run([str(tmp_path / "absent.json")])
    assert done.returncode == sgr.CANNOT_RENDER
    assert "NOT EVALUATED" in done.stdout
    assert "this is not a pass" in done.stdout


def test_end_to_end_a_file_payload_renders(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(payload(repo="otaman-plugin", pr="#98"), encoding="utf-8")
    done = _run([str(path)])
    assert done.returncode == sgr.CLEAR, done.stdout + done.stderr
    assert "otaman-plugin #98" in done.stdout


def test_end_to_end_no_argument_is_a_usage_error_not_a_pass():
    done = _run([])
    assert done.returncode == sgr.CANNOT_RENDER
    assert "Usage" in done.stdout


def test_bare_review_still_points_at_the_observer_agents():
    """The subtarget must not have swallowed the command it hangs off."""
    env = dict(os.environ)
    roots = [str(Path(__file__).resolve().parents[1] / "src")]
    if env.get("PYTHONPATH"):
        roots.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(roots)
    done = subprocess.run(
        [sys.executable, "-m", "otaman_cli.main", "review"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert done.returncode == 0
    assert "/otaman:review" in done.stdout
