"""`.agents/current-agent` is RETIRED — resolver included, and doctor errors on it.

team-mode D3/B1 as AMENDED (Roman ruling 2026-09-11, 20260911T113813):

    "CUTOVER — NO dual-read window: the release shipping B1 carries an explicit
     migration step that removes the marker, doctor ERRORs if one reappears, and the
     generated CLAUDE.local.md First-Session step 0 is deleted by the same release."

cli shipped the cutover's *intent* but kept a deprecated step 5 in
`resolve_agent_identity` that still read the marker when steps 1-4 came up empty. That is
a dual-read window, and the resolver is the component that converts one stale shared file
into a wrong identity — the last-writer-wins class B1 removed. spec-agent ruled it
non-conformance (20261003T130553): delete the step, and ship doctor's half, which never
had.

The cost was live, not theoretical. On 2026-10-03 the marker read `fswatch-agent`
(written 10-01); plugin's post-commit hook read that same file in every repo, so two days
of fleet-wide commits were attributed to an agent that had committed nothing
(20261003T121802).
"""

from __future__ import annotations

import inspect

import pytest

from otaman_cli import identity
from otaman_cli.doctor import check_retired_identity_marker


@pytest.fixture
def program(tmp_path, monkeypatch):
    root = tmp_path / "meta"
    (root / ".agents").mkdir(parents=True)
    (root / "platform.yaml").write_text(
        "project: p\nversion: '1.0'\nrepos:\n  - name: a\n    path: ../a\n    owner: a-agent\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OTAMAN_AGENT", raising=False)
    return root


# ---------------------------------------------------------------------------
# the resolver no longer reads it


def test_the_marker_is_NOT_consulted_even_when_nothing_else_resolves(program, tmp_path):
    """The dual-read window. Steps 1-4 all come up empty here — explicit arg absent,
    OTAMAN_AGENT unset, cwd outside any repo, no `.otaman` field — which is precisely
    the condition under which step 5 used to answer.
    """
    (program / ".agents" / "current-agent").write_text("a-agent\n", encoding="utf-8")
    outside = tmp_path / "nowhere"
    outside.mkdir()

    resolved = identity.resolve_agent_identity(program, cwd=outside)

    assert resolved is None, (
        f"resolver consumed the retired marker and returned {resolved!r} — "
        "that is the dual-read window the cutover forbids"
    )


def test_a_marker_naming_a_DECLARED_agent_is_still_ignored(program, tmp_path):
    """The old step validated the name against platform.yaml and trusted it if it
    matched. Validation was never the point: a *valid* name written by the wrong
    session is exactly the last-writer-wins failure."""
    (program / ".agents" / "current-agent").write_text("a-agent\n", encoding="utf-8")
    outside = tmp_path / "nowhere"
    outside.mkdir()

    assert identity.resolve_agent_identity(program, cwd=outside) is None


def test_no_code_path_in_the_resolver_reads_the_marker():
    """Checked against source, because a reader reintroduced anywhere in the chain
    re-opens the window — it does not have to be step 5 to do that."""
    import ast

    tree = ast.parse(inspect.getsource(identity))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    live = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "current-agent" in node.value
        and id(node) not in docstrings
    ]

    assert not live, (
        "the retired marker appears as a live string literal (a path segment or a "
        f"read) at line(s) {live} — prose in a docstring is fine, code is not"
    )


def test_the_documented_chain_does_not_advertise_it():
    """An operator reading the docstring must not be told a retired file is consulted;
    that is how plugin's hook came to read it in the first place."""
    doc = identity.resolve_agent_identity.__doc__ or ""

    assert "RETIRED" in doc
    assert "5. None" in doc, "step 5 is now the terminal 'nothing found' case"


# ---------------------------------------------------------------------------
# doctor's half of the same clause


def test_doctor_FAILS_when_the_marker_reappears(program):
    (program / ".agents" / "current-agent").write_text("fswatch-agent\n", encoding="utf-8")

    result = check_retired_identity_marker(program)

    assert result["status"] == "fail", "warn would let a cutover violation pass a health check"
    (issue,) = result["issues"]
    assert issue["severity"] == "high"
    assert "fswatch-agent" in issue["message"], "name what it claims, not just that it exists"
    assert "whoami --for-path" in issue["fix"], "point at the surface that replaced it"


def test_doctor_names_the_WRITER_as_the_defect_not_just_the_file(program):
    """Deleting the file fixes nothing if a hook recreates it next commit — which is
    exactly what happened: plugin's post-commit hook was the writer."""
    (program / ".agents" / "current-agent").write_text("x-agent\n", encoding="utf-8")

    (issue,) = check_retired_identity_marker(program)["issues"]

    assert "writer" in issue["fix"].lower()


def test_doctor_is_quiet_when_the_marker_is_absent(program):
    result = check_retired_identity_marker(program)

    assert result["status"] == "ok"
    assert "absent" in result["details"]["marker"]
    assert not result.get("issues")


def test_an_empty_or_unreadable_marker_is_still_a_FAILURE(program):
    """Present-but-empty is still present, and something still wrote it."""
    (program / ".agents" / "current-agent").write_text("# just a comment\n", encoding="utf-8")

    result = check_retired_identity_marker(program)

    assert result["status"] == "fail"
    assert result["details"]["contains"] == "<empty>"


def test_the_check_runs_in_the_real_doctor_sweep():
    """A check nobody calls is not a guard. Registered in `run_doctor`'s list."""
    from otaman_cli import doctor

    src = inspect.getsource(doctor.run_doctor)

    assert "check_retired_identity_marker(" in src
