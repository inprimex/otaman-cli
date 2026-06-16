"""Top-level orchestrator for `otaman onboard program-init` (tasks.md 2.1-2.3).

Flow:
    1. Detect edition (CE/EE) + mode (1/2+)
    2. Locate question YAML  →  fallback to built-in if not found
    3. Check for checkpoint  →  offer resume or restart
    4. Detect existing platform.yaml  →  UPDATE mode if found
    5. Run YAML-driven Q&A via questionary; checkpoint after each step
    6. Generate / update platform.yaml
    7. Scaffold companion repos (in-process, with graceful fallback)
    8. Print post-init guidance
    9. Clear checkpoint on success

Idempotency (task 2.2):
    - Re-running on an existing program detects platform.yaml
    - Prompts "update existing program?" instead of re-asking all questions
    - Only delta questions are presented (questions whose step was NOT in
      the previous platform.yaml's step list)

Failure recovery (task 2.3):
    - Checkpoint is written after every step (via on_step_complete callback)
    - On re-run the checkpoint is loaded and completed steps are skipped
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from otaman_cli.onboard.program_init.agents_init import init_agents_structure
from otaman_cli.onboard.program_init.checkpoint import Checkpoint
from otaman_cli.onboard.program_init.edition import detect_edition, detect_mode
from otaman_cli.onboard.program_init.git_init import create_gitignore, ensure_git_repo, initial_commit
from otaman_cli.onboard.program_init.guidance import print_guidance
from otaman_cli.onboard.program_init.platform_gen import update_platform_yaml, write_platform_yaml
from otaman_cli.onboard.program_init.scaffold import ScaffoldResult, compute_companion_repos, scaffold_companion_repos
from otaman_cli.onboard.scaffold_ce import scaffold_companion_repos_ce, ScaffoldError

# Default question YAML search path (relative to otaman-meta sibling checkout)
_DEFAULT_QUESTIONS_REL = "../otaman-meta/onboarding/program-init-questions.yaml"


def _find_questions_yaml(override: str | None = None) -> Path | None:
    if override:
        p = Path(override)
        return p if p.is_file() else None
    # Try well-known paths
    candidates = [
        Path(_DEFAULT_QUESTIONS_REL),
        Path.home() / ".otaman" / "onboarding" / "program-init-questions.yaml",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _load_questions(questions_yaml: Path | None) -> list[dict[str, Any]]:
    if questions_yaml:
        from otaman_cli.onboard.program_init.questions import load_questions
        return load_questions(questions_yaml)
    # Fall back to the built-in minimal question set (ensures tests always work)
    return _builtin_questions()


def _builtin_questions() -> list[dict[str, Any]]:
    """Minimal hard-coded question set used when the YAML file is absent.

    Mirrors the 10-step walkthrough from proposal.md §1.  The YAML-driven
    file in otaman-meta is authoritative for production; this is the safe
    fallback for CI / fresh checkouts.
    """
    return [
        # ── step: identity ──────────────────────────────────────────────────
        {
            "id": "program_name", "step": "identity", "type": "text",
            "label": "Program name (kebab-slug)",
            "default": "my-program", "validate": "kebab_slug",
            "output_mapping": "project",
        },
        {
            "id": "description", "step": "identity", "type": "text",
            "label": "Short description",
            "default": "",
            "output_mapping": "description",
        },
        {
            "id": "primary_repo", "step": "identity", "type": "text",
            "label": "Path to the primary repo (where platform.yaml + .agents/ live)",
            "help": (
                "Filesystem path — absolute, relative to current dir, or '.' for "
                "current dir. Examples: '.', './my-specs', '~/projects/x/x-specs'"
            ),
            # Single-repo default: when cmd_init detects cwd is a git repo, it
            # sets OTAMAN_INIT_CWD_IS_GIT=1 so the wizard can suggest "." here.
            "default": "." if os.environ.get("OTAMAN_INIT_CWD_IS_GIT") else "",
            "output_mapping": "repos[0].path",
        },
        # program-init-claude-profile (2026-06-02): pre-fill an existing
        # Claude profile so the first session reuses the OAuth login.
        # Empty answer → field omitted from platform.yaml → launcher
        # falls back to $CLAUDE_CONFIG_DIR shell env / ~/.claude default.
        {
            "id": "claude_config_dir", "step": "identity", "type": "text",
            "label": "Claude profile path (CLAUDE_CONFIG_DIR; preserves OAuth session)",
            "help": (
                "Optional. Leave empty to defer to $CLAUDE_CONFIG_DIR or ~/.claude. "
                "Examples: ~/.claude  ~/.claude-myprogram"
            ),
            "default": "",
            "output_mapping": "program.claude.config_dir",
        },
        {
            "id": "domains", "step": "identity", "type": "checkbox",
            "label": "Target domain(s)",
            "options": [
                "software-development", "tech-startup", "fintech",
                "healthcare", "e-commerce", "gaming", "ai-ml",
                "drones-uav", "embedded-iot", "other",
            ],
            "default": [],
            "output_mapping": "domains",
        },
        # ── step: roles ──────────────────────────────────────────────────────
        {
            "id": "roles", "step": "roles", "type": "checkbox",
            "label": "Active roles in this program",
            "options": ["CEO", "CPO", "CTO", "BA", "cofounder", "engineer", "designer", "legal"],
            "default": [],
            "output_mapping": "roles",
        },
        # ── step: processes ──────────────────────────────────────────────────
        {
            "id": "processes", "step": "processes", "type": "checkbox",
            "label": "Which processes do you want to enable?",
            "options": [
                "outcomes", "solutions", "personas", "vocabulary",
                "flows", "processes", "risks", "assumptions",
            ],
            "default": [],
            "output_mapping": "processes",
        },
        # strategy offered only when cofounder role is active (spec §Q order §4)
        {
            "id": "strategy_opt_in", "step": "processes", "type": "confirm",
            "label": "Enable strategy process (pitch deck + business plan + GTM + financials)?",
            "default": False,
            "condition": "'cofounder' in answers.get('roles', [])",
            "output_mapping": "processes[+strategy]",
        },
        # ── step: currency ───────────────────────────────────────────────────
        {
            "id": "currency_code", "step": "currency", "type": "text",
            "label": "Currency code (ISO 4217)",
            "default": "USD",
            "output_mapping": "currency.code",
        },
        {
            "id": "currency_symbol", "step": "currency", "type": "text",
            "label": "Currency symbol",
            "default": "$",
            "output_mapping": "currency.symbol",
        },
        {
            "id": "currency_decimals", "step": "currency", "type": "number",
            "label": "Decimal places",
            "default": 2,
            "output_mapping": "currency.decimal_places",
        },
        # ── step: scales ─────────────────────────────────────────────────────
        {
            "id": "probability_scale", "step": "scales", "type": "select",
            "label": "Risk probability scale",
            "options": ["t-shirt", "fibonacci", "percentage", "custom"],
            "default": "t-shirt",
            "output_mapping": "triage.probability_scale",
        },
        {
            "id": "impact_scale", "step": "scales", "type": "select",
            "label": "Risk impact scale",
            "options": ["t-shirt", "fibonacci", "numeric", "custom"],
            "default": "t-shirt",
            "output_mapping": "triage.impact_scale",
        },
        # ── step: releases (outcomes-gated per spec §Q §7) ───────────────────
        {
            "id": "releases", "step": "releases", "type": "text",
            "label": "Release sequence (comma-separated, e.g. MVP,post-MVP)",
            "default": "MVP",
            "condition": "'outcomes' in answers.get('processes', [])",
            "output_mapping": "releases",
        },
        # ── step: skills ─────────────────────────────────────────────────────
        {
            "id": "skill_profile", "step": "skills", "type": "select",
            "label": "Skill profile",
            "options": [
                "software-development-default",
                "tech-startup-cofounder",
                "fintech-default",
                "healthcare-default",
                "custom",
            ],
            "default": "software-development-default",
            "default_from": "skill_profile_recommendation",
            "output_mapping": "skills.profile",
        },
        # ── step: git_platform ───────────────────────────────────────────────
        {
            "id": "git_platform", "step": "git_platform", "type": "select",
            "label": "Git platform",
            "options": ["local", "github", "gitlab", "bitbucket"],
            "default": "local",
            "mode_min": 1,
            "output_mapping": "git_platform",
        },
        # ── step: secrets (CE: env-file/os-keyring; EE: full list) ─────────
        {
            "id": "secret_backend", "step": "secrets", "type": "select",
            "label": "Secret backend",
            "options": ["env-file", "os-keyring"],
            "default": "env-file",
            "condition": "edition == 'ce'",
            "output_mapping": "secrets.backend",
        },
        {
            "id": "secret_backend_ee", "step": "secrets", "type": "select",
            "label": "Secret backend",
            "options": [
                "env-file", "os-keyring", "vault", "aws-secrets-manager",
                "gcp-secret-manager", "azure-key-vault", "1password-connect",
                "doppler", "infisical",
            ],
            "default": "env-file",
            "condition": "edition == 'ee'",
            "edition_min": "ee",
            "output_mapping": "secrets.backend",
        },
        # ── step: zitadel (EE + Mode 2+ only) ───────────────────────────────
        {
            "id": "organisation_name", "step": "zitadel", "type": "text",
            "label": "Zitadel organisation name",
            "default": "",
            "edition_min": "ee",
            "mode_min": 2,
            "output_mapping": "ee.organisation",
        },
    ]


def _detect_existing_platform_yaml(program_name: str, primary_repo: str | None) -> Path | None:
    """Try to find an existing platform.yaml for this program."""
    candidates: list[Path] = []
    if primary_repo:
        candidates.append(Path(primary_repo).expanduser() / "platform.yaml")
    # Convention: ~/otaman/<program>/<program>-specs/platform.yaml
    candidates.append(
        Path.home() / program_name / f"{program_name}-specs" / "platform.yaml"
    )
    for c in candidates:
        if c.is_file():
            return c
    return None


def _parse_releases(raw: str) -> list[str]:
    """'MVP,post-MVP' → ['MVP', 'post-MVP']"""
    return [r.strip() for r in raw.split(",") if r.strip()]


# tech-startup-skill-pack-implementation tasks 4.1-4.3 — confirmation screen
# shown when `tech-startup` is in the selected domains.  Lets the user
# verify the prefilled skill profile or override it before platform.yaml
# is written.  Skipped in non-interactive / dry-run modes.

_TECH_STARTUP_CONFIRM_COPY = (
    "The tech-startup pack includes 10 skills for cofounder strategy work.\n"
    "2 skills (investor-targeting-strategist, financial-modeling-analyst) "
    "require cofounder identity to activate.\n"
    "We've prefilled the skill profile — review and confirm."
)

_TECH_STARTUP_IDENTITY_NOTE = (
    "Note: 2 skills require cofounder identity.  To activate them, add\n"
    "  identity:\n"
    "    roles:\n"
    "      cofounder: <username>\n"
    "to platform.yaml after init."
)


def _confirm_tech_startup_prefill(answers: dict[str, Any], *, dry_run: bool) -> None:
    """Tasks 4.2 + 4.3 — show confirmation screen + identity note.

    Only fires when ``tech-startup`` is among the selected ``domains``.
    Allows the user to override the prefilled skill profile from the
    confirmation prompt.  In dry-run / non-interactive (EOF on input)
    mode, the prefill is accepted as-is silently.
    """
    domains = answers.get("domains") or []
    if "tech-startup" not in domains:
        return

    current_profile = answers.get("skill_profile") or "tech-startup-cofounder"

    # Always show the screen so it appears in --dry-run output too — visible
    # signal that prefill happened.  In dry-run we don't read input.
    print()
    print("─" * 64)
    print("  Tech-Startup Pack Prefill")
    print("─" * 64)
    for line in _TECH_STARTUP_CONFIRM_COPY.splitlines():
        print(f"  {line}")
    print()
    print(f"  Prefilled skill_profile: {current_profile}")
    print()
    for line in _TECH_STARTUP_IDENTITY_NOTE.splitlines():
        print(f"  {line}")
    print("─" * 64)

    if dry_run:
        return

    # Interactive confirmation — let the user override or accept.  EOF
    # / KeyboardInterrupt falls through to "accept as-is" (matches the
    # rest of the wizard's degrade-gracefully posture).
    try:
        raw = input(
            f"  Press Enter to keep '{current_profile}', or type a different profile name: "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return
    if raw and raw != current_profile:
        answers["skill_profile"] = raw
        print(f"  Skill profile overridden: {raw}")
    else:
        print(f"  Confirmed: {current_profile}")


def run_program_init(args: argparse.Namespace) -> int:
    """Main entry point called from the CLI.

    Returns 0 on success, non-zero on error.
    """
    # ── 1. edition + mode ────────────────────────────────────────────────────
    edition = detect_edition()
    mode_override: int | None = getattr(args, "mode", None)
    # Try to locate an existing platform.yaml early so mode can be auto-detected
    # from it (design Q3). We use the --program slug hint if available; the full
    # path is confirmed again later after the checkpoint / primary_repo are known.
    _early_slug = getattr(args, "program", None) or None
    _early_yaml = _detect_existing_platform_yaml(_early_slug or "", None) if _early_slug else None
    mode = mode_override if mode_override else detect_mode(_early_yaml)

    print()
    print("  Welcome to Otaman program-init.  Let's set up your new program.")
    print(f"  Edition: {edition.upper()}  |  Mode: {mode}")
    print()

    if edition == "ee":
        lic_env = __import__("os").environ.get("OTAMAN_LICENSE_FILE", "~/.otaman/license.key")
        print(f"  [EE] License detected: {lic_env}")
        print()

    # ── 2. load questions ────────────────────────────────────────────────────
    questions_path = _find_questions_yaml(getattr(args, "questions_yaml", None))
    questions = _load_questions(questions_path)
    if questions_path:
        _note(f"Using question definitions from {questions_path}")
    else:
        _note("Using built-in question definitions (no program-init-questions.yaml found)")

    # ── 3. checkpoint detection ───────────────────────────────────────────────
    # We need program_name early for checkpoint lookup; use a lightweight peek
    # at an existing checkpoint or the first text question
    ckpt_slug = _peek_program_slug(args)
    checkpoint = Checkpoint.load(ckpt_slug) if ckpt_slug else None

    if checkpoint and checkpoint.completed_steps:
        _note(
            f"Checkpoint found for '{checkpoint.program}' — "
            f"completed steps: {', '.join(checkpoint.completed_steps)}"
        )
        resume = _ask_yes_no("Resume from checkpoint?", default=True)
        if not resume:
            checkpoint = None
            _note("Starting fresh.")
        else:
            _note(f"Resuming from last checkpoint.")

    # ── 4. existing platform.yaml (UPDATE mode) ───────────────────────────────
    existing_yaml: Path | None = None
    if checkpoint:
        prefill_name = checkpoint.answers.get("program_name", "")
        existing_yaml = _detect_existing_platform_yaml(
            prefill_name,
            checkpoint.answers.get("primary_repo"),
        )
    if existing_yaml:
        print(f"  [i] Found existing platform.yaml: {existing_yaml}")
        update_mode = _ask_yes_no("Update existing program?", default=True)
        if not update_mode:
            existing_yaml = None  # treat as fresh

    # ── 5. run questions ─────────────────────────────────────────────────────
    prefill = checkpoint.answers if checkpoint else {}
    completed_steps = checkpoint.completed_steps if checkpoint else []

    # We need a checkpoint name before running — ask for program_name first if unknown.
    # Inject the early answer into prefill so run_questions doesn't re-ask it
    # (questions.py line 498: `if q_id in answers: continue`).
    if not ckpt_slug:
        ckpt_slug = _ask_program_name_early(questions)
        prefill = {**prefill, "program_name": ckpt_slug}

    active_checkpoint = checkpoint or Checkpoint.new(ckpt_slug)

    def on_step_complete(step_id: str, step_answers: dict[str, Any]) -> None:
        active_checkpoint.mark_step(step_id, step_answers)
        _note(f"Step '{step_id}' complete — checkpoint saved.")

    from otaman_cli.onboard.program_init.questions import run_questions
    try:
        answers = run_questions(
            questions,
            edition=edition,
            mode=mode,
            prefill=prefill,
            skip_steps=completed_steps,
            on_step_complete=on_step_complete,
        )
    except (KeyboardInterrupt, EOFError):
        print()
        print("  [!] Interrupted — progress saved to checkpoint.  Re-run to resume.")
        return 1

    # Inject meta-answers the questions don't ask
    answers["active_edition"] = edition
    answers["mode"] = mode
    if "releases" in answers and isinstance(answers["releases"], str):
        answers["releases"] = _parse_releases(answers["releases"])

    program_name: str = answers.get("program_name", ckpt_slug)

    # Merge strategy_opt_in into processes list (strategy is a separate confirm question
    # gated on cofounder role — spec §Q question-order §4)
    processes: list[str] = list(answers.get("processes") or [])
    if answers.get("strategy_opt_in") and "strategy" not in processes:
        processes.append("strategy")
        answers["processes"] = processes

    # Compute companion repos from processes
    companion_repos = compute_companion_repos(processes)
    answers["scaffold_business"] = "business" in companion_repos
    answers["scaffold_strategy"] = "strategy" in companion_repos

    # ── 5d. tech-startup-skill-pack-implementation tasks 4.1-4.3 ─────────────
    # When `tech-startup` was selected as a domain, the wizard's existing
    # `skill_profile` recommendation already defaults to `tech-startup-cofounder`
    # (questions._recommend_skill_profile).  Per the spec, surface a
    # confirmation screen with the exact copy from design.md Q4 PLUS the
    # cofounder identity note before writing platform.yaml.  The user may
    # override the prefilled profile from the confirmation screen.
    _confirm_tech_startup_prefill(answers, dry_run=getattr(args, "dry_run", False))

    # ── 6. generate / update platform.yaml ───────────────────────────────────
    if existing_yaml:
        platform_out = existing_yaml
        _note(f"Updating {platform_out} …")
        try:
            update_platform_yaml(answers, platform_out)
            _ok(f"Updated platform.yaml at {platform_out}")
        except Exception as exc:
            _error(f"platform.yaml update failed: {exc}")
            return 1
    else:
        # --output-dir overrides primary_repo as the output base directory
        output_dir_override: str | None = getattr(args, "output_dir", None)
        if output_dir_override:
            platform_out = Path(output_dir_override).expanduser() / "platform.yaml"
        else:
            primary_repo_raw: str = answers.get("primary_repo") or str(
                Path.home() / program_name / f"{program_name}-specs"
            )
            platform_out = Path(primary_repo_raw).expanduser() / "platform.yaml"
        _note(f"Generating {platform_out} …")
        try:
            write_platform_yaml(answers, platform_out)
            _ok(f"Generated platform.yaml at {platform_out}")
        except Exception as exc:
            _error(f"platform.yaml generation failed: {exc}")
            return 1

    # ── 7a. initialize .agents/ structure in the specs repo ──────────────────
    specs_dir = platform_out.parent
    if specs_dir.is_dir():
        _note("Initializing .agents/ directory structure …")
        try:
            created_paths = init_agents_structure(specs_dir, program_name)
            for p in created_paths:
                _ok(f"Created {p}")
            if not created_paths:
                _note(".agents/ already initialized — nothing to do.")
        except Exception as exc:
            _warn(f".agents/ init failed (non-fatal): {exc}")

    # ── 7b. git init + initial commit ────────────────────────────────────────
    if not existing_yaml:  # only for fresh inits, not UPDATE flow
        _note("Ensuring specs repo is a git repository …")
        git_err = ensure_git_repo(specs_dir)
        if git_err:
            _warn(f"git init failed (non-fatal): {git_err}")
        else:
            create_gitignore(specs_dir)
            _note("Creating initial git commit …")
            commit_err = initial_commit(specs_dir, program_name, answers)
            if commit_err:
                _warn(f"Initial commit failed (non-fatal): {commit_err}")
            else:
                _ok("Initial commit created.")

    # ── 7c. scaffold companion repos ─────────────────────────────────────────
    # CE path (in-process; no bridge) — always used in Mode 1 + CE edition.
    # EE bridge path is tried only when edition == 'ee' AND the bridge module
    # is importable; otherwise we fall back to CE so a fresh install always
    # produces a workable program (ce-companion-repos-scaffold).
    if companion_repos:
        _note(f"Scaffolding companion repos: {', '.join(companion_repos)} …")
        dry_run = getattr(args, "dry_run", False)
        use_bridge = False
        if edition == "ee":
            try:
                import otaman_bridge.scaffold as _bridge_scaffold  # noqa: F401
                use_bridge = True
            except ImportError:
                _note("EE bridge module not available; falling back to CE scaffolder.")

        if use_bridge:
            result: ScaffoldResult = scaffold_companion_repos(
                program_name, companion_repos, answers, dry_run=dry_run
            )
            for repo in result.scaffolded:
                _ok(f"Scaffolded {repo}")
            for repo in result.skipped:
                _note(f"Already exists, skipped: {repo}")
            for err in result.errors:
                _warn(err)
        else:
            try:
                ce_result = scaffold_companion_repos_ce(
                    program_slug=program_name,
                    processes=processes,
                    meta_dir=specs_dir,
                    program_name=answers.get("description") or program_name,
                    dry_run=dry_run,
                    repo_kinds=companion_repos,
                )
            except ScaffoldError as exc:
                _error(str(exc))
            else:
                for repo in ce_result.created:
                    _ok(f"Scaffolded {repo.kind} at {repo.path} (owner: {repo.owner})")
                for repo in ce_result.skipped:
                    _note(f"Already exists, skipped: {repo.path} — {repo.skipped_reason}")

    # ── 7d. launcher scaffold (otaman-init-dev-scaffold) ──────────────────────
    # Generate `launcher/` alongside platform.yaml so the program is ready
    # to launch with `bash launcher/launch.sh` (or .ps1 on Windows). We
    # default to non-interactive (--yes) inside program-init because the
    # wizard is already long; users can re-run `otaman init` in the meta
    # repo to customize connection mode / agent set / tmux layout.
    if not getattr(args, "dry_run", False):
        try:
            from otaman_cli.main import _scaffold_launcher_after_init
            _scaffold_launcher_after_init(platform_out, yes=True)
        except Exception as _launcher_exc:
            _note(f"Launcher scaffold skipped: {_launcher_exc}")

    # ── 8. post-init guidance ─────────────────────────────────────────────────
    print_guidance(answers, program_name)

    # ── 9. audit trail — bus message ──────────────────────────────────────────
    _emit_audit_message(program_name, answers, platform_out)

    # ── 10. clear checkpoint on success ───────────────────────────────────────
    active_checkpoint.clear()

    return 0


# --------------------------------------------------------------------------- helpers

def _note(msg: str) -> None:
    print(f"  [i] {msg}")


def _ok(msg: str) -> None:
    print(f"  [+] {msg}")


def _warn(msg: str) -> None:
    print(f"  [!] {msg}")


def _error(msg: str) -> None:
    print(f"  [X] {msg}", file=sys.stderr)


def _ask_yes_no(question: str, *, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    try:
        raw = input(f"  ? {question} [{hint}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default
    if not raw:
        return default
    return raw in ("y", "yes")


def _peek_program_slug(args: argparse.Namespace) -> str | None:
    """If --program was passed on the CLI, return it; else None."""
    return getattr(args, "program", None) or None


def _ask_program_name_early(questions: list[dict[str, Any]]) -> str:
    """Ask only the program_name question to establish the checkpoint slug."""
    for q in questions:
        if q.get("id") == "program_name":
            from otaman_cli.onboard.program_init.questions import ask_question
            ans = ask_question(q, {})
            if ans:
                return str(ans)
    return "my-program"


def _emit_audit_message(
    program_name: str,
    answers: dict[str, Any],
    platform_out: Path,
) -> None:
    """Emit a `program-init-completed` bus message for the audit trail.

    Spec requirement: "every program-init invocation SHALL emit a
    `program-init-completed` bus message."

    Tries to call the otaman bus CLI (`otaman send`).  Fails silently
    so the audit trail never breaks the init flow.
    """
    import datetime
    import os
    import subprocess

    processes = answers.get("processes") or []
    roles = answers.get("roles") or []
    scaffolded: list[str] = []
    if answers.get("scaffold_business"):
        scaffolded.append(f"{program_name}-business")
    if answers.get("scaffold_strategy"):
        scaffolded.append(f"{program_name}-strategy")

    body = (
        f"program: {program_name}\n"
        f"edition: {answers.get('active_edition', 'ce')}\n"
        f"mode: {answers.get('mode', 1)}\n"
        f"processes: {', '.join(sorted(processes)) or 'none'}\n"
        f"roles: {', '.join(sorted(roles)) or 'none'}\n"
        f"scaffolded_repos: {', '.join(scaffolded) or 'none'}\n"
        f"platform_yaml: {platform_out}\n"
        f"timestamp: {datetime.datetime.now(datetime.timezone.utc).isoformat()}\n"
        f"actor: {os.environ.get('USER', 'unknown')}\n"
    )

    try:
        subprocess.run(
            [
                "otaman", "send",
                "--to", "cli-agent",
                "--subject", f"program-init-completed: {program_name}",
                "--body", body,
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except Exception:
        pass  # audit trail failure must never abort the init flow
