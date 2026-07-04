"""Small, independent, mostly read-only commands — migrated from main.py.

`--reviewer` (review) and `--format` (compliance) were each exclusive to
their one command in main()'s shared flag loop, so removed entirely
(F021/F022) rather than duplicated; each command now parses its own flag.

`_normalize_ce_platform_yaml_for_validation` stays in main.py -- it's
shared with cmd_init (not yet migrated); commands/misc_readonly.py
imports it from there, same pattern as commands/complete.py importing
_read_platform_specs_path.
"""

from __future__ import annotations

from pathlib import Path

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root
from otaman_cli.main import C, UI, _normalize_ce_platform_yaml_for_validation, run_script


def cmd_owner_paths(args: list[str]) -> int:
    """monorepo-path-ownership task 2.2 — `otaman owner-paths --validate`.

    Walks every repo's `owner-paths:` block and reports:
      [ok]    pattern → declared agent
      [WARN]  two patterns overlap at equal specificity
      [ERROR] referenced agent not declared in platform.yaml agents:

    Exit 0 on success or warnings-only; 1 when any error finding fires.
    """
    if "--validate" not in args:
        UI.error("Usage: otaman owner-paths --validate")
        return 2

    from otaman_cli.owner_paths import validate_owner_paths

    root = find_project_root()
    if root is None:
        UI.error("Not in an otaman project (no platform.yaml found)")
        return 1

    print("  Validating owner-paths in platform.yaml...")
    findings = validate_owner_paths(root)
    if not findings:
        print("  No owner-paths configured in platform.yaml")
        return 0

    # Render with the spec-specified labels + alignment
    n_ok = sum(1 for f in findings if f.severity == "ok")
    n_warn = sum(1 for f in findings if f.severity == "warn")
    n_error = sum(1 for f in findings if f.severity == "error")

    for f in findings:
        label_map = {"ok": "[ok]   ", "warn": "[WARN] ", "error": "[ERROR]"}
        label = label_map.get(f.severity, "[?]   ")
        # Padding for visual alignment with the spec's example output
        line = f'  {label} {f.repo}: "{f.pattern}"  → {f.agent}'
        print(line)
        if f.note:
            print(f"         {f.note}")

    print()
    print(
        f"  Summary: {n_ok + n_warn + n_error} patterns, "
        f"{n_warn} warnings, {n_error} errors"
    )
    if n_error > 0:
        print("  Run `otaman owner-paths --validate` again after fixing platform.yaml")
        return 1
    return 0


def cmd_review(args: list[str]) -> int:
    """Trigger a review."""
    reviewer = "all"
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--reviewer" and i + 1 < len(args):
            reviewer = args[i + 1]
            i += 2
        else:
            positional.append(args[i])
            i += 1

    UI.info("Observer reviews are designed to run inside Claude Code sessions")
    print(f"  where the observer agents have access to Read/Glob/Grep/Bash tools.")
    print()
    UI.action(f"Run in your Claude Code session:")
    UI.muted(f"/otaman:review --reviewer {reviewer}")
    if positional:
        UI.kv("Scope", " ".join(positional))
    return 0


def cmd_validate(args: list[str]) -> int:
    """Validate platform.yaml.

    ce-org-agent-bootstrap task 4.1 — accepts the CE platform.yaml shape
    by normalizing in-memory before validation (agent→owner alias; project
    inferred from parent dir; version default "1.0").  Deprecation hints
    are printed; the on-disk file is not rewritten.
    """
    config = args[0] if args else "platform.yaml"
    config_path = Path(config)
    norm_path, hints = _normalize_ce_platform_yaml_for_validation(config_path)
    if hints:
        for h in hints:
            UI.muted(f"hint: {h}")
    try:
        result = run_script("validate-platform.py", str(norm_path))
    finally:
        if norm_path != config_path and norm_path.exists():
            try:
                norm_path.unlink()
            except OSError:
                pass
    return result.returncode


def cmd_validate_messages(args: list[str]) -> int:
    """Validate bus message files."""
    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    UI.header("Bus Message Validation")

    if args:
        # Validate specific file
        result = run_script("validate-message.py", args[0])
    else:
        # Validate all active messages
        result = run_script("validate-message.py", str(root), "--all")
    return result.returncode


def cmd_compliance(args: list[str]) -> int:
    """Generate compliance report."""
    fmt = "markdown"
    i = 0
    while i < len(args):
        if args[i] == "--format" and i + 1 < len(args):
            fmt = args[i + 1]
            i += 2
        else:
            i += 1

    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1
    result = run_script("compliance-report.py", str(root), "--format", fmt)
    return result.returncode


def cmd_discovery_phase(args: list[str]) -> int:
    """Show discovery phase status."""
    UI.header("Otaman Discovery Phase")

    # Find presale dir
    d = Path.cwd()
    presale_dir = None
    for _ in range(10):
        new_ = d / ".otaman-presale"
        if new_.is_dir():
            presale_dir = new_
            break
        if (d / ".maestro-presale").is_dir():  # legacy: fallback for pre-rebrand presale dirs
            presale_dir = d / ".maestro-presale"  # legacy: pre-rebrand directory name
            break
        parent = d.parent
        if parent == d:
            break
        d = parent

    if not presale_dir:
        UI.error("No .otaman-presale/ (or legacy: .maestro-presale/) directory found.")
        UI.muted("Run 'otaman presale' first to initialize a pre-sale project.")
        return 1

    # Show status
    meta_path = presale_dir / "project-meta.yaml"
    if meta_path.exists():
        try:
            import yaml
            meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
            UI.kv("Project", f"{meta.get('project_name', '?')} ({meta.get('project_code', '?')})")
            UI.kv("Domain", meta.get('domain', '?'))
            UI.kv("Phase", meta.get('current_phase', '?'))
        except Exception:
            pass

    # Check artifacts
    checks = [
        ("Estimation", (presale_dir / "estimation").is_dir() and list((presale_dir / "estimation").glob("estimate-*.md"))),
        ("Assumptions", (presale_dir / "assumptions.yaml").exists()),
        ("Risks", (presale_dir / "risks.yaml").exists()),
        ("Architecture", (presale_dir / "architecture").is_dir() and list((presale_dir / "architecture").glob("*.md"))),
        ("Knowledge audit", (presale_dir / "knowledge-audit.yaml").exists()),
        ("Validated assumptions", (presale_dir / "discovery" / "validated-assumptions.yaml").exists()),
        ("Updated risks", (presale_dir / "discovery" / "updated-risks.yaml").exists()),
    ]

    UI.subheader("Discovery Artifacts:")
    for name, exists in checks:
        icon = UI.badge("OK", C.GREEN) if exists else UI.path("--")
        print(f"  [{icon}] {name}")

    UI.subheader("To manage discovery interactively:")
    UI.action(f"Use {C.GREEN}/otaman:discovery{C.RESET} in Claude Code")
    UI.muted("It will guide you through assumption validation and risk mitigation.")
    return 0


def cmd_handoff(args: list[str]) -> int:
    """Show handoff readiness."""
    UI.header("Otaman Handoff")

    d = Path.cwd()
    presale_dir = None
    for _ in range(10):
        new_ = d / ".otaman-presale"
        if new_.is_dir():
            presale_dir = new_
            break
        if (d / ".maestro-presale").is_dir():  # legacy: fallback for pre-rebrand presale dirs
            presale_dir = d / ".maestro-presale"  # legacy: pre-rebrand directory name
            break
        parent = d.parent
        if parent == d:
            break
        d = parent

    if not presale_dir:
        UI.error("No .otaman-presale/ (or legacy: .maestro-presale/) directory found.")
        return 1

    UI.kv("Presale dir", str(presale_dir))
    has_estimation = list((presale_dir / "estimation").glob("estimate-*.md")) if (presale_dir / "estimation").is_dir() else []
    has_platform = (presale_dir.parent / "platform.yaml").exists()

    UI.subheader("Handoff readiness:")
    est_icon = UI.badge("OK", C.GREEN) if has_estimation else UI.path("--")
    ka_icon = UI.badge("OK", C.GREEN) if (presale_dir / 'knowledge-audit.yaml').exists() else UI.path("--")
    py_icon = UI.badge("SKIP", C.YELLOW) if has_platform else UI.path("--")
    print(f"  [{est_icon}] Estimation document")
    print(f"  [{ka_icon}] Knowledge audit")
    print(f"  [{py_icon}] platform.yaml {'(already exists)' if has_platform else '(will be generated)'}")

    UI.subheader("To execute handoff:")
    UI.action(f"Use {C.GREEN}/otaman:handoff execute{C.RESET} in Claude Code")
    UI.muted("It will generate platform.yaml, create ADRs, and migrate artifacts.")
    return 0


def cmd_audit_knowledge(args: list[str]) -> int:
    """Show knowledge audit status."""
    UI.header("Otaman Knowledge Audit")

    # Check multiple locations for audit file
    for candidate in [".otaman-presale/knowledge-audit.yaml", ".maestro-presale/knowledge-audit.yaml", ".agents/knowledge-audit.yaml"]:  # legacy: presale fallback
        p = Path(candidate)
        if p.exists():
            try:
                import yaml
                audit = yaml.safe_load(p.read_text(encoding="utf-8"))
                UI.kv("Audit date", audit.get('audit_date', '?'))
                UI.kv("Overall readiness", f"{audit.get('overall_readiness', '?')}%")
                print()
                for item in audit.get("items", []):
                    conf = item.get("confidence", "?")
                    icon = {"high": UI.badge("OK", C.GREEN), "medium": UI.badge("??", C.YELLOW),
                            "low": UI.badge("!!", C.RED), "none": UI.badge("XX", C.RED)}.get(conf, "??")
                    print(f"  [{icon}] {item.get('tech', '?'):30s} {conf:8s} {item.get('action', '')}")
            except Exception as e:
                UI.error(f"Failed to read audit: {e}")
            return 0

    UI.muted("No knowledge audit found.")
    UI.subheader("To run the audit:")
    UI.action(f"Use {C.GREEN}/otaman:audit-knowledge{C.RESET} in Claude Code")
    UI.muted("It will assess Claude's confidence per tech stack item.")
    return 0


def cmd_gate(args: list[str]) -> int:
    """Check gate readiness for a phase transition."""
    UI.header("Otaman Gate Check")

    root = find_project_root()
    transition = args[0] if args else None

    # Determine current phase
    for meta_loc in [".otaman-presale/project-meta.yaml", ".maestro-presale/project-meta.yaml", ".agents/project-meta.yaml"]:  # legacy: presale fallback
        p = Path(meta_loc) if not root else root / meta_loc
        if p.exists():
            try:
                import yaml
                meta = yaml.safe_load(p.read_text(encoding="utf-8"))
                phase = meta.get("current_phase", "?")
                UI.kv("Current phase", phase, C.BOLD)
                if not transition:
                    # Auto-detect next transition
                    default_order = ["presale", "discovery", "development", "support"]
                    if phase in default_order:
                        idx = default_order.index(phase)
                        if idx + 1 < len(default_order):
                            next_phase = default_order[idx + 1]
                            transition = f"{phase}-to-{next_phase}"
            except Exception:
                pass
            break

    if transition:
        UI.kv("Transition", transition, C.BOLD)
    else:
        UI.error("Could not determine transition. Specify: otaman gate <from>-to-<to>")
        return 1

    UI.subheader("To run full gate validation:")
    UI.action(f"Use {C.GREEN}/otaman:gate {transition}{C.RESET} in Claude Code")
    UI.muted("It will check required artifacts, run validations, and apply domain-specific checks.")
    return 0


register(CommandSpec(name="owner-paths", handler=cmd_owner_paths, help="Validate owner-paths globs in platform.yaml"))
register(CommandSpec(name="review", handler=cmd_review, help="Trigger observer review (CTO / security / all)"))
register(CommandSpec(name="validate", handler=cmd_validate, help="Validate platform.yaml against the schema"))
register(CommandSpec(name="validate-messages", handler=cmd_validate_messages, help="Validate bus message files"))
register(CommandSpec(name="compliance", handler=cmd_compliance, help="Generate compliance audit report (HIPAA / ISO / GDPR)"))
register(CommandSpec(name="discovery", handler=cmd_discovery_phase, help="Show discovery phase status"))
register(CommandSpec(name="handoff", handler=cmd_handoff, help="Show handoff readiness (presale → development)"))
register(CommandSpec(name="audit-knowledge", handler=cmd_audit_knowledge, help="Show tech stack knowledge audit"))
register(CommandSpec(name="gate", handler=cmd_gate, help="Check phase transition readiness"))
