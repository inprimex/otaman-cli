"""delivery-authorization-envelope 2.2 — propose, review, and the D6 measurement.

Core owns the vocabulary (class registry, floor, well-formedness). These are the
three places a human meets it: authoring one, seeing one, and finding out
whether declaring a class buys anything at runtime.

The measurement is the part worth being careful about. An envelope that promises
autonomy the runtime refuses is worse than no envelope: the agent proceeds
believing it is authorized, hits a prompt nobody is watching, and freezes —
which is the halt this whole change exists to remove, with a promise attached.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_cli import envelope as E

#: The SCR template refuses without every decision-grade section, which is a
#: separate gate (generated-artifact-quality 1.1) and not what these tests are
#: about. Filled once so the envelope is the only thing under test.
_SECTIONS = [
    "--problem",
    "observed a thing",
    "--evidence",
    "file.py:1",
    "--impact",
    "one repo, sometimes",
    "--direction",
    "somewhere",
    "--scope",
    "not everything",
    "--routing",
    "otaman-cli, cli-agent",
    "--workaround",
    "n/a because nothing is needed",
]


def _program(tmp_path, monkeypatch):
    from otaman_cli.commands import propose_team as P

    root = tmp_path / "meta"
    (root / ".agents" / "bus" / "active").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text("project: demo\n", encoding="utf-8")
    monkeypatch.setattr(P, "find_project_root", lambda: root)
    return P, root


def _scr(root):
    """The spec-change-request file — propose also writes an approval-pending
    companion, and picking the first glob hit gets the wrong one."""
    hits = list((root / ".agents" / "bus" / "active").glob("*spec-change-request*.md"))
    assert hits, "no SCR was written"
    return hits[0]


# ---------------------------------------------------------------------------
# authoring


def test_a_bare_class_parses():
    assert E.parse_cli_classes(["hooks-wiring"]) == ["hooks-wiring"]


def test_a_scoped_class_parses_to_cores_shape():
    assert E.parse_cli_classes(["release-publish:otaman-deploy|otaman-cli"]) == [
        {"release-publish": ["otaman-deploy", "otaman-cli"]}
    ]


def test_one_flag_can_carry_several():
    assert E.parse_cli_classes(["hooks-wiring,service-restart"]) == [
        "hooks-wiring",
        "service-restart",
    ]


def test_a_known_class_validates():
    envelope, error = E.validate(["hooks-wiring"])
    assert error == "" and "hooks-wiring" in envelope


def test_a_floor_action_refuses_and_names_the_floor():
    """The proposer has to know WHICH of the six they touched, not merely that
    something was rejected."""
    _, error = E.validate(["act-as-human"])
    assert "floor" in error and "act-as-human" in error


def test_a_floor_action_refuses_even_when_scoped():
    """ "However scoped" is the spec's wording — narrowing a floor action does
    not make it pre-authorizable."""
    _, error = E.validate(["history-rewrite:one-repo"])
    assert "floor" in error


def test_an_unknown_class_refuses_as_canon():
    _, error = E.validate(["invented-class"])
    assert "unknown action class" in error


def test_propose_refuses_a_floor_envelope_before_writing_anything(tmp_path, monkeypatch, capsys):
    """A refusal after the message is on the bus would be a floor action
    recorded as requested."""
    P, root = _program(tmp_path, monkeypatch)
    rc = P.cmd_propose(["--authorizes", "act-as-human", *_SECTIONS, "a", "title"])
    assert rc == 2
    assert not list((root / ".agents" / "bus" / "active").glob("*.md")), "nothing may be written"
    assert "floor" in capsys.readouterr().out


def test_propose_accepts_a_valid_envelope(tmp_path, monkeypatch):
    P, root = _program(tmp_path, monkeypatch)
    P.cmd_propose(["--authorizes", "hooks-wiring", *_SECTIONS, "a", "title"])
    assert "hooks-wiring" in _scr(root).read_text(encoding="utf-8")


def test_the_envelope_travels_in_the_body_where_a_reviewer_reads_it(tmp_path, monkeypatch):
    """An envelope in frontmatter nobody reads is an envelope nobody reviewed."""
    P, root = _program(tmp_path, monkeypatch)
    P.cmd_propose(["--authorizes", "hooks-wiring", *_SECTIONS, "a", "title"])
    body = _scr(root).read_text(encoding="utf-8").split("---", 2)[-1]
    assert "## Authorizes" in body


# ---------------------------------------------------------------------------
# rendering at review


def test_an_empty_envelope_says_it_authorizes_nothing():
    assert "authorizes nothing" in " ".join(E.render_lines({})).lower() or "nothing" in " ".join(
        E.render_lines({})
    )


def test_a_rendered_class_shows_its_runtime_marker():
    """A reviewer approving `release-publish` is approving something that today
    authorizes nothing at runtime. Hiding that makes the approval mean less
    than it appears to."""
    line = " ".join(E.render_lines({"release-publish": None}))
    assert "release-publish" in line and "runtime-honored" in line


def test_a_scoped_class_shows_its_targets():
    line = " ".join(E.render_lines({"release-publish": ["otaman-deploy"]}))
    assert "otaman-deploy" in line


def test_propose_and_review_refuse_the_same_things():
    """A change that could be authored but not approved would be a trap."""
    _, from_cli = E.validate(["act-as-human"])
    _, from_yaml = E.validate_raw(["act-as-human"])
    assert "floor" in from_cli and "floor" in from_yaml


# ---------------------------------------------------------------------------
# D6 — measurement, not assumption


def test_no_class_is_certified_from_a_single_surface(tmp_path):
    """THE property. This probe sees one runtime surface, so it can falsify
    ("the guard stops it") but never certify ("nothing does"). Certifying on one
    negative would put `runtime-honored: yes` on a class the runtime may still
    stop."""
    results = E.measure(tmp_path)
    assert results, "the measurement must report something"
    assert all(r["verdict"] == "limited" for r in results.values())


def test_the_scope_of_the_measurement_is_stated():
    """An unstated scope reads as a full audit."""
    scope = E.MEASUREMENT_SCOPE
    assert "ONE runtime surface" in scope
    assert "cannot certify" in scope


def test_a_missing_guard_measures_nothing_rather_than_passing(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "_guard_script", lambda root: None)
    results = E.measure(tmp_path)
    assert all(r["verdict"] == "limited" for r in results.values())
    assert any("nothing measured" in r["evidence"] for r in results.values())


def test_a_probe_that_cannot_run_is_not_a_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "_guard_script", lambda root: Path("/nonexistent/guard.sh"))
    monkeypatch.setattr(E, "_guard_would_ask", lambda script, cmd: None)
    results = E.measure(tmp_path)
    assert all(r["verdict"] == "limited" for r in results.values())


def test_a_blocked_class_is_distinguished_from_an_unblocked_one(tmp_path, monkeypatch):
    """Same verdict, different evidence — which is what a later probe of the
    other surfaces builds on."""
    monkeypatch.setattr(E, "_guard_script", lambda root: Path("/x/guard.sh"))
    monkeypatch.setattr(E, "_guard_would_ask", lambda script, cmd: "release" in cmd)
    results = E.measure(tmp_path)
    blocked = [n for n, r in results.items() if "BLOCKED" in r["evidence"]]
    assert "release-publish" in blocked


def test_the_registry_is_cores_to_change_not_mine():
    """CLASS_REGISTRY lives in otaman-core, which is read-only here. Measuring
    is cli's job; RECORDING the result is a core change."""
    src = Path(E.__file__).read_text(encoding="utf-8")
    assert "CLASS_REGISTRY[" not in src, "cli must not write core's registry"


@pytest.mark.parametrize("cls", sorted(E._PROBES))
def test_every_registry_class_has_a_probe(cls):
    """A class with no probe is a class silently left unmeasured."""
    from otaman_core.delivery_envelope import CLASS_REGISTRY

    assert set(CLASS_REGISTRY) <= set(E._PROBES), (
        f"unprobed classes: {sorted(set(CLASS_REGISTRY) - set(E._PROBES))}"
    )
