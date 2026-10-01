"""kv2 2.3 — what `--domain` says about a term it could not check.

spec-agent's kv2 gate on v0.5.17 (20261001T142250) reported `knowledge add
--domain nonexistent-term` recording with NO statement about the domain, against
the ratified accept-and-state ruling. I could not reproduce the silence: the
not-validated line prints on `main` and the installed v0.5.17 carries the same
code. What I DID find, chasing it, is the defect underneath — and their second
observation ("output showed the function field, nothing for domain") was exact.

**`known_terms` collapsed three facts into `None`**, contradicting its own
docstring, which said `None` and `set()` were deliberately distinct:

  * no registry file                        -> accept, say the term is unvalidated
  * a registry that declares nothing        -> REFUSE: the term is genuinely undeclared
  * a registry that exists and won't parse  -> accept, say it is unreadable, NOT absent

All three produced "no vocabulary registry at <path>" — false for the last two,
where the file is sitting right there. That is the fail-open family spec-agent
found in plugin's dispatch gate and I then found twice in mine (cli #223): absent
and unreadable mean opposite things to the reader, who must declare a term in one
case and fix a file in the other.
"""

from __future__ import annotations

import pytest

pytest.importorskip("otaman_core.knowledge")

from otaman_cli import vocabulary  # noqa: E402
from otaman_cli.commands.knowledge import cmd_knowledge  # noqa: E402


@pytest.fixture
def program(isolate_bus, monkeypatch, tmp_path):
    root = isolate_bus
    (root / ".agents").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(root)
    monkeypatch.setenv("OTAMAN_AGENT", "cli-agent")
    # The registry home is a sibling strategy repo; point it somewhere writable so
    # each case can put a different file there (or none).
    home = tmp_path / "strategy"
    home.mkdir()
    monkeypatch.setattr(vocabulary, "registry_path", lambda _root: home / "vocabulary.yaml")
    return root, home / "vocabulary.yaml"


# ---------------------------------------------------------------------------
# the three facts `None` used to hide


def test_no_registry_accepts_and_says_the_term_was_not_validated(program):
    root, registry = program
    assert not registry.exists()

    terms, reason = vocabulary.read_terms(root)
    assert terms is None and reason == vocabulary.ABSENT

    ok, note = vocabulary.check_domain(root, "logistics")
    assert ok is True
    assert "NOT validated" in note
    assert "no vocabulary registry" in note
    assert str(registry) in note, "the note must name where a registry would live"


def test_a_registry_that_declares_nothing_refuses_the_term(program):
    """THE DEFECT. An empty registry is a CHECKED registry — the term is genuinely
    undeclared. This accepted it as unvalidated and reported the file as absent."""
    root, registry = program
    registry.write_text("vocabulary: []\n", encoding="utf-8")

    terms, reason = vocabulary.read_terms(root)
    assert terms == set(), "an empty registry is `set()`, not None"
    assert reason == ""

    ok, note = vocabulary.check_domain(root, "logistics")
    assert ok is False, "a declared-nothing registry must refuse an undeclared term"
    assert "not in the program vocabulary" in note
    assert "(none declared)" in note


@pytest.mark.parametrize("body", ["", "   \n\n", "# nothing declared yet\n"])
def test_a_file_that_declares_nothing_is_checked_not_broken(program, body):
    """Same ruling as cli #200: an empty FILTER was not an empty SURFACE. A
    comments-only file parses clean and holds nothing — that is a registry
    declaring nothing, which is a CHECK, not a failure to check."""
    root, registry = program
    registry.write_text(body, encoding="utf-8")

    terms, reason = vocabulary.read_terms(root)
    assert terms == set() and reason == ""


def test_an_unreadable_registry_says_unreadable_and_not_absent(program):
    """Accept-and-state, with the reason named: "no registry" and "the registry is
    there and would not parse" call for different actions by the reader."""
    root, registry = program
    registry.write_text("vocabulary: [: : :\n  - broken\n", encoding="utf-8")

    terms, reason = vocabulary.read_terms(root)
    assert terms is None and reason == vocabulary.UNREADABLE, (
        "the STRICT loader is required here: `yaml_fast.load_file` returns None "
        "for malformed YAML instead of raising, so it cannot tell a broken "
        "registry from a comments-only one"
    )

    ok, note = vocabulary.check_domain(root, "logistics")
    assert ok is True, "unreadable must not refuse — the term may well be declared"
    assert "could not be read" in note
    assert "not a missing-registry case" in note
    assert "no vocabulary registry" not in note, "the old sentence was false here"


@pytest.mark.parametrize(
    "body",
    [
        "vocabulary: [: : :\n  - broken\n",
        "vocabulary:\n\t- id: x\n",  # a tab, which YAML forbids for indentation
        "vocabulary: [\n  - id: x\n",  # unclosed flow sequence
        "just a bare string\n",  # parses, but is not a registry shape
    ],
)
def test_every_unreadable_shape_is_unreadable_not_absent(program, body):
    """Each of these came back as `None` from the fast loader, i.e. silently
    indistinguishable from an empty file."""
    root, registry = program
    registry.write_text(body, encoding="utf-8")

    terms, reason = vocabulary.read_terms(root)
    assert terms is None and reason == vocabulary.UNREADABLE


def test_a_declared_term_is_accepted_silently(program):
    root, registry = program
    registry.write_text("vocabulary:\n  - id: logistics\n    title: Logistics\n", encoding="utf-8")

    ok, note = vocabulary.check_domain(root, "logistics")
    assert ok is True and note == "", "a validated term needs no commentary"


def test_the_match_is_case_insensitive(program):
    root, registry = program
    registry.write_text("vocabulary:\n  - id: Logistics\n", encoding="utf-8")

    assert vocabulary.check_domain(root, "LOGISTICS")[0] is True


def test_an_undeclared_term_names_what_is_declared(program):
    root, registry = program
    registry.write_text("vocabulary:\n  - id: logistics\n  - id: haulage\n", encoding="utf-8")

    ok, note = vocabulary.check_domain(root, "fintech")
    assert ok is False
    assert "haulage" in note and "logistics" in note
    assert "omit --domain" in note, "a refusal must say how to satisfy it"


def test_known_terms_still_works_for_callers_that_ignore_the_reason(program):
    root, registry = program
    registry.write_text("vocabulary:\n  - id: logistics\n", encoding="utf-8")
    assert vocabulary.known_terms(root) == {"logistics"}


# ---------------------------------------------------------------------------
# the surface — spec-agent's second observation, which was exact


def test_the_not_validated_statement_reaches_the_operator(program, capsys):
    """The ratified accept-and-state ruling, end to end through the command."""
    root, _ = program
    rc = cmd_knowledge(
        [
            "add",
            "--type",
            "lesson",
            "--title",
            "a claim",
            "--anchor",
            "loader.py:41",
            "--body",
            "x",
            "--domain",
            "nonexistent-term",
        ]
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "Recorded:" in out
    assert "NOT validated" in out, "silence here is the branch the ruling forbids"


def test_the_recorded_domain_is_echoed_in_the_confirmation(program, capsys):
    """spec-agent: "output showed the function field, nothing for domain" — and so
    had the flag's value even been consumed? It had. A recorded field the
    confirmation does not mention is indistinguishable from a dropped one, and
    `function` sitting right beside it made the omission look deliberate."""
    root, registry = program
    registry.write_text("vocabulary:\n  - id: logistics\n", encoding="utf-8")
    rc = cmd_knowledge(
        [
            "add",
            "--type",
            "lesson",
            "--title",
            "a claim",
            "--anchor",
            "loader.py:41",
            "--body",
            "x",
            "--domain",
            "logistics",
        ]
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "domain logistics" in out


def test_no_domain_adds_no_domain_noise(program, capsys):
    """The echo must not put an empty field on the line for every entry."""
    root, _ = program
    rc = cmd_knowledge(
        ["add", "--type", "lesson", "--title", "a claim", "--anchor", "loader.py:41", "--body", "x"]
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "domain" not in out.lower()


def test_an_unrecognised_anchor_warns_and_names_the_shapes(program, capsys):
    """spec-agent's minor note. The leniency is defensible — an anchor exists, its
    shape is unrecognised — but "not a recognised shape" without saying WHICH
    shapes are recognised tells the author that something is wrong and nothing
    about what would be right."""
    root, _ = program
    rc = cmd_knowledge(
        ["add", "--type", "lesson", "--title", "a claim", "--anchor", "somewhere", "--body", "x"]
    )
    out = capsys.readouterr().out

    assert rc == 0, "the hard rule is presence, so this records"
    assert "not a recognised shape" in out
    assert "file:line" in out
    assert "bus message stem" in out
    assert "measured number" in out
