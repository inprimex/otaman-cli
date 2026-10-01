"""In-process dispatch to the otaman helper scripts.

Extracted from `main.py` with `UI` (CTO review 2026-09-26). `run_script` is a leaf
utility that thirteen call sites need, and it sat in the entry-point module beside the
dispatcher that calls them — so importing it meant importing the thing it serves.

Imports `ui` and nothing else from `otaman_cli`: that keeps the dependency pointing
DOWNWARD (entry point -> command -> scripts -> ui) instead of back up at `main`.

`main` re-exports `run_script` and `SCRIPT_MAP`. That matters beyond tidiness here:
seven tests patch `otaman_cli.main.run_script` by string path, and a consumer that
reads the name off `main` at call time still sees those patches. Consumers repointed
to this module are patched here instead, and those seven were updated with the move.
"""

from __future__ import annotations

import sys

from otaman_cli.ui import UI

# After Stage 4E carve, run_script() dispatches to scripts that live across
# multiple sibling repos via "python -m <module>". SCRIPT_MAP records the
# legacy filename → new module name lookup; PYTHONPATH for the subprocess
# is built from the dispatcher's own location.
SCRIPT_MAP = {
    # otaman-core: validators
    "validate-platform.py": "otaman_core.validate_platform",
    "validate-message.py": "otaman_core.validate_message",
    # otaman-cli: this very package
    "doctor.py": "otaman_cli.doctor",
    "cleanup-bus.py": "otaman_cli.cleanup_bus",
    "compliance-report.py": "otaman_cli.compliance_report",
    "status-report.py": "otaman_cli.status_report",
    "models-report.py": "otaman_cli.models_report",
    "accounts.py": "otaman_cli.accounts",
    "install_cli.py": "otaman_cli.install_cli",
    # otaman-bridge: server daemon scripts
    "ping.py": "otaman_bridge.ping",
    "afk.py": "otaman_bridge.afk",
    "bridge/cli.py": "otaman_bridge.cli",
    # otaman-plugin: proper Python package
    "actualize-tasks.py": "otaman_plugin.actualize_tasks",
    "clone-project.py": "otaman_plugin.clone_project",
    "discover-repos.py": "otaman_plugin.discover_repos",
    "generate-agent-config.py": "otaman_plugin.generate_agent_config",
    "init-presale.py": "otaman_plugin.init_presale",
    "map-tasks.py": "otaman_plugin.map_tasks",
    "scaffold-launcher.py": "otaman_plugin.scaffold_launcher",
}


def run_script(name: str, *args: str, capture: bool = False, stream_stderr: bool = False):
    """Run an otaman script via direct Python import (Roman option C).

    SCRIPT_MAP records legacy filename → fully-qualified module name. The
    target module is imported in-process; its main(argv) is called; the
    return value is shaped to match subprocess.CompletedProcess so existing
    call sites in this dispatcher don't need to change.

    Args:
        capture: Capture stdout (for JSON parsing).
        stream_stderr: If True and capture=True, let stderr pass through.

    Returns:
        SimpleNamespace with .returncode, .stdout (str if capture else None),
        .stderr (always None — pure imports don't separate stderr).
    """
    if name not in SCRIPT_MAP:
        UI.error(f"Script not in SCRIPT_MAP: {name}")
        sys.exit(2)
    module_name = SCRIPT_MAP[name]

    import contextlib
    import importlib
    import io
    from types import SimpleNamespace

    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        UI.error(f"Failed to import {module_name}: {e}")
        return SimpleNamespace(returncode=2, stdout="", stderr=None)

    main_fn = getattr(module, "main", None)
    if main_fn is None:
        UI.error(f"Module {module_name} has no main() entry point")
        return SimpleNamespace(returncode=2, stdout="", stderr=None)

    argv = list(args)

    def _invoke() -> int:
        # Inspect main() signature: scripts either accept argv as a list
        # or take no args and read sys.argv directly.
        import inspect

        try:
            sig = inspect.signature(main_fn)
            takes_argv = len(sig.parameters) >= 1
        except (ValueError, TypeError):
            takes_argv = True  # safer default

        # Save and set sys.argv for scripts that read it directly.
        saved_argv = sys.argv
        sys.argv = [name, *argv]
        try:
            if takes_argv:
                rc = main_fn(argv)
            else:
                rc = main_fn()
        except SystemExit as e:
            sys.argv = saved_argv
            return int(e.code) if e.code is not None else 0
        finally:
            sys.argv = saved_argv
        return int(rc) if rc is not None else 0

    if capture:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = _invoke()
        return SimpleNamespace(returncode=rc, stdout=buf.getvalue(), stderr=None)

    rc = _invoke()
    return SimpleNamespace(returncode=rc, stdout=None, stderr=None)


__all__ = ["SCRIPT_MAP", "run_script"]
