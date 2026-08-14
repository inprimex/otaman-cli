"""otaman onboard program-init — interactive program initialisation flow.

Public API used by the CLI entrypoint::

    from otaman_cli.onboard.program_init import run_program_init
    rc = run_program_init(args)

Sub-modules:
    edition      — CE/EE edition detection (single ``active_edition`` field)
    checkpoint   — ~/.otaman/<slug>/.init-state.yaml  read/write
    questions    — YAML-driven question loader + questionary adapter
    platform_gen — ruamel.yaml platform.yaml writer (create + UPDATE round-trip)
    scaffold     — in-process companion-repos invocation stub
    guidance     — post-init next-step message generator
    runner       — top-level orchestrator (entry point)
"""

from __future__ import annotations

from otaman_cli.onboard.program_init.runner import run_program_init

__all__ = ["run_program_init"]
