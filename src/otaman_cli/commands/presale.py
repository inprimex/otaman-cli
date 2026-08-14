"""`otaman presale` and `otaman retrospective` — migrated from main.py.

Migrated together since both call the presale-exclusive
`_find_presale_dir` walk-up helper; splitting them across files would
just mean one importing the helper from the other.
"""

from __future__ import annotations

from pathlib import Path

from otaman_cli.commands import CommandSpec, register
from otaman_cli.main import UI, C, run_script


def _find_presale_dir(start: Path) -> Path | None:
    """Locate a presale directory walking up from ``start``.

    Prefers ``.otaman-presale/`` (current name). Falls back to legacy
    legacy: ``.maestro-presale/`` for one release window (sunset at otaman-core 1.0).
    Returns absolute Path or None.
    """
    for d in [start] + list(start.parents):
        new = d / ".otaman-presale"
        if new.is_dir():
            return new
        legacy = d / ".maestro-presale"  # legacy: fallback for pre-rebrand presale dirs
        if legacy.is_dir():
            return legacy
    return None


def cmd_presale(args: list[str]) -> int:
    """Initialize a pre-sale estimation project."""
    UI.header("Otaman Pre-Sale")

    # Check for existing presale
    cwd = Path.cwd()
    presale_dir = _find_presale_dir(cwd)

    if presale_dir:
        meta_path = presale_dir / "project-meta.yaml"
        if meta_path.exists():
            UI.info(f"Found existing pre-sale project at: {C.BOLD}{presale_dir}{C.RESET}")
            try:
                import yaml

                meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
                UI.kv(
                    "Project", f"{meta.get('project_name', '?')} ({meta.get('project_code', '?')})"
                )
                UI.kv("Domain", meta.get("domain", "?"))
                UI.kv("Phase", meta.get("current_phase", "?"))
            except Exception:
                pass
            UI.muted("To continue this estimation, use /otaman:presale in Claude Code.")
            UI.muted("The SA agent will pick up where you left off.")
            return 0

    # Interactive setup
    if len(args) >= 3:
        project_name, domain = args[0], args[1]
        client = args[2] if len(args) > 2 else ""
    else:
        print("Setting up a new pre-sale estimation project.\n")
        project_name = input("  Project name: ").strip()
        if not project_name:
            UI.error("Project name required")
            return 1
        domain = input(
            "  Domain (healthcare/fintech/marketplace/ml-ai/saas/ecommerce/iot/general): "
        ).strip()
        if not domain:
            domain = "general"
        client = input("  Client name (optional): ").strip()

    # Generate project code
    from datetime import date

    domain_prefix = {
        "healthcare": "HLT",
        "fintech": "FIN",
        "marketplace": "MKT",
        "ml-ai": "ML",
        "saas": "SAS",
        "ecommerce": "ECM",
        "iot": "IOT",
        "general": "GEN",
    }.get(domain, "GEN")
    date_suffix = date.today().strftime("%y%m%d")
    project_code = f"{domain_prefix}-EST-{date_suffix}"

    # Run init-presale script
    script_args = [project_code, project_name, domain]
    if client:
        script_args.extend(["--client", client])

    result = run_script("init-presale.py", *script_args)
    if result.returncode != 0:
        return result.returncode

    print()
    UI.ok("Pre-sale project initialized.")
    UI.kv("Code", project_code, C.BOLD)
    UI.kv("Domain", domain)
    UI.kv("Dir", ".otaman-presale/")
    print()
    UI.action(f"Run {C.GREEN}/otaman:presale{C.RESET} in Claude Code to start Gate 0 estimation.")
    UI.muted("The SA agent will guide you through the full estimation workflow.")
    return 0


def cmd_retrospective(args: list[str]) -> int:
    """Post-project retrospective — updates benchmarks."""
    UI.header("Otaman Retrospective")

    # Find project meta
    cwd = Path.cwd()
    presale_dir = _find_presale_dir(cwd)

    project_code = args[0] if args else None
    meta = None

    if presale_dir:
        meta_path = presale_dir / "project-meta.yaml"
        if meta_path.exists():
            try:
                import yaml

                meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
                project_code = project_code or meta.get("project_code", "UNKNOWN")
            except Exception:
                pass

    if not project_code:
        UI.error("No project code found.")
        UI.muted("Usage: otaman retrospective [project-code]")
        UI.muted(
            "Or run from a directory with .otaman-presale/project-meta.yaml "
            "(or legacy: .maestro-presale/)"
        )
        return 1

    UI.kv("Project", project_code, C.BOLD)
    if meta:
        UI.kv("Domain", meta.get("domain", "?"))
        est = meta.get("estimation", {})
        if est.get("total_range_hours"):
            rng = est["total_range_hours"]
            UI.kv("Estimated", f"{rng[0]}-{rng[1]} hours")

    UI.subheader("To run the full retrospective:")
    UI.action(f"Use {C.GREEN}/otaman:retrospective{C.RESET} in Claude Code")
    UI.muted("The agent will collect actuals, calculate accuracy, and update benchmarks.")
    print()
    UI.muted("For a quick manual benchmark entry, add data directly to:")
    UI.muted("  assets/estimation-benchmarks.yaml")
    return 0


register(
    CommandSpec(name="presale", handler=cmd_presale, help="Initialize pre-sale estimation project")
)
register(
    CommandSpec(
        name="retrospective",
        handler=cmd_retrospective,
        help="Post-project retrospective (updates benchmarks)",
    )
)
