"""cli's wrapper and the core-invokable gate must agree (rnsc 1.3).

Every sibling repo's CI runs ``python -m otaman_core.changelog_fragment --check``
while humans run ``otaman policy check-changelog``. If those two ever disagree, a
PR passes locally and is blocked in CI (or worse, the reverse) and the discipline
stops being believable. 1.3 asks cli to CONFIRM the parity rather than assert it
in a docstring, so these compare actual verdicts over the same inputs.

The one legitimate divergence is pinned at the bottom: the core gate uses core's
shipped standard rules because a sibling's CI has no otaman project to resolve a
policy from, so a repo that OVERRIDES the fragment rules in its own policy will
see different answers. That is by design — the test exists so nobody later
"fixes" it into silence.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

pytest.importorskip("otaman_core.changelog_fragment")

from otaman_core.changelog_fragment import evaluate  # noqa: E402
from otaman_core.policy import GIT_STANDARD_RULES  # noqa: E402

#: (label, changed paths, PR body) — the cases a merge gate actually sees.
CASES = [
    ("shipped code, no fragment", ["src/otaman_cli/main.py"], ""),
    ("shipped code with a fragment", ["src/otaman_cli/main.py", "changelog.d/1.fix.md"], ""),
    ("docs only", ["README.md", "docs/guide.md"], ""),
    ("tests only", ["tests/test_x.py"], ""),
    ("shipped code, exempted", ["src/otaman_cli/main.py"], "changelog: exempt"),
    ("mixed shipped + docs, no fragment", ["src/a.py", "README.md"], ""),
    ("workflow only", [".github/workflows/test.yml"], ""),
]


def _core_gate(tmp_path, paths, body):
    """Run the gate the siblings wire, exactly as CI invokes it."""
    body_file = tmp_path / "pr-body.txt"
    body_file.write_text(body, encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "otaman_core.changelog_fragment",
            "--check",
            "--json",
            "--pr-body-file",
            str(body_file),
            "--paths",
            *paths,
        ],
        capture_output=True,
        text=True,
    )
    return proc.returncode, json.loads(proc.stdout)


def _cli_wrapper(monkeypatch, capsys, tmp_path, paths, body):
    """Run `otaman policy check-changelog`, stubbing ONLY policy resolution.

    The stub stands in for an unoverridden repo — which is the precondition the
    parity claim is scoped to. Everything downstream (argument handling, path
    collection, exemption reading, the verdict, the JSON shape) is the real
    command, so this compares the shipped code path rather than a re-implementation.
    """
    from otaman_cli.commands import policy as P

    monkeypatch.setattr(P, "_load_context", lambda: (tmp_path, {}))
    monkeypatch.setattr(
        P,
        "effective_policy",
        lambda *a, **k: (type("E", (), {"rules": dict(GIT_STANDARD_RULES)})(), []),
        raising=False,
    )
    import otaman_core.policy as core_policy

    monkeypatch.setattr(
        core_policy,
        "effective_policy",
        lambda *a, **k: (type("E", (), {"rules": dict(GIT_STANDARD_RULES)})(), []),
    )
    body_file = tmp_path / "cli-body.txt"
    body_file.write_text(body, encoding="utf-8")
    argv = ["check-changelog", "--json", "--pr-body-file", str(body_file), "--paths", *paths]
    rc = P.cmd_policy(argv)
    return rc, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("label,paths,body", CASES, ids=[c[0] for c in CASES])
def test_wrapper_and_core_gate_agree(label, paths, body, tmp_path, monkeypatch, capsys):
    core_rc, core_json = _core_gate(tmp_path, paths, body)
    cli_rc, cli_json = _cli_wrapper(monkeypatch, capsys, tmp_path, paths, body)

    assert cli_rc == core_rc, f"{label}: exit codes differ"
    for key in ("ok", "required", "exempted", "shipped_files", "fragments"):
        assert cli_json[key] == core_json[key], f"{label}: {key} differs"


def test_cli_evaluates_with_cores_function_not_a_copy():
    """The shim re-exports core's object itself — not a same-named local one."""
    import otaman_cli.changelog_fragment as shim

    assert shim.evaluate is evaluate


def test_refusal_exit_code_matches_between_the_two(tmp_path, monkeypatch, capsys):
    """A blocked merge must block with the same status on both paths."""
    core_rc, _ = _core_gate(tmp_path, ["src/a.py"], "")
    cli_rc, _ = _cli_wrapper(monkeypatch, capsys, tmp_path, ["src/a.py"], "")
    assert core_rc == cli_rc == 3


def test_overridden_rules_legitimately_diverge(tmp_path):
    """The documented limit of the parity claim, pinned so it stays deliberate.

    A repo that renames its fragment directory in its own policy gets a different
    verdict from the core gate, which uses the shipped standard rules. Siblings
    therefore must NOT override these keys while the core gate is their CI check.
    """
    paths = ["src/a.py", "notes/1.fix.md"]
    standard = evaluate(paths, dict(GIT_STANDARD_RULES), pr=1, exemption_text="")
    overridden = evaluate(
        paths,
        {**GIT_STANDARD_RULES, "changelog_fragment": {"dir": "notes"}},
        pr=1,
        exemption_text="",
    )
    assert standard.ok is False and overridden.ok is True
