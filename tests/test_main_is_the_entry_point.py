"""`main.py` is the entry point, not the library (CTO review 2026-09-26).

The finding: `UI`, `C`, `run_script`, `SCRIPT_MAP` and four platform/bus helpers lived
in the entry-point module, so **39 of 40 modules under `commands/` imported `main`** —
the dispatcher, its 153-line help text and its version resolution — in order to print
a line. Measured before the move: importing ONE command module loaded 47 `otaman_cli`
modules including `main` and the whole `console` package, in 118ms. After: `main` is
not loaded at all, and the file went 806 -> 327 lines.

The direction is now entry point -> command -> leaf, and these tests hold it there.
They are structural on purpose: the inversion did not arrive through a decision, it
arrived through 39 people each needing `UI` and importing it from the only place it
was.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "otaman_cli"

#: The modules the extraction created. Each must stay importable without pulling the
#: entry point in, which is the whole property being protected.
LEAVES = ("ui", "scripts", "bus_paths", "platform_config")


def _modules():
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def _imports_main(tree: ast.AST) -> list[int]:
    """Line numbers where *tree* imports `otaman_cli.main`, by any spelling."""
    out: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "") == "otaman_cli.main":
                out.append(node.lineno)
            elif (node.module or "") == "otaman_cli" and any(a.name == "main" for a in node.names):
                out.append(node.lineno)
        elif isinstance(node, ast.Import):
            if any(a.name == "otaman_cli.main" for a in node.names):
                out.append(node.lineno)
    return out


# ---------------------------------------------------------------------------
# The direction.


def test_nothing_in_the_package_imports_the_entry_point():
    """THE finding. 39 of 40 command modules did; none may.

    Deliberately the whole package, not just `commands/`: the inversion was not a
    property of that directory, it was a property of `UI` living in `main`, and the
    next module to want `UI` could be anywhere.
    """
    offenders = []
    for path, tree in _modules():
        if path.name == "main.py":
            continue
        lines = _imports_main(tree)
        if lines:
            offenders.append(f"{path.relative_to(SRC)}:{','.join(map(str, lines))}")

    assert not offenders, (
        "these import the entry point: " + "; ".join(offenders) + " — the leaf is in "
        f"one of {LEAVES}, and `main` re-exports only for callers outside this repo"
    )


@pytest.mark.parametrize("leaf", LEAVES)
def test_no_leaf_imports_upward(leaf):
    """A leaf may import sideways and downward, never UP.

    "Up" is precisely two things: the entry point, and anything under `commands/`.
    Importing another leaf is fine and `platform_config` does it — it borrows
    `onboard.schema_fields`, which is itself a leaf. The first test in this file
    already forbids `main` package-wide, so what this adds is the `commands/` half:
    a leaf that imported a command would be a cycle through the registry, which
    imports all forty of them.
    """
    tree = ast.parse((SRC / f"{leaf}.py").read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        module = ""
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
        elif isinstance(node, ast.Import):
            module = next((a.name for a in node.names if a.name.startswith("otaman_cli")), "")
        if module == "otaman_cli.main" or module.startswith("otaman_cli.commands"):
            bad.append(f"{module} (line {node.lineno})")

    assert not bad, f"{leaf}.py imports upward: {bad}"


def test_ui_is_the_floor():
    """It imports NOTHING from otaman_cli — the property that makes the direction
    terminate rather than merely point somewhere else."""
    tree = ast.parse((SRC / "ui.py").read_text(encoding="utf-8"))
    reaches = [
        n.lineno
        for n in ast.walk(tree)
        if (isinstance(n, ast.ImportFrom) and (n.module or "").startswith("otaman_cli"))
        or (isinstance(n, ast.Import) and any(a.name.startswith("otaman_cli") for a in n.names))
    ]

    assert not reaches, f"ui.py imports from otaman_cli at line(s) {reaches}"


# ---------------------------------------------------------------------------
# Behaviour is unchanged — the point of a refactor.


def test_main_still_re_exports_every_moved_name():
    """Anything outside this repo that learned `from otaman_cli.main import UI` keeps
    working. Nothing is forced to follow a refactor it did not ask for."""
    import otaman_cli.main as main

    for name in (
        "UI",
        "C",
        "run_script",
        "SCRIPT_MAP",
        "_resolve_bus_paths",
        "_get_agent_ack_status",
        "_read_platform_specs_path",
        "_normalize_ce_platform_yaml_for_validation",
    ):
        assert hasattr(main, name), f"main no longer re-exports {name}"


def test_the_re_exports_are_the_same_objects():
    """Not copies. A second `UI` class would make `C.disable()` apply to one of them,
    and the colour decision is module-level and made once."""
    import otaman_cli.bus_paths as bus_paths
    import otaman_cli.main as main
    import otaman_cli.platform_config as platform_config
    import otaman_cli.scripts as scripts
    import otaman_cli.ui as ui

    assert main.UI is ui.UI
    assert main.C is ui.C
    assert main.run_script is scripts.run_script
    assert main.SCRIPT_MAP is scripts.SCRIPT_MAP
    assert main._resolve_bus_paths is bus_paths._resolve_bus_paths
    assert (
        main._normalize_ce_platform_yaml_for_validation
        is platform_config._normalize_ce_platform_yaml_for_validation
    )


def test_importing_a_command_no_longer_loads_the_entry_point():
    """The measurable outcome, in a fresh interpreter so an already-imported `main`
    from another test cannot make this pass."""
    code = (
        "import sys; import otaman_cli.commands.check; "
        "print('main' if 'otaman_cli.main' in sys.modules else 'clean')"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "clean", (
        "importing a command module pulled in the entry point again"
    )


def test_the_colour_auto_disable_still_runs_at_import():
    """It moved with `C`, and it has to stay module level: every `UI` method reads `C`
    at call time, so the decision must be made before the first line is printed."""
    source = (SRC / "ui.py").read_text(encoding="utf-8")

    assert "C.disable()" in source
    body = ast.parse(source).body
    guards = [n for n in body if isinstance(n, ast.If)]
    assert guards, "the auto-disable is no longer evaluated at module level"


def test_the_windows_encoding_fix_moved_with_the_output_layer():
    """It is a property of writing to the console, not of dispatching a command."""
    assert "reconfigure" in (SRC / "ui.py").read_text(encoding="utf-8")
    assert "reconfigure" not in (SRC / "main.py").read_text(encoding="utf-8")


def test_main_is_now_only_the_entry_point():
    """Version, help, dispatch. A guard on the SHAPE rather than a line count, so it
    fails when a library concern moves back in rather than when help text grows."""
    tree = ast.parse((SRC / "main.py").read_text(encoding="utf-8"))
    defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}

    assert defined == {"_resolve_version", "cmd_help", "main"}, (
        f"main.py defines {sorted(defined)} — a library concern moved back into the "
        "entry point; put it in a leaf and re-export if callers need it there"
    )
