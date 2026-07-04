"""`otaman git-host` — migrated from main.py (F020: the audit's own example
of a large single command group, ~449 lines across this module before the
move). Manages git host (GitHub / GitLab / Bitbucket / Azure DevOps)
integration: PAT wiring, PR listing/inspection, posting review comments.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root
from otaman_cli.main import UI


def cmd_git_host(args: list[str]) -> int:
    """Manage git host (GitHub / GitLab / Bitbucket / Azure DevOps) integration.

    Subcommands:
      detect [REPO]     Classify origin's git remote and print provider/slug.
      add               Interactive: walk the user through wiring a PAT.
      check             Load platform.yaml git_host:, resolve token, validate.
      list              Show git_host: config + origin-remote summary per repo.
    """
    UI.header("Otaman Git Host")
    sub = (args[0] if args else "list").lower()
    rest = args[1:]

    try:
        from otaman_core import git_host as gh
    except ImportError as e:
        UI.error(f"Failed to import git_host module: {e}")
        return 1

    if sub == "detect":
        target = Path(rest[0] if rest else ".").resolve()
        info = gh.detect_remote_for_repo(target)
        if info is None:
            UI.error(f"No parsable git remote found in {target}")
            return 1
        UI.kv("Repo", str(target))
        UI.kv("Provider", info.provider)
        UI.kv("Host", info.host)
        UI.kv("Slug", info.slug)
        if info.is_self_hosted:
            UI.muted("(self-hosted — host alone doesn't identify provider; "
                     "set `git_host.provider` explicitly)")
        return 0 if info.provider != "unknown" else 2

    if sub == "list":
        root = find_project_root()
        if not root:
            UI.error("Not in an otaman project")
            return 1
        cfg = gh.load_git_host_config(root)
        if cfg:
            UI.info("Configured git_host:")
            UI.kv("  Provider", cfg.provider)
            UI.kv("  Host", cfg.host)
            sources = ", ".join(
                s.get("type", "?") + ":" + str(s.get("name") or s.get("account") or "?")
                for s in cfg.token_ref.sources
            )
            UI.kv("  Token source chain", sources or "(empty)")
        else:
            UI.muted("No `git_host:` block in platform.yaml (run "
                     "`otaman git-host add` to wire one).")
        UI.info("Detected origin remotes:")
        remotes = gh.detect_remotes_for_maestro(root)
        if not remotes:
            UI.muted("  (no repos in platform.yaml)")
            return 0
        for name, info in remotes:
            if info is None:
                UI.muted(f"  {name:<25}  (no remote / not a git repo)")
            else:
                UI.kv(f"  {name}", f"{info.provider} · {info.slug}  [{info.host}]")
        return 0

    if sub == "check":
        root = find_project_root()
        if not root:
            UI.error("Not in an otaman project")
            return 1
        cfg = gh.load_git_host_config(root)
        if cfg is None:
            UI.error("No `git_host:` configured. Run `otaman git-host add` first.")
            return 1
        result = gh.resolve_and_validate(cfg, maestro_root=root)
        if result.ok:
            UI.ok(f"{cfg.provider} token valid "
                  f"(authenticated as {result.identity or '?'})")
            if result.scopes:
                UI.kv("  Scopes", ", ".join(result.scopes))
            return 0
        UI.error(f"Token validation failed: {result.error}")
        return 2

    if sub == "add":
        return _git_host_add_interactive(gh, rest)

    if sub == "pr":
        return _git_host_pr(gh, rest)

    if sub == "post-review":
        return _git_host_post_review(gh, rest)

    UI.error(f"Unknown subcommand: {sub}")
    UI.muted("Usage: otaman git-host [detect|list|check|add|pr|post-review] [args...]")
    return 1


def _git_host_current_branch(repo_dir: Path) -> str | None:
    """Best-effort ``git rev-parse --abbrev-ref HEAD`` in repo_dir."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    name = result.stdout.strip()
    return name if name and name != "HEAD" else None


def _git_host_resolve_repo(gh, root: Path, repo_arg: str | None):
    """Pick which managed repo the `pr` subcommand applies to.

    - If --repo=<name> given, use that entry from platform.yaml.
    - Else if running inside a managed repo, use it.
    - Else if only one repo is configured, use it.
    - Else error out listing the choices.

    Returns (repo_dir, RemoteInfo) or raises UserError via UI.error.
    """
    import yaml
    platform_yaml = root / "platform.yaml"
    data = yaml.safe_load(platform_yaml.read_text(encoding="utf-8")) or {} \
        if platform_yaml.is_file() else {}
    repos = [r for r in (data.get("repos") or []) if isinstance(r, dict)]

    cwd = Path.cwd().resolve()
    chosen = None
    if repo_arg:
        chosen = next((r for r in repos if r.get("name") == repo_arg), None)
        if chosen is None:
            return None, None
    else:
        # Prefer repo whose path contains cwd.
        for r in repos:
            path = r.get("path")
            if not path:
                continue
            resolved = (root / path).resolve()
            if cwd == resolved or cwd.is_relative_to(resolved):
                chosen = r
                break
        if chosen is None and len(repos) == 1:
            chosen = repos[0]

    if chosen is None:
        return None, None
    repo_dir = (root / chosen["path"]).resolve()
    info = gh.detect_remote_for_repo(repo_dir)
    return repo_dir, info


def _git_host_pr_target_branch(root: Path) -> int:
    """`otaman git-host pr target-branch` — git-flow-branch-config 2.1.

    Read-only advisory: resolves and prints a default target branch for
    `gh pr create -B <resolved>`, creating nothing. `otaman` has no
    PR-create capability today (no adapter `create_pr` method, no CLI
    action, across all 5 supported git-host providers) — building that is
    out of scope for this change (design.md's "declared-intent, not live
    behavior" v1 philosophy). Needs no `git_host:` config/token — this is
    pure `standards.git` resolution, independent of provider wiring.

    Resolution order:
      1. first `branch`-keyed entry (skipping any `tag_pattern`-keyed
         entries, which have no branch to target) in
         `standards.git.environments`
      2. `standards.git.development_branch`
      3. informational "no branch preference declared" (exit 0, not an
         error)
    """
    import yaml
    platform_yaml = root / "platform.yaml"
    data: dict = {}
    if platform_yaml.is_file():
        try:
            data = yaml.safe_load(platform_yaml.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}
    if not isinstance(data, dict):
        data = {}

    git_cfg = (data.get("standards") or {}).get("git") or {}
    if not isinstance(git_cfg, dict):
        git_cfg = {}

    environments = git_cfg.get("environments") or []
    if isinstance(environments, list):
        for entry in environments:
            if isinstance(entry, dict):
                branch = entry.get("branch")
                if branch:
                    print(branch)
                    UI.muted(f"(from standards.git.environments: {entry.get('environment', '?')})")
                    return 0

    dev_branch = git_cfg.get("development_branch")
    if dev_branch:
        print(dev_branch)
        UI.muted("(from standards.git.development_branch)")
        return 0

    UI.muted(
        "No branch preference declared "
        "(standards.git.environments / development_branch absent)"
    )
    return 0


def _git_host_pr(gh, args: list[str]) -> int:
    """`otaman git-host pr list|get|for-branch|comment|target-branch`"""
    if not args:
        UI.error("Missing subcommand")
        UI.muted("Usage: otaman git-host pr [list|get|for-branch|comment|target-branch] [args...]")
        return 1

    action = args[0].lower()
    rest = args[1:]

    # target-branch is pure platform.yaml config resolution — no
    # git_host:/adapter/repo-detection needed, unlike every other `pr`
    # action below, so it's dispatched before that setup.
    if action == "target-branch":
        root = find_project_root()
        if not root:
            UI.error("Not in an otaman project")
            return 1
        return _git_host_pr_target_branch(root)

    # Parse --repo NAME and --body TEXT out of rest.
    repo_arg: str | None = None
    body_arg: str | None = None
    positional: list[str] = []
    i = 0
    while i < len(rest):
        if rest[i] == "--repo" and i + 1 < len(rest):
            repo_arg = rest[i + 1]
            i += 2
        elif rest[i] == "--body" and i + 1 < len(rest):
            body_arg = rest[i + 1]
            i += 2
        else:
            positional.append(rest[i])
            i += 1

    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    cfg = gh.load_git_host_config(root)
    if cfg is None:
        UI.error("No `git_host:` configured. Run `otaman git-host add` first.")
        return 1

    repo_dir, info = _git_host_resolve_repo(gh, root, repo_arg)
    if info is None or info.provider == "unknown":
        UI.error(
            f"Can't determine repo slug. "
            f"Pass --repo <name> or run inside a managed repo with a parseable origin."
        )
        return 1

    try:
        adapter = gh.get_adapter(cfg, maestro_root=root)
    except gh.GitHostError as e:
        UI.error(str(e))
        return 2

    slug = info.slug
    try:
        if action == "list":
            prs = adapter.list_open_prs(slug)
            if not prs:
                UI.muted(f"No open PRs in {slug}")
                return 0
            UI.info(f"Open PRs in {slug}:")
            for pr in prs:
                draft = " [DRAFT]" if pr.draft else ""
                UI.kv(f"  #{pr.number}",
                      f"{pr.title}{draft}  by {pr.author}  ({pr.head_ref} → {pr.base_ref})")
            return 0

        if action == "get":
            if not positional:
                UI.error("Missing PR number: otaman git-host pr get <number>")
                return 1
            try:
                number = int(positional[0])
            except ValueError:
                UI.error(f"Invalid PR number: {positional[0]!r}")
                return 1
            pr = adapter.get_pr(slug, number)
            UI.kv("Number", f"#{pr.number}")
            UI.kv("Title", pr.title)
            UI.kv("State", pr.state + (" (draft)" if pr.draft else ""))
            UI.kv("Author", pr.author)
            UI.kv("Branches", f"{pr.head_ref} → {pr.base_ref}")
            UI.kv("SHA", pr.head_sha[:12])
            UI.kv("URL", pr.url)
            return 0

        if action == "for-branch":
            branch = positional[0] if positional else None
            if branch is None:
                branch = _git_host_current_branch(repo_dir or Path.cwd())
            if not branch:
                UI.error("Can't determine branch name (pass it as argument)")
                return 1
            pr = adapter.get_pr_for_branch(slug, branch)
            if pr is None:
                UI.muted(f"No open PR for {slug}:{branch}")
                return 0
            UI.kv("PR", f"#{pr.number} — {pr.title}")
            UI.kv("URL", pr.url)
            return 0

        if action == "comment":
            if not positional:
                UI.error("Missing PR number: otaman git-host pr comment <number> "
                         "[--body TEXT | via stdin]")
                return 1
            try:
                number = int(positional[0])
            except ValueError:
                UI.error(f"Invalid PR number: {positional[0]!r}")
                return 1
            body = body_arg
            if body is None:
                # Read from stdin if not given.
                if sys.stdin.isatty():
                    UI.error("--body TEXT required (or pipe body on stdin)")
                    return 1
                body = sys.stdin.read()
            if not body.strip():
                UI.error("Comment body is empty")
                return 1
            c = adapter.post_comment(slug, number, body)
            UI.ok(f"Posted comment #{c.id}")
            UI.kv("URL", c.url)
            return 0

        UI.error(f"Unknown pr subcommand: {action}")
        return 1
    except gh.GitHostError as e:
        UI.error(str(e))
        return 2
    except ValueError as e:
        UI.error(str(e))
        return 1


def _git_host_post_review(gh, args: list[str]) -> int:
    """`otaman git-host post-review [REVIEW_FILE] [--pr N] [--repo NAME]`

    Reads a review artifact from .agents/reviews/ (or the explicit path
    given) and posts it as a PR comment. Uses the current branch's PR
    if --pr isn't given. Prints a link to the posted comment.
    """
    pr_number: int | None = None
    repo_arg: str | None = None
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--pr" and i + 1 < len(args):
            try:
                pr_number = int(args[i + 1])
            except ValueError:
                UI.error(f"Invalid --pr value: {args[i + 1]!r}")
                return 1
            i += 2
        elif args[i] == "--repo" and i + 1 < len(args):
            repo_arg = args[i + 1]
            i += 2
        else:
            positional.append(args[i])
            i += 1

    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    cfg = gh.load_git_host_config(root)
    if cfg is None:
        UI.error("No `git_host:` configured. Run `otaman git-host add` first.")
        return 1

    # Find the review file.
    review_path: Path | None = None
    if positional:
        candidate = Path(positional[0])
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        if not candidate.is_file():
            UI.error(f"Review file not found: {candidate}")
            return 1
        review_path = candidate
    else:
        pending_dir = root / ".agents" / "reviews" / "pending"
        if not pending_dir.is_dir():
            UI.error(f"No .agents/reviews/pending/ directory at {root}")
            return 1
        reviews = sorted(pending_dir.glob("*.md"))
        if not reviews:
            UI.error(
                "No review files in .agents/reviews/pending/ — "
                "run /otaman:review first, or pass a path explicitly."
            )
            return 1
        review_path = reviews[-1]  # most recent
        UI.muted(f"Using latest review: {review_path.name}")

    body = review_path.read_text(encoding="utf-8")
    if not body.strip():
        UI.error(f"Review file is empty: {review_path}")
        return 1

    # Resolve repo + PR.
    repo_dir, info = _git_host_resolve_repo(gh, root, repo_arg)
    if info is None or info.provider == "unknown":
        UI.error(
            "Can't determine repo slug. "
            "Pass --repo <name> or run inside a managed repo."
        )
        return 1

    try:
        adapter = gh.get_adapter(cfg, maestro_root=root)
    except gh.GitHostError as e:
        UI.error(str(e))
        return 2

    if pr_number is None:
        branch = _git_host_current_branch(repo_dir or Path.cwd())
        if not branch:
            UI.error("Can't determine current branch — pass --pr N")
            return 1
        try:
            pr = adapter.get_pr_for_branch(info.slug, branch)
        except gh.GitHostError as e:
            UI.error(str(e))
            return 2
        if pr is None:
            UI.error(f"No open PR for {info.slug}:{branch} (pass --pr N explicitly)")
            return 1
        pr_number = pr.number
        UI.muted(f"Resolved PR: #{pr_number} ({pr.title})")

    # legacy: wrap with plugin attribution; repo still named maestro-plugin on GitHub
    wrapped = (
        f"> _Posted by [otaman-plugin](https://github.com/inprimex/maestro-plugin) "  # legacy: GitHub repo not yet renamed
        f"from `{review_path.name}`_\n\n"
        f"{body.rstrip()}\n"
    )

    try:
        c = adapter.post_comment(info.slug, pr_number, wrapped)
    except gh.GitHostError as e:
        UI.error(str(e))
        return 2

    UI.ok(f"Posted review as comment on {info.slug}#{pr_number}")
    UI.kv("Comment", f"#{c.id}")
    UI.kv("URL", c.url)
    return 0


def _git_host_add_interactive(gh, args: list[str]) -> int:
    """Walk the user through wiring a PAT: detect, confirm, print
    exactly the lines to add to platform.yaml + .otaman/secrets.env."""
    root = find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    # Try to auto-detect from the first repo that has a remote.
    remotes = gh.detect_remotes_for_maestro(root)
    detected = next((info for _name, info in remotes if info is not None), None)

    if detected and detected.provider != "unknown":
        UI.ok(f"Detected {detected.provider} at {detected.host} "
              f"(from {detected.slug})")
        provider = detected.provider
        host = detected.host
    else:
        if detected:
            UI.muted(f"Remote host {detected.host} is self-hosted — pick a provider.")
        UI.info("Supported: github / gitlab / bitbucket / azure-devops")
        provider = input("Provider: ").strip().lower()
        if provider not in ("github", "gitlab", "bitbucket", "azure-devops"):
            UI.error(f"Unknown provider: {provider!r}")
            return 1
        default_host = gh.default_host_for(provider)
        host_input = input(f"Host [{default_host}]: ").strip()
        host = host_input or default_host

    # Token env var name.
    default_env = f"OTAMAN_{provider.upper().replace('-', '_')}_TOKEN"
    env_name = input(f"Env var name for the PAT [{default_env}]: ").strip() or default_env

    UI.info("")
    UI.info("To finish setup:")
    UI.info("")
    UI.action(f"1. Generate a PAT on {host} with the scopes you need "
              f"(read-only is enough for Phase 1).")
    UI.info("")
    UI.action(f"2. Add the token to .otaman/secrets.env "
              f"(gitignored, mode 0600):")
    UI.muted(f"   echo '{env_name}=<paste-token-here>' >> .otaman/secrets.env")
    UI.muted(f"   chmod 600 .otaman/secrets.env")
    UI.info("")
    UI.action(f"3. Add this block to platform.yaml:")
    UI.muted("")
    UI.muted(f"   git_host:")
    UI.muted(f"     provider: {provider}")
    UI.muted(f"     host: {host}")
    UI.muted(f"     token:")
    UI.muted(f"       sources:")
    UI.muted(f"         - {{ type: env,    name: {env_name} }}")
    UI.muted(f"         - {{ type: dotenv, name: {env_name} }}")
    UI.muted("")
    UI.action(f"4. Verify: `otaman git-host check`")
    return 0


register(CommandSpec(
    name="git-host",
    handler=cmd_git_host,
    help="Git host PAT + PR/MR API: detect, list, check, add, pr, post-review",
))
