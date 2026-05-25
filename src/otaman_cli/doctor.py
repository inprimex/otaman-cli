#!/usr/bin/env python3
"""Otaman Doctor — validate environment readiness for agent development.

Checks:
- Git identity (user.name, user.email)
- Git platform CLI (gh, glab) — installed, authenticated
- Runtimes per repo tech stack (node, python, dotnet)
- Claude CLI installed
- SSH keys for git push
- MCP server dependencies

Output: JSON report to stdout with issues and fixes.

Usage:
    python doctor.py [project-root]

Exit codes:
    0 — all checks passed
    1 — issues found (with fix suggestions)
    2 — error
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None


def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr).

    If the command isn't in PATH, tries to resolve it via _which().
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        # Try resolving the binary via _which
        resolved = _which(cmd[0])
        if resolved and resolved != cmd[0]:
            try:
                r = subprocess.run([resolved] + cmd[1:], capture_output=True, text=True, timeout=timeout)
                return r.returncode, r.stdout.strip(), r.stderr.strip()
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        return -1, "", ""
    except subprocess.TimeoutExpired:
        return -1, "", ""


def _which(name: str) -> str | None:
    """Find executable in PATH or common user-install locations."""
    found = shutil.which(name)
    if found:
        return found
    # Check common user-install locations (not in PATH for non-interactive SSH)
    home = Path.home()
    candidates = [
        home / ".dotnet" / name,                    # dotnet-install.sh
        home / ".cargo" / "bin" / name,             # rustup
        home / ".local" / "bin" / name,             # pip --user
        home / "go" / "bin" / name,                 # go install
    ]
    # nvm-installed node/npm/claude
    nvm_dir = Path(os.environ.get("NVM_DIR", home / ".nvm"))
    if nvm_dir.is_dir():
        for node_ver in sorted((nvm_dir / "versions" / "node").glob("v*"), reverse=True):
            candidates.append(node_ver / "bin" / name)
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def check_git_identity() -> dict[str, Any]:
    """Check git user.name and user.email are configured."""
    result: dict[str, Any] = {"check": "git_identity", "status": "ok", "details": {}}

    rc, name, _ = _run(["git", "config", "--global", "user.name"])
    rc2, email, _ = _run(["git", "config", "--global", "user.email"])

    result["details"]["user_name"] = name or None
    result["details"]["user_email"] = email or None

    issues = []
    if not name:
        issues.append({
            "issue": "Git user.name not configured",
            "fix": 'git config --global user.name "Your Name"',
        })
    if not email:
        issues.append({
            "issue": "Git user.email not configured",
            "fix": 'git config --global user.email "you@example.com"',
        })

    if issues:
        result["status"] = "fail"
        result["issues"] = issues
    return result


def _git_host_pat_is_live(project_root: Path) -> bool:
    """True when platform.yaml has a `git_host:` block AND the PAT validates.

    Used by check_git_platform to decide whether the standalone CLI tool
    (glab/gh/...) is *required* or merely convenient. When the API
    integration is live, cli auth is the secondary path — an install/auth
    gap shouldn't block the user's doctor run.
    """
    try:
        from otaman_core import git_host as gh  # type: ignore
    except ImportError:
        return False
    cfg = gh.load_git_host_config(project_root)
    if cfg is None:
        return False
    try:
        result = gh.resolve_and_validate(cfg, maestro_root=project_root)
    except Exception:  # noqa: BLE001
        return False
    return bool(result.ok)


def check_git_platform(repos: list[dict[str, Any]], project_root: Path) -> dict[str, Any]:
    """Detect git platform from repo remotes and check CLI tools."""
    result: dict[str, Any] = {"check": "git_platform", "status": "ok", "details": {}}

    # Detect platform from remotes
    platforms: dict[str, int] = {}
    for repo in repos:
        repo_dir = project_root / repo["path"]
        if not repo_dir.is_dir():
            continue
        rc, remotes, _ = _run(["git", "-C", str(repo_dir), "remote", "-v"])
        if rc != 0:
            continue
        for line in remotes.splitlines():
            if "github.com" in line:
                platforms["github"] = platforms.get("github", 0) + 1
            elif "gitlab.com" in line or "gitlab" in line.lower():
                platforms["gitlab"] = platforms.get("gitlab", 0) + 1
            elif "bitbucket.org" in line:
                platforms["bitbucket"] = platforms.get("bitbucket", 0) + 1
            elif "dev.azure.com" in line or "visualstudio.com" in line:
                platforms["azure-devops"] = platforms.get("azure-devops", 0) + 1

    if not platforms:
        result["status"] = "warn"
        result["issues"] = [{"issue": "No git remotes detected — repos may not be pushed", "fix": "git remote add origin <url>"}]
        return result

    # Primary platform
    primary = max(platforms, key=platforms.get)
    result["details"]["provider"] = primary
    result["details"]["repo_count"] = platforms[primary]

    # Check CLI tool
    cli_map = {
        "github": ("gh", "gh auth status", "sudo apt install gh || brew install gh"),
        "gitlab": ("glab", "glab auth status", "sudo apt install glab || brew install glab"),
        "bitbucket": ("bb", "bb auth status", "pip install bitbucket-cli"),
        "azure-devops": ("az", "az devops configure --list", "curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash"),
    }

    cli_name, auth_cmd, install_cmd = cli_map.get(primary, ("", "", ""))
    issues = []

    # If the native `git_host:` API integration is configured AND its PAT
    # validates, the standalone CLI (glab/gh/bb/az) is no longer required
    # for otaman features — it's just a convenience for terminal browsing.
    # Downgrade install/auth gaps from "fail" to "warn" in that case so the
    # overall doctor run doesn't flag as broken.
    api_live = _git_host_pat_is_live(project_root)
    result["details"]["git_host_api_live"] = api_live
    cli_severity = "medium" if api_live else "high"

    if cli_name:
        cli_path = _which(cli_name)
        result["details"]["cli"] = cli_name
        result["details"]["cli_installed"] = bool(cli_path)

        if not cli_path:
            issue_text = f"{cli_name} CLI not installed"
            issue_text += (
                " — optional, `otaman git-host` covers it via API"
                if api_live else " — agents cannot create PRs"
            )
            issues.append({
                "issue": issue_text,
                "fix": install_cmd,
                "severity": cli_severity,
            })
        else:
            # Check authentication
            rc, out, err = _run(auth_cmd.split())
            authenticated = rc == 0
            result["details"]["authenticated"] = authenticated
            if not authenticated:
                issue_text = f"{cli_name} CLI installed but not authenticated"
                if api_live:
                    issue_text += (
                        " — optional, `otaman git-host` is authenticated "
                        "via platform.yaml's `git_host:` block"
                    )
                issues.append({
                    "issue": issue_text,
                    "fix": f"{cli_name} auth login",
                    "severity": cli_severity,
                })

    result["details"]["pr_enabled"] = (
        (bool(cli_name) and result["details"].get("cli_installed")
         and result["details"].get("authenticated", False))
        or api_live
    )

    if issues:
        # Only critical/high severity flips the overall status to "fail";
        # medium (when api_live) becomes a warning.
        has_high = any(i.get("severity") in ("critical", "high") for i in issues)
        result["status"] = "fail" if has_high else "warn"
        result["issues"] = issues
    return result


def check_runtimes(repos: list[dict[str, Any]], project_root: Path) -> dict[str, Any]:
    """Check that required runtimes are installed based on repo tech stacks."""
    result: dict[str, Any] = {"check": "runtimes", "status": "ok", "details": {}, "issues": []}

    # Collect required runtimes from tech stacks
    needs_node = False
    needs_python = False
    needs_dotnet = False
    needs_rust = False
    needs_go = False
    needs_java = False

    for repo in repos:
        tech = repo.get("tech", [])
        if any(t in tech for t in ("nodejs", "typescript", "react", "nextjs", "vue", "angular", "express", "nestjs", "svelte")):
            needs_node = True
        if any(t in tech for t in ("python", "python-ml", "django", "flask", "fastapi")):
            needs_python = True
        if any(t in tech for t in ("csharp", "dotnet")):
            needs_dotnet = True
        if "rust" in tech:
            needs_rust = True
        if "go" in tech:
            needs_go = True
        if any(t in tech for t in ("java", "kotlin")):
            needs_java = True

    # Check each required runtime
    runtimes: dict[str, Any] = {}

    if needs_node:
        node_path = _which("node")
        if node_path:
            rc, ver, _ = _run([node_path, "--version"])
            nvm_dir = os.environ.get("NVM_DIR", os.path.expanduser("~/.nvm"))
            has_nvm = os.path.isdir(nvm_dir)
            in_path = shutil.which("node") is not None
            runtimes["nodejs"] = {"version": ver, "manager": "nvm" if has_nvm else "system", "path": node_path}
            if not in_path:
                runtimes["nodejs"]["note"] = "Found but not in PATH (source ~/.nvm/nvm.sh)"
            # Check npm
            npm_path = _which("npm")
            if npm_path:
                rc, npm_ver, _ = _run(["npm", "--version"])
                runtimes["npm"] = {"version": npm_ver}
        else:
            result["issues"].append({
                "issue": "Node.js not found — needed for JS/TS repos",
                "fix": "curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash && nvm install 22",
                "severity": "high",
            })

    if needs_python:
        py_path = _which("python3") or _which("py") or _which("python")
        if py_path:
            rc, ver, _ = _run([py_path, "--version"])
            runtimes["python"] = {"version": ver, "path": py_path}
            # Check pip
            rc, pip_ver, _ = _run([py_path, "-m", "pip", "--version"])
            if rc == 0:
                runtimes["pip"] = {"version": pip_ver.split()[1] if pip_ver else ""}
            else:
                result["issues"].append({
                    "issue": "pip not available — needed for Python dependency management",
                    "fix": "curl -sSL https://bootstrap.pypa.io/get-pip.py | python3 - --user",
                    "severity": "medium",
                })
        else:
            result["issues"].append({
                "issue": "Python not found — needed for ML/backend repos",
                "fix": "sudo apt install python3 python3-pip",
                "severity": "high",
            })

    if needs_dotnet:
        dotnet_path = _which("dotnet")
        if dotnet_path:
            rc, ver, _ = _run([dotnet_path, "--version"])
            in_path = shutil.which("dotnet") is not None
            runtimes["dotnet"] = {"version": ver, "path": dotnet_path}
            if not in_path:
                runtimes["dotnet"]["note"] = "Found but not in PATH (source ~/.bashrc or add ~/.dotnet to PATH)"
            # Check SDKs
            rc, sdks, _ = _run([dotnet_path, "--list-sdks"])
            if sdks:
                runtimes["dotnet"]["sdks"] = sdks.splitlines()
        else:
            result["issues"].append({
                "issue": ".NET SDK not found — needed for C# repos",
                "fix": "See https://dot.net/install or: sudo apt install dotnet-sdk-8.0",
                "severity": "high",
            })

    if needs_rust:
        cargo_path = _which("cargo")
        if cargo_path:
            rc, ver, _ = _run(["rustc", "--version"])
            runtimes["rust"] = {"version": ver}
        else:
            result["issues"].append({
                "issue": "Rust not found",
                "fix": "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh",
                "severity": "high",
            })

    if needs_go:
        go_path = _which("go")
        if go_path:
            rc, ver, _ = _run(["go", "version"])
            runtimes["go"] = {"version": ver}
        else:
            result["issues"].append({
                "issue": "Go not found",
                "fix": "See https://go.dev/dl/ or: sudo apt install golang",
                "severity": "high",
            })

    if needs_java:
        java_path = _which("java")
        if java_path:
            rc, ver, _ = _run(["java", "--version"])
            runtimes["java"] = {"version": ver.splitlines()[0] if ver else ""}
        else:
            result["issues"].append({
                "issue": "Java not found",
                "fix": "sudo apt install openjdk-17-jdk",
                "severity": "high",
            })

    result["details"] = runtimes
    if result["issues"]:
        result["status"] = "fail"
    return result


def check_claude_cli() -> dict[str, Any]:
    """Check Claude CLI is installed and accessible."""
    result: dict[str, Any] = {"check": "claude_cli", "status": "ok", "details": {}}

    claude_path = _which("claude")
    if claude_path:
        rc, ver, _ = _run(["claude", "--version"])
        result["details"]["version"] = ver
        result["details"]["path"] = claude_path
    else:
        # Check if nvm node has it
        nvm_dir = os.environ.get("NVM_DIR", os.path.expanduser("~/.nvm"))
        nvm_claude = Path(nvm_dir) / "versions" / "node"
        found = False
        if nvm_claude.is_dir():
            for node_ver in nvm_claude.iterdir():
                candidate = node_ver / "bin" / "claude"
                if candidate.exists():
                    result["details"]["path"] = str(candidate)
                    result["details"]["note"] = "Found in nvm but not in PATH — source ~/.nvm/nvm.sh first"
                    found = True
                    break

        if not found:
            result["status"] = "fail"
            result["issues"] = [{
                "issue": "Claude CLI not installed",
                "fix": "npm install -g @anthropic-ai/claude-code",
                "severity": "critical",
            }]
        else:
            result["status"] = "warn"
            result["issues"] = [{
                "issue": "Claude CLI found in nvm but not in current PATH",
                "fix": "Add to .bashrc: export NVM_DIR=\"$HOME/.nvm\" && [ -s \"$NVM_DIR/nvm.sh\" ] && . \"$NVM_DIR/nvm.sh\"",
                "severity": "medium",
            }]

    return result


def check_ssh_keys(repos: list[dict[str, Any]], project_root: Path) -> dict[str, Any]:
    """Check SSH keys are available for git push."""
    result: dict[str, Any] = {"check": "ssh_keys", "status": "ok", "details": {}}

    # Check if any repos use SSH remotes
    ssh_repos = 0
    https_repos = 0
    for repo in repos:
        repo_dir = project_root / repo["path"]
        if not repo_dir.is_dir():
            continue
        rc, remotes, _ = _run(["git", "-C", str(repo_dir), "remote", "-v"])
        if rc != 0:
            continue
        for line in remotes.splitlines():
            if "git@" in line:
                ssh_repos += 1
                break
            elif "https://" in line:
                https_repos += 1
                break

    result["details"]["ssh_repos"] = ssh_repos
    result["details"]["https_repos"] = https_repos

    if ssh_repos > 0:
        # Check for SSH keys in common locations
        home = Path.home()
        ssh_dir = home / ".ssh"
        keys: list[Path] = []
        if ssh_dir.is_dir():
            keys.extend(ssh_dir.glob("id_*"))
            keys.extend(ssh_dir.glob("*.pub"))
            keys.extend(ssh_dir.glob("*.pem"))
            keys.extend(ssh_dir.glob("*_key"))
            keys.extend(ssh_dir.glob("github*"))
        # Also check home dir for standalone key files
        for pattern in ("*.prv", "*_key", "github_key", "*.pem"):
            keys.extend(home.glob(pattern))
        # Check git SSH config
        rc, git_ssh, _ = _run(["git", "config", "--global", "core.sshCommand"])
        if git_ssh:
            result["details"]["git_ssh_command"] = git_ssh

        # Also check if gh CLI has auth (covers SSH via gh)
        gh_path = _which("gh")
        if gh_path:
            rc, _, _ = _run([gh_path, "auth", "status"])
            if rc == 0:
                result["details"]["gh_authenticated"] = True

        result["details"]["ssh_keys_found"] = len(keys)
        if keys:
            result["details"]["key_locations"] = [str(k) for k in keys[:5]]

        if not keys and not result["details"].get("gh_authenticated"):
            result["status"] = "warn"
            result["issues"] = [{
                "issue": f"{ssh_repos} repos use SSH remotes but no SSH keys found",
                "fix": 'ssh-keygen -t ed25519 -C "your@email.com"',
                "severity": "medium",
            }]

    return result


def check_mcp_deps() -> dict[str, Any]:
    """Check MCP server Python dependencies are available."""
    result: dict[str, Any] = {"check": "mcp_dependencies", "status": "ok", "details": {}}

    issues = []
    for module, pkg in [("fastmcp", "fastmcp"), ("yaml", "pyyaml")]:
        try:
            __import__(module)
            result["details"][module] = "installed"
        except ImportError:
            issues.append({
                "issue": f"Python module '{module}' not installed — MCP server needs it",
                "fix": f"pip3 install --user {pkg}",
                "severity": "high",
            })

    if issues:
        result["status"] = "fail"
        result["issues"] = issues
    return result


def check_tmux() -> dict[str, Any]:
    """Check tmux is installed locally — recommended for remote SSH work.

    tmux lets a Claude Code session survive SSH disconnects: when an unstable
    network drops the connection, the agent process keeps running on the
    server and reattaches when the user relaunches the tab. Without tmux,
    a network drop kills the in-flight session and loses any unstaged work.

    The launcher's per-connection ``reliability: tmux`` (or ``tmux+mosh``)
    setting wraps the inner command in ``tmux new -A -s '<session>' bash -lc
    '<cmd>'`` so this works automatically. tmux must be installed on the
    REMOTE host — this check confirms it's available locally as a proxy
    (most users SSH to a server they also use directly).
    """
    result: dict[str, Any] = {"check": "tmux", "status": "ok", "details": {}}

    tmux_path = _which("tmux")
    if tmux_path:
        rc, ver, _ = _run(["tmux", "-V"])
        result["details"]["version"] = ver.strip() if ver else "installed"
        result["details"]["path"] = tmux_path
    else:
        result["status"] = "warn"
        result["issues"] = [{
            "issue": "tmux not installed — highly recommended for remote SSH work",
            "fix": "apt install tmux  /  brew install tmux  /  see references/connection-resilience.md",
            "severity": "low",
        }]
    return result


def check_openspec(config: dict[str, Any], project_root: Path) -> dict[str, Any]:
    """Check OpenSpec CLI is installed if project uses openspec format."""
    result: dict[str, Any] = {"check": "openspec", "status": "ok", "details": {}}

    specs = config.get("specs", {})
    fmt = specs.get("format", "")
    if fmt != "openspec":
        result["details"]["skipped"] = "specs.format is not openspec"
        return result

    result["details"]["format"] = "openspec"
    specs_path = specs.get("path", "")
    if specs_path:
        result["details"]["specs_path"] = specs_path

    # Check openspec CLI is installed
    openspec_path = _which("openspec")
    if openspec_path:
        rc, ver, _ = _run([openspec_path, "--version"])
        result["details"]["version"] = ver
        result["details"]["path"] = openspec_path
    else:
        # Check if it's installed as npm package but not in PATH (nvm scenario)
        npx_path = _which("npx")
        if npx_path:
            rc, ver, _ = _run([npx_path, "openspec", "--version"])
            if rc == 0:
                result["details"]["version"] = ver
                result["details"]["via_npx"] = True
                result["status"] = "warn"
                result["issues"] = [{
                    "issue": "OpenSpec available via npx but not as global command",
                    "fix": "npm install -g @fission-ai/openspec@latest",
                    "severity": "low",
                }]
                return result

        result["status"] = "fail"
        result["issues"] = [{
            "issue": "OpenSpec CLI not installed — specs.format is 'openspec' but the CLI is missing",
            "fix": "npm install -g @fission-ai/openspec@latest",
            "severity": "high",
        }]

    # Check specs repo exists
    if specs_path:
        specs_dir = (project_root / specs_path).resolve()
        result["details"]["specs_dir_exists"] = specs_dir.is_dir()
        if not specs_dir.is_dir():
            result.setdefault("issues", []).append({
                "issue": f"Specs directory not found: {specs_path}",
                "fix": f"Clone the specs repo or update specs.path in platform.yaml",
                "severity": "high",
            })
            result["status"] = "fail"

    return result


def check_maestro_plugin(project_root: Path) -> dict[str, Any]:
    """Check otaman plugin is properly set up."""
    result: dict[str, Any] = {"check": "maestro_plugin", "status": "ok", "details": {}}
    issues = []

    # Check .agents/ exists
    agents_dir = project_root / ".agents"
    if not agents_dir.is_dir():
        issues.append({
            "issue": ".agents/ directory not found — run otaman init first",
            "fix": "otaman init",
            "severity": "critical",
        })

    # Check ownership.json
    ownership = agents_dir / "ownership.json"
    if agents_dir.is_dir() and not ownership.exists():
        issues.append({
            "issue": "ownership.json missing — run otaman init",
            "fix": "otaman init",
            "severity": "critical",
        })

    # Check platform.yaml
    config = project_root / "platform.yaml"
    if not config.exists():
        issues.append({
            "issue": "platform.yaml not found",
            "fix": "otaman scan",
            "severity": "critical",
        })

    # Check .mcp.json in repos
    if config.exists() and yaml:
        with open(config, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        repos_without_mcp = []
        for repo in cfg.get("repos", []):
            repo_dir = project_root / repo["path"]
            if repo_dir.is_dir() and not (repo_dir / ".mcp.json").exists():
                repos_without_mcp.append(repo["name"])
        if repos_without_mcp:
            # Show the repo names so the user knows which one(s) to fix —
            # not just a count. Re-running `otaman init` is the right path
            # but it must be run from the otaman folder, not from inside a
            # managed repo, so include the absolute path.
            names = ", ".join(repos_without_mcp)
            issues.append({
                "issue": f"{len(repos_without_mcp)} repo(s) missing .mcp.json (MCP tools won't work): {names}",
                "fix": f"cd {project_root} && otaman init   # re-run from otaman folder to install .mcp.json",
                "severity": "high",
            })
            result["details"]["repos_without_mcp"] = repos_without_mcp

    if issues:
        result["status"] = "fail"
        result["issues"] = issues
    return result


def check_secrets_leaks(project_root: Path) -> dict[str, Any]:
    """Check that .otaman/secrets.env has never been committed and is gitignored.

    Three things can go wrong:
      1. File is committed to HEAD (currently tracked).
      2. File appears anywhere in git history (past commit with secrets).
      3. .gitignore doesn't list .otaman/secrets.env.
      4. File mode is looser than 0600 on POSIX.

    Severity levels:
      - Tracked in HEAD or in history => CRITICAL (rotate tokens + purge history)
      - Missing gitignore entry       => HIGH (file could be accidentally staged)
      - Loose mode                     => MEDIUM
    """
    result: dict[str, Any] = {
        "check": "secrets_leaks",
        "status": "ok",
        "details": {},
    }
    issues: list[dict[str, Any]] = []

    secrets_path = project_root / ".otaman" / "secrets.env"
    result["details"]["secrets_env_present"] = secrets_path.is_file()

    # Only run git checks if the folder is a git repo.
    is_git = (project_root / ".git").exists()
    result["details"]["git_repo"] = is_git

    if is_git:
        # 1. Tracked in HEAD?
        rc, out, _ = _run(
            ["git", "-C", str(project_root), "ls-files", "--error-unmatch",
             ".otaman/secrets.env"],
            timeout=10,
        )
        tracked = rc == 0
        result["details"]["tracked_in_head"] = tracked
        if tracked:
            issues.append({
                "issue": ".otaman/secrets.env is tracked in git — secrets may leak on push",
                "fix": (
                    "Rotate any tokens stored here, then:\n"
                    "  git -C <otaman-folder> rm --cached .otaman/secrets.env\n"
                    "  git -C <otaman-folder> commit -m 'untrack secrets.env'\n"
                    "  (see: https://github.com/newren/git-filter-repo to purge from history)"
                ),
                "severity": "critical",
            })

        # 2. Appears anywhere in history?
        rc, out, _ = _run(
            ["git", "-C", str(project_root), "log", "--all", "--pretty=format:%H",
             "--", ".otaman/secrets.env"],
            timeout=15,
        )
        in_history = bool(out.strip())
        result["details"]["in_git_history"] = in_history
        if in_history and not tracked:
            # Only flag separately if it's not also currently tracked
            commit_count = len([l for l in out.splitlines() if l.strip()])
            issues.append({
                "issue": (
                    f".otaman/secrets.env appears in {commit_count} past commit(s) — "
                    f"secrets may still be exposed in history"
                ),
                "fix": (
                    "Rotate any tokens that may have been committed, then purge:\n"
                    "  git filter-repo --path .otaman/secrets.env --invert-paths\n"
                    "  (or: git filter-branch ... --force-push afterwards)"
                ),
                "severity": "critical",
            })
    else:
        result["details"]["tracked_in_head"] = False
        result["details"]["in_git_history"] = False

    # 3. Gitignore entry present?
    gitignore = project_root / ".gitignore"
    required_entries = (".otaman/secrets.env",)
    missing_gi: list[str] = []
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        entries = {ln.strip() for ln in content.splitlines()}
        for req in required_entries:
            if req not in entries:
                missing_gi.append(req)
    else:
        missing_gi = list(required_entries)
    result["details"]["gitignore_missing"] = missing_gi
    if missing_gi:
        # Show the resolved gitignore path so the user knows WHICH file to
        # edit. When `otaman doctor` is run from a managed repo (not the
        # otaman folder), project_root resolves to the otaman folder via
        # the .otaman marker (or legacy: .maestro) — and that's the .gitignore that needs the
        # entry, not the current repo's. Showing the absolute path
        # eliminates the foot-gun.
        gi_display = str(gitignore)
        issues.append({
            "issue": f".gitignore missing entries: {', '.join(missing_gi)} (file: {gi_display})",
            "fix": (
                f"Run `otaman init` from the otaman folder ({project_root}) to regenerate, "
                f"or add manually:\n"
                f"  echo '.otaman/secrets.env' >> {gi_display}"
            ),
            "severity": "high",
        })

    # 4. Mode check (POSIX only).
    if secrets_path.is_file() and os.name == "posix":
        try:
            mode = secrets_path.stat().st_mode & 0o777
            result["details"]["mode"] = f"{mode:o}"
            if mode not in (0o600, 0o400):
                issues.append({
                    "issue": f".otaman/secrets.env mode is {mode:o} (should be 600 or 400)",
                    "fix": f"chmod 600 {secrets_path}",
                    "severity": "medium",
                })
        except OSError:
            pass

    if issues:
        # Elevate overall status to the worst severity present.
        has_critical = any(i["severity"] == "critical" for i in issues)
        result["status"] = "fail" if has_critical else "warn"
        result["issues"] = issues
    return result


def check_git_host(project_root: Path) -> dict[str, Any]:
    """Validate the `git_host:` PAT if configured; summarize detected remotes.

    Four states the user cares about:
      - No git_host: block          → status=ok, informational detail
      - Block present, token resolves + API 200 → status=ok, identity in detail
      - Block present, token missing from source chain → warn
      - Block present, token rejected by the provider (403/401) → fail

    Also surfaces when platform.yaml's declared provider doesn't match
    what the repos' origin remotes look like (common on copy-pasted
    configs).
    """
    result: dict[str, Any] = {
        "check": "git_host",
        "status": "ok",
        "details": {},
    }

    try:
        from otaman_core import git_host as gh  # type: ignore
    except ImportError as e:
        result["details"]["import_error"] = str(e)
        return result

    # Summarize origin remotes per repo (informational).
    detected = gh.detect_remotes_for_maestro(project_root)
    summary = []
    providers_seen: set[str] = set()
    for name, info in detected:
        if info is None:
            summary.append({"repo": name, "remote": None})
        else:
            providers_seen.add(info.provider)
            summary.append({
                "repo": name,
                "provider": info.provider,
                "host": info.host,
                "slug": info.slug,
            })
    result["details"]["remotes"] = summary

    cfg = gh.load_git_host_config(project_root)
    if cfg is None:
        result["details"]["configured"] = False
        return result

    result["details"]["configured"] = True
    result["details"]["provider"] = cfg.provider
    result["details"]["host"] = cfg.host

    issues: list[dict[str, Any]] = []

    # Config/provider mismatch — usually a stale copy-paste.
    if providers_seen and cfg.provider not in providers_seen \
            and "unknown" not in providers_seen:
        issues.append({
            "issue": (
                f"platform.yaml git_host.provider={cfg.provider!r} but "
                f"origin remotes point at {sorted(providers_seen)}"
            ),
            "fix": (
                "Either update `git_host.provider` in platform.yaml to match "
                "your remotes, or re-point origin if it's wrong."
            ),
            "severity": "medium",
        })

    # Token resolution + validation.
    try:
        validation = gh.resolve_and_validate(cfg, maestro_root=project_root)
    except Exception as e:  # noqa: BLE001
        validation = gh.ValidationResult(ok=False, error=f"exception: {e}")

    result["details"]["token_ok"] = validation.ok
    if validation.ok:
        if validation.identity:
            result["details"]["authenticated_as"] = validation.identity
        if validation.scopes:
            result["details"]["scopes"] = validation.scopes
    else:
        # Distinguish "token absent" (likely user forgot to set env var)
        # from "token rejected" (likely expired / wrong scope).
        err = validation.error or ""
        if "not found" in err:
            issues.append({
                "issue": "git_host token not resolvable from configured sources",
                "fix": (
                    "Set the env var named in platform.yaml's git_host.token "
                    "sources, or add it to .otaman/secrets.env."
                ),
                "severity": "high",
            })
        else:
            issues.append({
                "issue": f"git_host token rejected by provider: {err}",
                "fix": (
                    "Token may have expired or been revoked. Regenerate a "
                    "PAT and re-add to .otaman/secrets.env."
                ),
                "severity": "high",
            })

    if issues:
        result["status"] = "warn"  # token issues don't break core workflow
        result["issues"] = issues
    return result


def check_plugin_doctor(project_root: Path) -> dict[str, Any]:
    """Run plugin-side doctor checks (M4_PLUGIN_DIR_DRIFT, M4_WSL_PATH_UNDER_SSH,
    M13B_MISSING_CONTINUE_FLAG) via otaman_plugin.doctor_checks.run_all_checks().

    Gracefully degrades when otaman_plugin is not importable — returns ok with
    a note so the rest of doctor still runs.
    """
    result: dict[str, Any] = {"check": "plugin_doctor", "status": "ok", "details": {}}
    try:
        from otaman_plugin.doctor_checks import run_all_checks
    except ImportError:
        result["details"]["skipped"] = "otaman_plugin not importable"
        return result

    warnings = run_all_checks(project_root)
    if not warnings:
        return result

    issues = []
    for w in warnings:
        repo_tag = f"{w.repo}: " if w.repo else ""
        issue: dict[str, Any] = {
            "issue": f"{repo_tag}{w.message} ({w.code})",
            "severity": {"info": "low", "warn": "medium", "error": "high"}.get(w.severity, "medium"),
        }
        if w.hint:
            issue["fix"] = w.hint
        issues.append(issue)

    has_error = any(w.severity == "error" for w in warnings)
    result["status"] = "fail" if has_error else "warn"
    result["issues"] = issues
    return result


def check_launch_commands_resume(repos: list[dict[str, Any]]) -> dict[str, Any]:
    """Warn when a repo's launch_commands invoke claude without -c/--resume.

    Stale platform.yaml entries that bypass the launcher rewrite can omit -c,
    causing SSH reconnects to start a fresh Claude session instead of resuming
    the in-progress one. (M-13b in finish-maestro-to-otaman-migration: legacy string sweep)
    """
    import re as _re
    result: dict[str, Any] = {"check": "launch_commands_resume", "status": "ok", "details": {}}
    issues = []
    _claude_pat = _re.compile(r'\bclaude\b')
    _resume_pat = _re.compile(r'(?:^|\s)(?:-c\b|--continue\b|--resume\b)')

    for repo in repos:
        name = repo.get("name", "?")
        cmds = repo.get("launch_commands")
        if not cmds:
            continue
        if isinstance(cmds, str):
            cmds = [cmds]
        for cmd in cmds:
            if _claude_pat.search(cmd) and not _resume_pat.search(cmd):
                issues.append({
                    "issue": (
                        f"repo `{name}`: launch_commands lacks -c — "
                        "SSH reconnect will start a fresh session"
                    ),
                    "fix": (
                        f"Add -c to the claude invocation in platform.yaml for repo '{name}', "
                        "e.g.: claude -c --plugin-dir ... (see M-3 in finish-maestro-to-otaman-migration: legacy)"
                    ),
                    "severity": "low",
                })
                break

    if issues:
        result["status"] = "warn"
        result["issues"] = issues
    return result


def run_doctor(project_root: Path) -> dict[str, Any]:
    """Run all doctor checks and return comprehensive report."""
    config_path = project_root / "platform.yaml"
    repos: list[dict[str, Any]] = []
    config: dict[str, Any] = {}

    if config_path.exists() and yaml:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        repos = config.get("repos", [])

    checks = [
        check_git_identity(),
        check_git_platform(repos, project_root),
        check_runtimes(repos, project_root),
        check_claude_cli(),
        check_openspec(config, project_root),
        check_ssh_keys(repos, project_root),
        check_mcp_deps(),
        check_tmux(),
        check_maestro_plugin(project_root),
        check_secrets_leaks(project_root),
        check_git_host(project_root),
        check_launch_commands_resume(repos),
        check_plugin_doctor(project_root),
    ]

    passed = sum(1 for c in checks if c["status"] == "ok")
    warned = sum(1 for c in checks if c["status"] == "warn")
    failed = sum(1 for c in checks if c["status"] == "fail")

    all_issues = []
    for c in checks:
        for issue in c.get("issues", []):
            issue["check"] = c["check"]
            all_issues.append(issue)

    # Sort by severity
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_issues.sort(key=lambda x: severity_order.get(x.get("severity", "low"), 3))

    return {
        "project_root": str(project_root),
        "summary": {"passed": passed, "warned": warned, "failed": failed, "total": len(checks)},
        "checks": checks,
        "issues": all_issues,
    }


def main() -> int:
    if len(sys.argv) > 1:
        project_root = Path(sys.argv[1]).resolve()
    else:
        from otaman_core._resolve import find_maestro_root
        project_root = find_maestro_root()
        if not project_root:
            print(json.dumps({"error": "No otaman project found"}))
            return 2

    report = run_doctor(project_root)
    print(json.dumps(report, indent=2))

    return 1 if report["summary"]["failed"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
