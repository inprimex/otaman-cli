"""picker-canonical-precedence 1.1/1.2 — a stale ancestor cannot swallow programs.

Live on riseapps, 2026-09-11: the console picker showed **one** program — the org
root — instead of the tenant's **five** real programs. A pre-migration leftover
`orgs/<org>/platform.yaml` (June 2026, empty `repos: []` but structurally valid, with
a sibling `.agents/`) passed `_program_meta`'s shape gate. The picker's
"drop candidates nested inside another candidate" dedup then discarded every
canonical program, because all five sit physically under `orgs/<org>/`. Worked around
live by renaming the stray file; any tenant carrying the same leftover hits it again.

The old rule was right for its purpose — a copy under a program's repos/ tree is not
a program — and could not distinguish that purpose from this accident. So it is split
in two:

* a non-canonical candidate ABOVE a canonical one is a stale ancestor: excluded, and
  NAMED, never the other way round
* the nested-drop is scoped to a candidate inside another candidate's own DECLARED
  repos tree — physical nesting beneath an ancestor is not sufficient

1.2 asks for the regression fixture, and it is `riseapps_shape` below: the org-root
leftover plus N canonical programs, asserting all N list and the leftover is named.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_cli.console import bus

N_PROGRAMS = 5


def _program(meta_dir: Path, project: str, repos: list[str] | None = None) -> None:
    """A structurally valid program root: full shape + a bus beside it."""
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / ".agents").mkdir(exist_ok=True)
    lines = [f"project: {project}", "version: '1.0'"]
    if repos:
        lines.append("repos:")
        for rel in repos:
            lines.append(f"  - name: {Path(rel).name}")
            lines.append(f"    path: {rel}")
    else:
        lines.append("repos: []")
    (meta_dir / "platform.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def riseapps_shape(tmp_path):
    """The 1.2 fixture: an org-root pre-migration leftover above N canonical programs.

    `<base>/orgs/riseapps/platform.yaml`      <- the June-2026 leftover, repos: []
    `<base>/orgs/riseapps/programs/<p>/<p>-otaman/platform.yaml`  x N
    """
    base = tmp_path / "home"
    org = base / "orgs" / "riseapps"
    _program(org, "riseapps-org-leftover")
    for i in range(N_PROGRAMS):
        name = f"prog{i}"
        _program(org / "programs" / name / f"{name}-otaman", name)
    return base, org


# ---------------------------------------------------------------------------
# The incident, as a test.


def test_all_canonical_programs_are_listed(riseapps_shape):
    """THE DEFECT: this returned exactly one program — the leftover."""
    base, _org = riseapps_shape

    names = [p.name for p in bus.discover_programs(base)]

    assert names == [f"prog{i}" for i in range(N_PROGRAMS)]
    assert len(names) == N_PROGRAMS


def test_the_leftover_is_excluded(riseapps_shape):
    base, _org = riseapps_shape

    names = [p.name for p in bus.discover_programs(base)]

    assert "riseapps-org-leftover" not in names


def test_the_leftover_is_named_in_a_warning(riseapps_shape):
    """Excluded is not enough — it has to be DELETED by someone, and the incident
    took a live debugging session precisely because nothing said which file."""
    base, org = riseapps_shape
    warnings: list[str] = []

    bus.discover_programs(base, warnings=warnings)

    assert len(warnings) == 1
    assert str(org / "platform.yaml") in warnings[0]
    assert "shadow" in warnings[0]


def test_discovery_still_works_without_the_warnings_argument(riseapps_shape):
    """Optional and append-only: eighteen existing call sites pass nothing."""
    base, _org = riseapps_shape

    assert len(bus.discover_programs(base)) == N_PROGRAMS


# ---------------------------------------------------------------------------
# The rule the old one was FOR must still hold.


#: Deep enough to REACH a stray sitting inside a repos tree. The default is 4, and a
#: stray two levels below a repo root sits at 6 — so a fixture using the default
#: never discovers it and the test passes whatever the dedup does. Both deep cases
#: below were written that way first and passed with the rule deleted; the
#: `_asserts_discoverable` guard exists so the next reader cannot repeat it.
_DEEP = 8


def _asserts_discoverable(stray_meta: Path) -> None:
    """Fail loudly if the stray is not even a valid candidate.

    A dedup test whose subject was never discovered proves nothing, and reads
    exactly like one that works.
    """
    assert bus._program_meta(stray_meta / "platform.yaml") is not None, (
        "the fixture's stray is not a valid candidate — this test cannot prove a drop"
    )


@pytest.mark.parametrize("rel", ["repo-a", "repo-a/nested", "repo-a/deep/deeper"])
def test_a_stray_copy_inside_a_declared_repos_tree_is_still_dropped(tmp_path, rel):
    """The second scenario. A stray platform.yaml inside a real program's declared
    repos tree is not its own program, and the owning program lists once.

    Note what the OLD rule did here: nothing. The owning meta
    (`real/real-otaman`) is a SIBLING of `real/repo-a`, not an ancestor, so physical
    nesting never matched and every one of these strays survived. Verified by
    deleting the new rule and re-running at this depth: all three listed
    `['real', 'stray-copy']`.
    """
    base = tmp_path / "home"
    _program(base / "orgs" / "acme" / "programs" / "real" / "real-otaman", "real", ["../repo-a"])
    stray = base / "orgs" / "acme" / "programs" / "real" / rel
    _program(stray, "stray-copy")
    _asserts_discoverable(stray)

    assert [p.name for p in bus.discover_programs(base, max_depth=_DEEP)] == ["real"]


def test_a_sibling_program_not_in_any_repos_tree_survives(tmp_path):
    """The case the OLD rule got wrong in the other direction is not reintroduced:
    physical nesting is no longer sufficient, so two programs under one parent both
    list as long as neither declares the other."""
    base = tmp_path / "home"
    org = base / "orgs" / "acme" / "programs"
    _program(org / "one" / "one-otaman", "one", repos=["../repo-a"])
    _program(org / "two" / "two-otaman", "two", repos=["../repo-b"])

    assert [p.name for p in bus.discover_programs(base, max_depth=_DEEP)] == ["one", "two"]


# ---------------------------------------------------------------------------
# The canonical guard is explicit, not a side effect of the repos rule.


def test_a_canonical_program_is_kept_even_when_the_ancestor_declares_it(tmp_path):
    """The case the repos-tree rule alone would get WRONG. An org-root leftover that
    declares `programs` as a repo would otherwise contain every canonical program,
    and the delta says canonical candidates are NEVER dropped for a shallower
    non-canonical ancestor."""
    base = tmp_path / "home"
    org = base / "orgs" / "acme"
    _program(org, "org-leftover", repos=["programs"])
    _program(org / "programs" / "real" / "real-otaman", "real")
    warnings: list[str] = []

    names = [p.name for p in bus.discover_programs(base, warnings=warnings)]

    assert names == ["real"]
    assert warnings and str(org / "platform.yaml") in warnings[0]


def test_the_canonical_layout_predicate_matches_the_canon(tmp_path):
    canonical = tmp_path / "base" / "orgs" / "acme" / "programs" / "p" / "p-otaman"
    assert bus._is_canonical_layout(canonical) is True
    # wrong segment names at the fixed depths
    assert bus._is_canonical_layout(tmp_path / "base" / "teams" / "a" / "programs" / "p" / "m") is (
        False
    )
    assert bus._is_canonical_layout(tmp_path / "base" / "orgs" / "a" / "projects" / "p" / "m") is (
        False
    )
    # too shallow to be the canonical shape at all
    assert bus._is_canonical_layout(tmp_path / "orgs" / "acme") is False


def test_a_non_canonical_ancestor_with_no_canonical_descendant_is_kept(tmp_path):
    """A plain non-canonical workspace must keep working: the exclusion fires only
    when canonical programs are actually being shadowed."""
    base = tmp_path / "workspace"
    _program(base / "alpha-otaman", "alpha")
    warnings: list[str] = []

    names = [p.name for p in bus.discover_programs(base, warnings=warnings)]

    assert names == ["alpha"]
    assert warnings == []


# ---------------------------------------------------------------------------
# Declared-repos resolution.


def test_declared_repo_dirs_resolves_relative_to_the_meta_root(tmp_path):
    meta = tmp_path / "p" / "p-otaman"
    meta.mkdir(parents=True)
    dirs = bus._declared_repo_dirs(meta, {"repos": [{"name": "a", "path": "../a"}]})

    assert dirs == {(tmp_path / "p" / "a").resolve()}


@pytest.mark.parametrize(
    "repos",
    [
        [],
        None,
        [{"name": "a"}],  # no path
        [{"name": "a", "path": "   "}],  # blank path
        ["not-a-mapping"],
    ],
)
def test_declared_repo_dirs_tolerates_a_shapeless_repos_list(tmp_path, repos):
    """An empty or malformed `repos:` declares no tree — which is the riseapps
    leftover's exact shape, and it must contain nothing rather than everything."""
    meta = tmp_path / "p"
    meta.mkdir()

    assert bus._declared_repo_dirs(meta, {"repos": repos}) == set()


# ---------------------------------------------------------------------------
# The surfaces.


def test_the_launch_path_prints_the_warning(tmp_path, monkeypatch, capsys):
    """Before the TUI takes the screen: once the app has started there is nowhere
    for a line like this to go."""
    base = tmp_path / "home"
    org = base / "orgs" / "riseapps"
    _program(org, "leftover")
    _program(org / "programs" / "p0" / "p0-otaman", "p0")

    import otaman_cli.console.launch as launch

    monkeypatch.setattr(launch, "_resolve_search_root", lambda argv: base)
    monkeypatch.setattr("otaman_cli.console.seat.on_fleet_server", lambda: False)
    monkeypatch.setattr("otaman_cli.console.seat.should_seat", lambda argv: False)
    monkeypatch.setattr("otaman_cli.console.seat.in_seat", lambda: False)

    rc = launch.run_console([], _run=False)
    out = capsys.readouterr().out

    assert rc == 0
    assert "warning:" in out
    assert str(org / "platform.yaml") in out


# ---------------------------------------------------------------------------
# program-crud-and-context-resolution 1.2 — the canonical arm consumes CORE.


def test_the_canonical_arm_follows_cores_enumeration(riseapps_shape, monkeypatch):
    """Make core enumerate something the directory walk would not, and require the
    picker to follow it.

    Asserting the five programs alone could not tell core's answer from the local
    walk's — both find them. The only way to see which one answered is to make them
    disagree, which is the guard core asked for on the resolver and the same shape as
    `test_the_org_slug_for_a_program_path_comes_from_core`.
    """
    import otaman_core.program_context as core

    base, org = riseapps_shape
    only = org / "programs" / "prog3"
    monkeypatch.setattr(
        core,
        "enumerate_programs",
        lambda root: [core.Program(name="prog3", org="riseapps", path=only)],
    )
    names = {p.name for p in bus.discover_programs(base)}
    assert "prog3" in names
    for dropped in ("prog0", "prog1", "prog2", "prog4"):
        assert dropped not in names, "the canonical arm must take core's list, not its own walk"


def test_the_console_gate_still_runs_on_what_core_returns(riseapps_shape, monkeypatch):
    """Both gates apply. Core's predicate rejects debris under `programs/`; if a core
    without it hands one up, the console's own full-shape/bus gate still drops it."""
    import otaman_core.program_context as core

    base, org = riseapps_shape
    debris = org / "programs" / "debris"
    debris.mkdir(parents=True)
    (debris / "LICENSE").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(
        core,
        "enumerate_programs",
        lambda root: [
            core.Program(name="debris", org="riseapps", path=debris),
            core.Program(name="prog0", org="riseapps", path=org / "programs" / "prog0"),
        ],
    )
    names = {p.name for p in bus.discover_programs(base)}
    assert "prog0" in names
    assert "debris" not in names


def test_an_old_bundle_without_the_resolver_still_lists_programs(riseapps_shape, monkeypatch):
    """5.1 finding #3 is the reason the local walk survives as a fallback: launched from
    home, the bounded walk cannot reach a meta dir five levels down, so losing the
    canonical arm on an old bundle means an EMPTY picker in the human's own home dir."""
    import builtins

    base, org = riseapps_shape
    real_import = builtins.__import__

    def no_resolver(name, *args, **kwargs):
        if name == "otaman_core.program_context":
            raise ImportError("no program_context in this bundle")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_resolver)
    names = {p.name for p in bus.discover_programs(base)}
    assert len([n for n in names if n.startswith("prog")]) == N_PROGRAMS


def test_the_meta_hop_is_the_consoles_own_question(tmp_path):
    """Core answers WHICH programs exist; the console needs their META root, which is
    what every console read opens. A program dir with no meta dir yields nothing."""
    program = tmp_path / "orgs" / "acme" / "programs" / "p1"
    program.mkdir(parents=True)
    assert bus._meta_dirs_of(program) == []

    # A program dir holds its REPOS beside the meta dir, and a repo is a directory with
    # no platform.yaml. Without one in the fixture, "any child dir" and "a child dir
    # with a platform.yaml" are the same answer and the check is untested.
    (program / "some-repo" / "src").mkdir(parents=True)
    assert bus._meta_dirs_of(program) == [], "a repo is not a meta dir"

    _program(program / "p1-otaman", "p1")
    assert bus._meta_dirs_of(program) == [program / "p1-otaman"]
    assert bus._meta_dirs_of(tmp_path / "nowhere") == []
