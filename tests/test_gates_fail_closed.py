"""Gates fail CLOSED on unreadable input.

spec-agent reported the class against plugin's dispatch gate (20260930T084339):
an unquoted colon made `.openspec.yaml` unparseable, the parse returned nothing,
and dispatch PROCEEDED — "the gate you built refuses a READABLE authored stage
but waves through an UNREADABLE one." They suggested everyone check their own
gates for the pattern. These are the two it found here.

The root cause is one API returning `{}` for two opposite conditions. An ABSENT
`.openspec.yaml` means no gate is declared — there is nothing to verify. An
UNPARSEABLE one means the gate could not be evaluated. Core provides
`openspec_is_unreadable` precisely to tell them apart.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import otaman_cli.commands.spec as sp
from otaman_cli.commands.doctor import _version_controlled


class _Policy:
    process_level = "outcomes"
    enforcement = "block"


@pytest.fixture
def change(tmp_path, monkeypatch):
    d = tmp_path / "changes" / "demo"
    d.mkdir(parents=True)
    monkeypatch.setattr(sp, "_change_dir", lambda root, name: d)
    monkeypatch.setattr(sp, "_load_policy", lambda root, at=None: _Policy())
    return tmp_path, d


# ---------------------------------------------------------------------------
# the dispatch gate


def test_an_unparseable_openspec_refuses_dispatch(change):
    """THE defect: measured before the fix, this returned allowed=True while
    the same file made readable and authored returned False."""
    root, d = change
    # spec-agent's own error class — an unquoted colon inside a value.
    (d / ".openspec.yaml").write_text("stage: authored\nowner: roman: yes\n", encoding="utf-8")
    allowed, notes = sp.dispatch_gate_check(root, "demo")
    assert allowed is False
    assert any("does not parse" in n for n in notes), "the refusal must name the real reason"


def test_the_refusal_names_the_file_not_a_missing_approval(change):
    """Under a blocking policy this refused even before the fix — but as
    "approved_by missing", which sends the operator to add an approval rather
    than fix the YAML."""
    root, d = change
    (d / ".openspec.yaml").write_text("stage: authored\nowner: a: b\n", encoding="utf-8")
    _, notes = sp.dispatch_gate_check(root, "demo")
    joined = " ".join(notes)
    assert ".openspec.yaml" in joined
    assert "approved_by" not in joined


def test_an_absent_openspec_is_still_ungated(change):
    """The opposite condition, and it must keep its opposite answer: nothing
    declared is not something to refuse."""
    root, _ = change
    assert sp.dispatch_gate_check(root, "demo")[0] is True


def test_a_readable_authored_change_is_still_refused(change):
    """The behaviour that already worked must not regress while fixing the
    one that did not."""
    root, d = change
    (d / ".openspec.yaml").write_text("stage: authored\n", encoding="utf-8")
    assert sp.dispatch_gate_check(root, "demo")[0] is False


def test_a_readable_approved_change_still_passes(change):
    root, d = change
    (d / ".openspec.yaml").write_text(
        "stage: spec-approved\napproved_by: roman 2026-09-30\n", encoding="utf-8"
    )
    allowed, _ = sp.dispatch_gate_check(root, "demo")
    assert allowed is True


def test_an_unimportable_core_refuses_rather_than_permits(change, monkeypatch):
    """An unperformed check never renders OK. Core is a hard dependency; if the
    lifecycle module is gone the gate has not run, and "allowed" would be a
    false green on the surface whose whole job is refusing."""
    root, d = change
    (d / ".openspec.yaml").write_text("stage: authored\n", encoding="utf-8")
    import builtins

    real_import = builtins.__import__

    def boom(name, *a, **k):
        if name == "otaman_core.spec_lifecycle":
            raise ImportError("gone")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", boom)
    allowed, notes = sp.dispatch_gate_check(root, "demo")
    assert allowed is False
    assert any("cannot verify" in n for n in notes)


# ---------------------------------------------------------------------------
# the doctor's version-control check


def test_a_real_repo_is_version_controlled():
    assert _version_controlled(Path(__file__).parent) is True


def test_a_plain_directory_is_not(tmp_path):
    assert _version_controlled(tmp_path) is False


def test_an_unwalkable_path_is_unknown_not_fine(monkeypatch, tmp_path):
    """Returning True here reported a repo as version-controlled on the
    strength of a read that FAILED — the same shape as the incident this check
    exists for, where four of five haulops repos sat unversioned for three
    weeks while doctor printed OK for each."""

    class Exploding(type(tmp_path)):
        pass

    def boom(self):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "exists", boom)
    assert _version_controlled(tmp_path) is None


def test_unverifiable_is_rendered_as_a_failure_not_a_pass():
    """Tri-state is only worth having if the caller renders the third state."""
    import inspect

    from otaman_cli.commands import doctor

    src = inspect.getsource(doctor)
    assert '"unverifiable"' in src
    assert "could not verify version control" in src
    assert "this is NOT a pass" in src
