"""knowledge-v2 2.2 / 2.3 — vault health and `domain:` validation.

The three signals fail in different directions and each has a way of being
useless rather than wrong:

* decay can retire a fact someone still relies on,
* out-of-band detection can fire on every legacy entry until nobody reads it,
* ownership can report "clean" from a check that never ran.

The tests below are aimed at those three, not at the happy path.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from otaman_core import knowledge as core

from otaman_cli import knowledge_health as health
from otaman_cli import vocabulary


def _entry(**kw):
    base = dict(
        type=core.KIND_LESSON,
        author="cli-agent",
        created="2026-01-01",
        review_by="2026-02-01",
        anchor="src/x.py:1",
        title="A fact",
        body="Body.",
    )
    base.update(kw)
    return core.KnowledgeEntry(**base)


@pytest.fixture
def vault(tmp_path):
    d = tmp_path / ".agents" / "knowledge"
    d.mkdir(parents=True)
    return d


# ---------------------------------------------------------------------------
# decay


def test_past_due_and_never_read_is_a_decay_candidate():
    e = _entry(review_by="2026-02-01", accessed_at="")
    assert health.is_unreinforced(e, "2026-03-01")


def test_a_read_after_the_due_date_reinforces_it():
    """Reading it once it came due is the evidence that keeps it active."""
    e = _entry(review_by="2026-02-01", accessed_at="2026-02-15")
    assert not health.is_unreinforced(e, "2026-03-01")


def test_a_read_BEFORE_the_due_date_does_not_reinforce_it():
    """The distinction the whole sweep rests on: an access from before the
    entry came due says nothing about whether it is still true now."""
    e = _entry(review_by="2026-02-01", accessed_at="2026-01-10")
    assert health.is_unreinforced(e, "2026-03-01")


def test_an_entry_still_in_date_is_never_swept():
    assert not health.is_unreinforced(_entry(review_by="2099-01-01"), "2026-03-01")


def test_already_dormant_entries_are_not_swept_again():
    entries = [_entry(state=core.STATE_DORMANT, accessed_at="")]
    assert health.decay_candidates(entries, "2026-03-01") == []


def test_a_decay_finding_states_the_reason_and_the_way_back():
    found = health.decay_candidates([_entry(accessed_at="")], "2026-03-01")
    assert len(found) == 1
    assert "2026-02-01" in found[0].detail and "never read" in found[0].detail
    assert "show" in found[0].remedy, "the reader must be told how to keep it"


# ---------------------------------------------------------------------------
# out-of-band: the cry-wolf failure is the one under test


def test_a_legacy_entry_is_not_flagged_as_hand_edited(vault):
    """The regression that made the first implementation useless.

    Six live entries predate `state:` and the renderer's date quoting. A
    byte-level round-trip flagged all six, which trains the reader to scroll
    past the signal — and the real edit then hides in the noise.
    """
    (vault / "2026-01-01-legacy.md").write_text(
        "---\n"
        "type: lesson\n"
        "author: cli-agent\n"
        "created: 2026-01-01\n"  # unquoted: the old renderer's output
        "review-by: 2099-01-01\n"
        "anchor: src/x.py:1\n"
        "title: Written before state existed\n"
        "---\n\nBody.\n",
        encoding="utf-8",
    )
    assert health.out_of_band_edits(vault, core) == []


def test_an_unknown_frontmatter_key_is_flagged(vault):
    """A key the CLI never writes — and which the next write would silently
    drop, which is why it is worth saying out loud."""
    core.write_entry(vault, _entry(title="Hand edited", review_by="2099-01-01"))
    path = next(vault.glob("*.md"))
    path.write_text(
        path.read_text(encoding="utf-8").replace("type: lesson", "type: lesson\nmood: cheerful"),
        encoding="utf-8",
    )
    found = health.out_of_band_edits(vault, core)
    assert len(found) == 1
    assert "mood" in found[0].detail
    assert "drop" in found[0].detail, "say what is at stake, not just that it differs"


def test_a_schema_violation_is_flagged(vault):
    core.write_entry(vault, _entry(title="Broken", review_by="2099-01-01"))
    path = next(vault.glob("*.md"))
    path.write_text(
        path.read_text(encoding="utf-8").replace("type: lesson", "type: nonsense"),
        encoding="utf-8",
    )
    found = health.out_of_band_edits(vault, core)
    assert len(found) == 1 and "schema" in found[0].detail


def test_a_readme_in_the_vault_is_not_an_entry(vault):
    (vault / "README.md").write_text("# Notes\n\nNot an entry.\n", encoding="utf-8")
    assert health.out_of_band_edits(vault, core) == []


def test_known_keys_are_derived_from_cores_schema_not_hardcoded():
    """A hardcoded list turns core's next field into a false positive on every
    entry carrying it — the drift that attribute-probing exists to avoid."""
    keys = health.known_keys(core)
    for field in dataclasses.fields(core.KnowledgeEntry):
        if field.name != "body":
            assert field.name.replace("_", "-") in keys
    source = Path(health.__file__).read_text(encoding="utf-8")
    assert "dataclasses.fields" in source


def test_the_known_gap_is_stated():
    """The register. A gap that stops being written down stops being fixed."""
    doc = health.out_of_band_edits.__doc__ or ""
    assert "GAP" in doc and "BODY PROSE" in doc


# ---------------------------------------------------------------------------
# ownership: not-checked must never read as clean


def test_no_partitions_map_yields_not_checked_never_an_empty_list():
    found = health.ownership_violations([_entry()], None)
    assert len(found) == 1
    assert found[0].kind == health.NOT_CHECKED
    assert "3.1" in found[0].remedy


def test_an_empty_partitions_map_is_also_not_checked():
    assert health.ownership_violations([_entry()], {})[0].kind == health.NOT_CHECKED


def test_an_entry_authored_past_the_owner_is_flagged():
    entries = [_entry(function="development", author="plugin-agent")]
    found = health.ownership_violations(entries, {"development": "cli-agent"})
    assert len(found) == 1 and found[0].kind == health.OWNERSHIP
    assert "plugin-agent" in found[0].detail and "cli-agent" in found[0].detail


def test_the_owners_own_entry_is_not_flagged():
    entries = [_entry(function="development", author="cli-agent")]
    assert health.ownership_violations(entries, {"development": "cli-agent"}) == []


def test_an_unowned_partition_is_not_a_violation():
    """`support` is carried explicitly unowned (kv2 3.1) — no owner is not a
    violation, it is a partition nobody curates yet."""
    entries = [_entry(function="support", author="anyone")]
    assert health.ownership_violations(entries, {"development": "cli-agent"}) == []


# ---------------------------------------------------------------------------
# 2.3 — domain validation


def test_no_registry_accepts_the_term_but_says_it_was_not_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(vocabulary, "registry_path", lambda root: tmp_path / "vocabulary.yaml")
    ok, note = vocabulary.check_domain(tmp_path, "fintech")
    assert ok, "refusing every domain would make the field unusable for every program today"
    assert "NOT validated" in note and "vocabulary.yaml" in note


def test_an_undeclared_term_is_refused_and_the_refusal_names_the_registry(tmp_path, monkeypatch):
    reg = tmp_path / "vocabulary.yaml"
    reg.write_text("vocabulary:\n  - term: fintech\n  - term: logistics\n", encoding="utf-8")
    monkeypatch.setattr(vocabulary, "registry_path", lambda root: reg)
    ok, note = vocabulary.check_domain(tmp_path, "aerospace")
    assert not ok
    assert str(reg) in note, "the operator must be told WHERE to declare it"
    assert "fintech" in note


def test_a_declared_term_passes_case_insensitively(tmp_path, monkeypatch):
    reg = tmp_path / "vocabulary.yaml"
    reg.write_text("vocabulary:\n  - term: FinTech\n", encoding="utf-8")
    monkeypatch.setattr(vocabulary, "registry_path", lambda root: reg)
    ok, note = vocabulary.check_domain(tmp_path, "fintech")
    assert ok and not note


def test_an_empty_domain_is_always_fine(tmp_path):
    assert vocabulary.check_domain(tmp_path, "") == (True, "")


def test_the_vocabulary_reader_is_the_consoles_not_a_second_one():
    """Four sibling registries are unbuilt and their schemas are spec-agent's.
    Two schema-guessing readers would be two guesses to reconcile."""
    source = Path(vocabulary.__file__).read_text(encoding="utf-8")
    assert "from otaman_cli.console.extra_registries import entry_rows" in source


# ---------------------------------------------------------------------------
# the doctor check and the sweep verb


def _program(tmp_path):
    root = tmp_path / "meta"
    (root / ".agents" / "knowledge").mkdir(parents=True)
    root.joinpath("platform.yaml").write_text(
        "project: demo\nversion: '1.0'\nrepos: []\n", encoding="utf-8"
    )
    return root


def test_doctor_never_reports_ok_for_an_ownership_check_it_could_not_run(tmp_path):
    """The defect this repo has now shipped twice: a health surface saying
    [OK] for something it never looked at."""
    from otaman_cli.doctor import check_knowledge_health

    root = _program(tmp_path)
    core.write_entry(root / ".agents" / "knowledge", _entry(review_by="2099-01-01"))
    result = check_knowledge_health(root)
    assert result["status"] == "warn"
    assert "NOT CHECKED" in result["details"]["ownership"]


def test_doctor_does_not_modify_the_vault(tmp_path):
    """Doctor's stated invariant. The sweep that mutates lives behind its own
    verb precisely so this one can stay safe to run."""
    from otaman_cli.doctor import check_knowledge_health

    root = _program(tmp_path)
    vault = root / ".agents" / "knowledge"
    core.write_entry(vault, _entry(accessed_at=""))  # a decay candidate
    before = {p: p.read_bytes() for p in vault.glob("*.md")}
    check_knowledge_health(root)
    assert {p: p.read_bytes() for p in vault.glob("*.md")} == before


def test_doctor_points_at_the_sweep_rather_than_fixing(tmp_path):
    from otaman_cli.doctor import check_knowledge_health

    root = _program(tmp_path)
    core.write_entry(root / ".agents" / "knowledge", _entry(accessed_at=""))
    result = check_knowledge_health(root)
    assert "knowledge sweep" in result["details"]["sweep"]


def test_an_absent_vault_is_not_a_warning(tmp_path):
    from otaman_cli.doctor import check_knowledge_health

    root = tmp_path / "meta"
    root.mkdir()
    root.joinpath("platform.yaml").write_text("project: demo\n", encoding="utf-8")
    assert check_knowledge_health(root)["status"] == "ok"


def test_sweep_reports_without_applying_by_default(tmp_path, monkeypatch, capsys):
    """A verb that retires things as a side effect of being asked a question is
    one nobody can afford to run to find out."""
    from otaman_cli.commands.knowledge import cmd_knowledge

    root = _program(tmp_path)
    vault = root / ".agents" / "knowledge"
    core.write_entry(vault, _entry(accessed_at=""))
    monkeypatch.setattr("otaman_cli.commands.knowledge.find_project_root", lambda: root)

    assert cmd_knowledge(["sweep"]) == 0
    assert capsys.readouterr()
    stem = next(vault.glob("*.md")).stem
    assert core.load_entry_by_stem(vault, stem).state == core.STATE_ACTIVE


def test_sweep_apply_moves_to_dormant_and_restore_brings_it_back(tmp_path, monkeypatch):
    from otaman_cli.commands.knowledge import cmd_knowledge

    root = _program(tmp_path)
    vault = root / ".agents" / "knowledge"
    core.write_entry(vault, _entry(accessed_at=""))
    monkeypatch.setattr("otaman_cli.commands.knowledge.find_project_root", lambda: root)
    stem = next(vault.glob("*.md")).stem

    assert cmd_knowledge(["sweep", "--apply"]) == 0
    assert core.load_entry_by_stem(vault, stem).state == core.STATE_DORMANT

    assert cmd_knowledge(["sweep", "--restore", stem]) == 0
    assert core.load_entry_by_stem(vault, stem).state == core.STATE_ACTIVE


def test_the_sweep_changes_only_the_state_field(tmp_path, monkeypatch):
    """ "Reversible" is only true if nothing the author wrote was touched."""
    from otaman_cli.commands.knowledge import cmd_knowledge

    root = _program(tmp_path)
    vault = root / ".agents" / "knowledge"
    core.write_entry(vault, _entry(accessed_at="", body="The author's words."))
    monkeypatch.setattr("otaman_cli.commands.knowledge.find_project_root", lambda: root)
    stem = next(vault.glob("*.md")).stem
    before = core.load_entry_by_stem(vault, stem)

    cmd_knowledge(["sweep", "--apply"])
    after = core.load_entry_by_stem(vault, stem)

    assert dataclasses.replace(after, state=before.state) == before
