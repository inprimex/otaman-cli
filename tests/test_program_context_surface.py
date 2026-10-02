"""program-crud-and-context-resolution 1.2 (cli half) — the program in context.

Core owns resolution and its ruled precedence (`resolve_program`, core #111). The two
halves that are the CLI's are the ones core cannot hold: the TTY picker (its PRESENCE
is the interactivity signal in core's chain) and the refusal rendering (name the
programs and the `--program` form; advise `init` only at zero).

The measurement that shaped this module is pinned below: core's enumeration tests the
path SHAPE, so on the live machine it reports three programs where one exists —
`orgs/<org>/programs/` also holds two stray directories from a botched copy. The
listing and the refusal mark those instead of offering debris as somewhere to work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("otaman_core.program_context")

from otaman_cli import program_context as pc  # noqa: E402


def _workspace(tmp_path: Path, *, programs: dict[str, bool]) -> Path:
    """A workspace whose `orgs/acme/programs/` holds *programs*.

    The bool says whether the program is REAL — a real one gets a meta dir with a
    platform.yaml, a fake one is a bare directory (what the live machine has two of).
    """
    base = tmp_path / "orgs" / "acme" / "programs"
    for name, real in programs.items():
        program_dir = base / name
        program_dir.mkdir(parents=True)
        if real:
            meta = program_dir / f"{name}-meta"
            meta.mkdir()
            (meta / "platform.yaml").write_text("project: demo\n", encoding="utf-8")
        else:
            (program_dir / "LICENSE").write_text("x\n", encoding="utf-8")
    return tmp_path


# --- the workspace root is derived, not configured --------------------------------


def test_the_workspace_root_comes_from_the_program_tree(tmp_path, monkeypatch):
    monkeypatch.delenv("OTAMAN_WORKSPACE", raising=False)
    root = _workspace(tmp_path, programs={"one": True})
    inside = root / "orgs" / "acme" / "programs" / "one" / "one-meta"
    assert pc.workspace_root(inside) == root


def test_outside_a_program_tree_the_workspace_root_is_home(tmp_path, monkeypatch):
    monkeypatch.delenv("OTAMAN_WORKSPACE", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    assert pc.workspace_root(tmp_path / "elsewhere") == tmp_path / "home"


def test_the_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("OTAMAN_WORKSPACE", str(tmp_path / "ws"))
    root = _workspace(tmp_path, programs={"one": True})
    inside = root / "orgs" / "acme" / "programs" / "one" / "one-meta"
    assert pc.workspace_root(inside) == tmp_path / "ws"


# --- interactivity is decided in exactly one place --------------------------------


class _Stream:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_a_tty_offers_the_picker_and_a_pipe_does_not(monkeypatch):
    monkeypatch.delenv("OTAMAN_NO_PICKER", raising=False)
    assert pc.interactive(_Stream(True)) is True
    assert pc.interactive(_Stream(False)) is False


def test_the_picker_can_be_forced_off_on_a_tty(monkeypatch):
    monkeypatch.setenv("OTAMAN_NO_PICKER", "1")
    assert pc.interactive(_Stream(True)) is False


def test_a_stream_that_cannot_answer_is_not_a_tty(monkeypatch):
    monkeypatch.delenv("OTAMAN_NO_PICKER", raising=False)

    class _Broken:
        def isatty(self):
            raise OSError("detached")

    assert pc.interactive(_Broken()) is False


# --- resolution: core's precedence, the cli's picker -------------------------------


def test_the_cwd_walk_resolves_without_enumerating(tmp_path, monkeypatch):
    monkeypatch.delenv("OTAMAN_WORKSPACE", raising=False)
    root = _workspace(tmp_path, programs={"one": True, "two": True})
    inside = root / "orgs" / "acme" / "programs" / "two" / "two-meta"
    program = pc.resolve(cwd=inside, allow_picker=False)
    assert (program.name, program.org) == ("two", "acme")


def test_an_explicit_name_wins_over_the_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("OTAMAN_WORKSPACE", raising=False)
    root = _workspace(tmp_path, programs={"one": True, "two": True})
    inside = root / "orgs" / "acme" / "programs" / "two"
    program = pc.resolve(explicit="one", cwd=inside, allow_picker=False)
    assert program.name == "one"


def test_an_explicit_miss_refuses_rather_than_falling_through(tmp_path, monkeypatch):
    """core's rule, pinned here because the fall-through would be silent: a caller who
    named a program must not be handed a different one."""
    from otaman_core.program_context import ProgramContextError

    monkeypatch.delenv("OTAMAN_WORKSPACE", raising=False)
    root = _workspace(tmp_path, programs={"one": True})
    inside = root / "orgs" / "acme" / "programs" / "one" / "one-meta"
    with pytest.raises(ProgramContextError):
        pc.resolve(explicit="nope", cwd=inside, allow_picker=False)


def test_the_picker_is_passed_only_when_interactivity_was_decided(tmp_path, monkeypatch):
    """The picker's PRESENCE is core's TTY signal, so a non-interactive caller must
    refuse rather than block on a prompt no one can answer."""
    from otaman_core.program_context import ProgramContextError

    monkeypatch.delenv("OTAMAN_WORKSPACE", raising=False)
    root = _workspace(tmp_path, programs={"one": True})
    called = []
    monkeypatch.setattr(pc, "picker", lambda programs: called.append(programs) or programs[0])

    with pytest.raises(ProgramContextError):
        pc.resolve(cwd=root / "elsewhere", allow_picker=False)
    assert called == [], "a non-interactive caller must not reach the picker"

    monkeypatch.setenv("OTAMAN_WORKSPACE", str(root))
    chosen = pc.resolve(cwd=root / "elsewhere", allow_picker=True)
    assert chosen.name == "one"
    assert called, "an interactive caller must reach the picker"


def test_declining_the_picker_falls_through_to_the_refusal(tmp_path, monkeypatch):
    from otaman_core.program_context import ProgramContextError

    monkeypatch.setenv("OTAMAN_WORKSPACE", str(_workspace(tmp_path, programs={"one": True})))
    monkeypatch.setattr(pc, "picker", lambda programs: None)
    with pytest.raises(ProgramContextError):
        pc.resolve(cwd=tmp_path / "elsewhere", allow_picker=True)


def test_a_bundle_without_the_resolver_refuses_with_a_remedy(monkeypatch):
    monkeypatch.setattr(pc, "_core", lambda: None)
    with pytest.raises(RuntimeError) as caught:
        pc.resolve(allow_picker=False)
    assert "update the bundle" in str(caught.value)


# --- the refusal: three roles, and init advice only at zero -----------------------


class _Err(Exception):
    def __init__(self, message, programs):
        super().__init__(message)
        self.programs = programs


def test_the_refusal_names_the_programs_and_the_target_form(tmp_path):
    from otaman_core.program_context import enumerate_programs

    root = _workspace(tmp_path, programs={"one": True, "two": True})
    parts = pc.refusal(_Err("no program in context", enumerate_programs(root)))
    assert parts.error == "no program in context"
    assert any(c.startswith("one") for c in parts.candidates)
    assert "--program <name>" in parts.advice
    assert "init" not in parts.advice


def test_with_no_programs_the_refusal_advises_init_and_nothing_else(tmp_path):
    parts = pc.refusal(_Err("nothing here", []))
    assert parts.candidates == ()
    assert "otaman init" in parts.advice


def test_the_refusal_marks_a_candidate_that_is_not_a_program(tmp_path):
    """Offering a human a directory holding only LICENSE as somewhere to 'target one'
    wastes the single message they get."""
    from otaman_core.program_context import enumerate_programs

    root = _workspace(tmp_path, programs={"real": True, "debris": False})
    parts = pc.refusal(_Err("no program in context", enumerate_programs(root)))
    debris = next(c for c in parts.candidates if c.startswith("debris"))
    real = next(c for c in parts.candidates if c.startswith("real"))
    assert pc.NOT_A_PROGRAM in debris
    assert pc.NOT_A_PROGRAM not in real


def test_refusal_lines_flattens_the_same_facts(tmp_path):
    from otaman_core.program_context import enumerate_programs

    root = _workspace(tmp_path, programs={"one": True})
    lines = pc.refusal_lines(_Err("no program in context", enumerate_programs(root)))
    assert lines[0] == "no program in context"
    assert any("one" in line for line in lines)


# --- the listing: enumeration is core's, the annotation is the surface's ----------


def test_rows_annotate_which_candidates_are_actually_programs(tmp_path, monkeypatch):
    """The live measurement: 3 candidates, 1 program. Core's enumeration tests the
    path shape; a listing that called all three programs would send the human (or the
    picker) at a directory holding `LICENSE` and nothing else."""
    monkeypatch.setenv(
        "OTAMAN_WORKSPACE",
        str(_workspace(tmp_path, programs={"real": True, "debris-a": False, "debris-b": False})),
    )
    rows = {r.name: r for r in pc.rows()}
    assert set(rows) == {"real", "debris-a", "debris-b"}
    assert rows["real"].readable is True
    assert rows["real"].note == ""
    assert rows["debris-a"].readable is False
    assert pc.NOT_A_PROGRAM in rows["debris-a"].note


def test_rows_are_empty_on_a_machine_with_no_orgs_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("OTAMAN_WORKSPACE", str(tmp_path / "bare"))
    assert pc.rows() == []


def test_an_archived_program_is_listed_and_marked_not_hidden(tmp_path, monkeypatch):
    """core keeps archived programs in the enumeration deliberately; a human who
    archived one yesterday must not conclude it is gone."""
    from otaman_core.lifecycle import lifecycle_registry_path, record_transition

    root = _workspace(tmp_path, programs={"old": True, "current": True})
    org = root / "orgs" / "acme"
    # `record_transition` takes the REGISTRY path; `read_program_state` takes the ORG
    # root and derives it. Writing through the derived path keeps the test honest about
    # which of the two core reads on the listing path.
    registry = lifecycle_registry_path(org)
    registry.parent.mkdir(parents=True, exist_ok=True)
    record_transition(registry, "old", "archived", by="roman", reason="done")

    monkeypatch.setenv("OTAMAN_WORKSPACE", str(root))
    rows = {r.name: r for r in pc.rows()}
    assert rows["old"].state == "archived", "an archived program must still be listed"
    assert rows["current"].state == "active"


# --- the verbs ---------------------------------------------------------------------


def _wire(monkeypatch, workspace: Path, cwd: Path):
    import otaman_cli.commands.program as P

    monkeypatch.setenv("OTAMAN_WORKSPACE", str(workspace))
    monkeypatch.setenv("OTAMAN_NO_PICKER", "1")
    monkeypatch.chdir(cwd)
    return P


def test_program_list_renders_state_and_marks_non_programs(tmp_path, monkeypatch, capsys):
    root = _workspace(tmp_path, programs={"real": True, "debris": False})
    P = _wire(monkeypatch, root, tmp_path)
    assert P.cmd_program(["list"]) == 0
    out = capsys.readouterr().out
    assert "real" in out and "debris" in out
    assert pc.NOT_A_PROGRAM in out
    assert "1 of 2 candidate(s) hold no program metadata" in out


def test_program_list_json_carries_the_readable_flag(tmp_path, monkeypatch, capsys):
    import json

    root = _workspace(tmp_path, programs={"real": True, "debris": False})
    P = _wire(monkeypatch, root, tmp_path)
    assert P.cmd_program(["list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    by_name = {p["name"]: p for p in payload["programs"]}
    assert by_name["real"]["readable"] is True
    assert by_name["debris"]["readable"] is False
    assert payload["workspace"] == str(root)


def test_program_list_on_an_empty_machine_advises_init(tmp_path, monkeypatch, capsys):
    """init-advice-only-at-zero: THIS is the zero case, so the advice belongs here."""
    P = _wire(monkeypatch, tmp_path / "bare", tmp_path)
    assert P.cmd_program(["list"]) == 0
    out = capsys.readouterr().out
    assert "No programs" in out
    assert "otaman init" in out


def test_program_show_resolves_from_the_cwd(tmp_path, monkeypatch, capsys):
    root = _workspace(tmp_path, programs={"one": True})
    inside = root / "orgs" / "acme" / "programs" / "one" / "one-meta"
    P = _wire(monkeypatch, root, inside)
    assert P.cmd_program(["show"]) == 0
    out = capsys.readouterr().out
    assert "one" in out and "acme" in out


def test_program_show_refuses_an_unknown_name_with_exit_two(tmp_path, monkeypatch, capsys):
    root = _workspace(tmp_path, programs={"one": True})
    P = _wire(monkeypatch, root, root)
    assert P.cmd_program(["show", "nope"]) == 2
    out = capsys.readouterr().out
    assert "nope" in out
    assert "one" in out, "the refusal names the programs that DO exist"
    assert "--program <name>" in out


def test_program_show_off_a_tty_refuses_instead_of_prompting(tmp_path, monkeypatch, capsys):
    """The non-interactive refusal, which is the whole reason the picker is a callback."""
    root = _workspace(tmp_path, programs={"one": True, "two": True})
    P = _wire(monkeypatch, root, tmp_path)
    assert P.cmd_program(["show"]) == 2
    out = capsys.readouterr().out
    assert "not on a TTY" in out
    assert "otaman init" not in out, "programs exist — init is not the advice"


def test_program_show_json_reports_the_refusal_as_data(tmp_path, monkeypatch, capsys):
    import json

    root = _workspace(tmp_path, programs={"one": True})
    P = _wire(monkeypatch, root, tmp_path)
    assert P.cmd_program(["show", "--json"]) == 2
    assert "error" in json.loads(capsys.readouterr().out)


def test_the_new_actions_are_advertised(capsys):
    import otaman_cli.commands.program as P

    P.cmd_program(["--help"])
    out = capsys.readouterr().out
    assert "program list" in out and "program show" in out


# --- bus_target now consumes core ---------------------------------------------------


def test_the_local_context_comes_from_cores_resolver(tmp_path, monkeypatch):
    """1.2's "bus_target consumes core": this file used to walk the parents itself."""
    from otaman_cli.bus_target import derive_local_context

    root = _workspace(tmp_path, programs={"one": True})
    meta = root / "orgs" / "acme" / "programs" / "one" / "one-meta"
    ctx = derive_local_context(meta)
    assert (ctx.org, ctx.program) == ("acme", "one")
    assert ctx.program_root == meta.resolve()
    assert ctx.org_root == (root / "orgs" / "acme").resolve()


def test_a_non_conforming_tree_still_reads_as_no_context(tmp_path):
    from otaman_cli.bus_target import derive_local_context

    assert derive_local_context(tmp_path / "somewhere") is None


def test_an_illegal_segment_is_refused_by_the_surface_not_core(tmp_path):
    """Core answers WHERE the program sits; this surface still decides whether that
    org/program pair is a legal bus address."""
    from otaman_cli.bus_target import derive_local_context

    base = tmp_path / "orgs" / "Not_A_Slug" / "programs" / "one"
    (base / "one-meta").mkdir(parents=True)
    assert derive_local_context(base / "one-meta") is None


def test_a_bundle_without_the_resolver_degrades_to_no_cross_program_sends(tmp_path, monkeypatch):
    """The specified meaning of None, so an old bundle loses cross-program sends
    rather than falling back to a second reading of the layout."""
    import builtins

    from otaman_cli.bus_target import derive_local_context

    root = _workspace(tmp_path, programs={"one": True})
    meta = root / "orgs" / "acme" / "programs" / "one" / "one-meta"
    real = builtins.__import__

    def no_resolver(name, *args, **kwargs):
        if name == "otaman_core.program_context":
            raise ImportError("no program_context in this bundle")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_resolver)
    assert derive_local_context(meta) is None


# --- the org slug reads core first --------------------------------------------------


def test_the_org_slug_for_a_program_path_comes_from_core(tmp_path, monkeypatch):
    """Make core say something the raw path does not, and require the surface to follow.

    Asserting `"acme"` alone would have been vacuous: the path scan below returns
    `"acme"` too, so the test passed with the delegation removed. The only way to tell
    which answered is to make them disagree.
    """
    import otaman_core.program_context as core

    from otaman_cli.commands.connection import _infer_org_from_path

    root = _workspace(tmp_path, programs={"one": True})
    meta = root / "orgs" / "acme" / "programs" / "one" / "one-meta"
    monkeypatch.setattr(
        core,
        "program_of_path",
        lambda path: core.Program(name="one", org="from-core", path=meta.parent),
    )
    assert _infer_org_from_path(meta) == "from-core"


def test_a_nested_layout_resolves_to_the_NEAREST_program(tmp_path):
    """Where the two readings genuinely differ on real paths: a checkout that contains
    another workspace. The path scan takes the FIRST `orgs` segment (the outer one);
    core walks up to the nearest program dir. The nearest is the right answer — it is
    the program whose tree you are actually standing in.
    """
    from otaman_cli.commands.connection import _infer_org_from_path

    inner = (
        tmp_path
        / "orgs"
        / "outer"
        / "programs"
        / "p1"
        / "checkout"
        / "orgs"
        / "inner"
        / "programs"
        / "p2"
        / "p2-meta"
    )
    inner.mkdir(parents=True)
    assert _infer_org_from_path(inner) == "inner"


def test_an_org_level_path_still_resolves_by_the_wider_scan(tmp_path):
    """Core does not claim this case — a path under `orgs/<org>/` that is in no
    program at all — and the credential cascade reads exactly that."""
    from otaman_cli.commands.connection import _infer_org_from_path

    root = _workspace(tmp_path, programs={"one": True})
    assert _infer_org_from_path(root / "orgs" / "acme" / "config") == "acme"
    assert _infer_org_from_path(tmp_path / "elsewhere") is None
