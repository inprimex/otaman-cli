"""`otaman doctor` — migrated from main.py.

--org was doctor-exclusive, so its flag-loop branch and variable are
removed entirely from main() (F021/F022), same as blocked/check/complete.
cmd_doctor now parses --org (and the optional root-path positional)
itself.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root
from otaman_cli.main import UI, C, run_script

try:
    import pwd as _pwd
except ImportError:
    # Windows has no pwd module -- `otaman doctor --org` resolves another
    # local system user's home directory, a POSIX-only concept. Handled as
    # a normal precondition-error result in _check_org_harnesses below,
    # not a crash.
    _pwd = None  # type: ignore[assignment]


def _parse_version_tuple(text: str) -> tuple[int, ...] | None:
    """Parse a version string into a tuple of ints; return None on failure.

    Strips a leading ``v`` and takes only the first whitespace-delimited token
    (handles outputs like ``v2.3.1 (Anthropic)``).  Stops at the first
    non-numeric segment so suffixes like ``-beta`` don't crash the comparison.
    """
    if not text:
        return None
    token = text.strip().split()[0] if text.strip() else ""
    if token.startswith("v") or token.startswith("V"):
        token = token[1:]
    parts: list[int] = []
    for seg in token.split("."):
        digits = ""
        had_non_digit = False
        for ch in seg:
            if ch.isdigit():
                digits += ch
            else:
                had_non_digit = True
                break
        if not digits:
            break
        parts.append(int(digits))
        if had_non_digit:
            # Stop at the first prerelease/build segment (e.g. "0-beta")
            break
    return tuple(parts) if parts else None


def _check_org_harnesses(root: Path, org_name: str) -> tuple[int, list[dict]]:
    """ce-bootstrap-harness-deps task 3.1 — verify harness binaries on an org user's PATH.

    Returns (rc, results) where rc is 0 if all harnesses pass, 1 if any fail or
    a precondition is unmet (org not declared, no system_user, no runner.harnesses).
    `results` is a list of dicts with keys: harness_id, binary, status (ok/missing/
    too_old), version, path, error.
    """
    import yaml as _yaml

    platform_yaml = root / "platform.yaml"
    if not platform_yaml.is_file():
        return 1, [{"status": "error", "error": f"platform.yaml not found at {platform_yaml}"}]

    try:
        config = _yaml.safe_load(platform_yaml.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return 1, [{"status": "error", "error": f"failed to parse platform.yaml: {exc}"}]

    orgs = config.get("orgs") or {}
    if not isinstance(orgs, dict) or org_name not in orgs:
        return 1, [
            {
                "status": "error",
                "error": f"org '{org_name}' not declared in platform.yaml `orgs:` block",
            }
        ]
    org_entry = orgs[org_name]
    if not isinstance(org_entry, dict):
        return 1, [{"status": "error", "error": f"orgs.{org_name} must be a mapping"}]
    system_user = org_entry.get("system_user")
    if not system_user or not isinstance(system_user, str):
        return 1, [
            {
                "status": "error",
                "error": f"orgs.{org_name}.system_user is required (a Unix user name)",
            }
        ]

    # Resolve the org user's home directory via pwd (more precise than expanduser,
    # which returns the literal ~name when the user is missing).
    if _pwd is None:
        return 1, [
            {
                "status": "error",
                "error": (
                    "otaman doctor --org requires a POSIX system "
                    "(the pwd module, used to resolve another user's home "
                    "directory, is unavailable on this platform)"
                ),
            }
        ]
    try:
        org_home = Path(_pwd.getpwnam(system_user).pw_dir)
    except KeyError:
        return 1, [
            {
                "status": "error",
                "error": f"system user '{system_user}' does not exist on this host",
            }
        ]

    runner = config.get("runner") or {}
    harnesses = runner.get("harnesses") if isinstance(runner, dict) else None
    if not isinstance(harnesses, list) or not harnesses:
        return 1, [
            {
                "status": "error",
                "error": "no runner.harnesses declared in platform.yaml",
            }
        ]

    results: list[dict] = []
    all_ok = True
    for h in harnesses:
        if not isinstance(h, dict):
            continue
        hid = h.get("id") or ""
        binary = h.get("binary") or ""
        min_version = h.get("min_version")
        if not hid or not binary:
            continue

        bin_path = org_home / ".local" / "bin" / binary
        entry = {
            "harness_id": hid,
            "binary": binary,
            "path": str(bin_path),
            "min_version": min_version,
        }

        if not bin_path.exists() or not os.access(bin_path, os.X_OK):
            entry["status"] = "missing"
            all_ok = False
            results.append(entry)
            continue

        if min_version:
            try:
                proc = subprocess.run(
                    [str(bin_path), "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                ver_text = (proc.stdout or proc.stderr or "").strip()
            except Exception as exc:
                entry["status"] = "version_check_failed"
                entry["error"] = str(exc)
                all_ok = False
                results.append(entry)
                continue

            actual = _parse_version_tuple(ver_text)
            required = _parse_version_tuple(str(min_version))
            entry["version"] = ver_text.splitlines()[0] if ver_text else ""
            if actual is None or required is None:
                entry["status"] = "version_unparseable"
                all_ok = False
            elif actual < required:
                entry["status"] = "too_old"
                all_ok = False
            else:
                entry["status"] = "ok"
        else:
            # No min_version pinned — presence is sufficient
            entry["status"] = "ok"

        results.append(entry)

    return (0 if all_ok else 1), results


def _print_org_harness_report(org_name: str, results: list[dict]) -> None:
    """Pretty-print the harness check results for `otaman doctor --org <name>`."""
    print()
    UI.header(f"Org Harness Check: {org_name}")
    for r in results:
        if r.get("status") == "error":
            UI.error(r.get("error", "unknown error"))
            continue
        hid = r.get("harness_id", "")
        binary = r.get("binary", "")
        status = r.get("status", "")
        version = r.get("version", "")
        if status == "ok":
            tail = f" {version}" if version else ""
            print(f"  {UI.badge('OK', C.GREEN)}  {hid}  {binary}{tail}")
        elif status == "missing":
            print(f"  {UI.badge('FAIL', C.RED)}  {hid}  {binary}  NOT FOUND")
            print(
                f"        run: sudo bash ce-bootstrap.sh --org={org_name} --install-harness={hid}"
            )
        elif status == "too_old":
            print(
                f"  {UI.badge('FAIL', C.RED)}  {hid}  {binary}  {version} "
                f"(min: {r.get('min_version')})"
            )
            print(
                f"        run: sudo bash ce-bootstrap.sh --org={org_name} --upgrade-harness={hid}"
            )
        else:
            print(f"  {UI.badge('FAIL', C.RED)}  {hid}  {binary}  {status}: {r.get('error', '')}")


def cmd_doctor(args: list[str]) -> int:
    """Check environment readiness — git, runtimes, CLI tools, MCP.

    When ``--org <name>`` is given, additionally verify that each binary
    declared in ``platform.yaml`` ``runner.harnesses`` is installed and
    executable for that org's system user (ce-bootstrap-harness-deps task 3.1).
    The harness check is additive — all existing checks still run.
    """
    org: str | None = None
    positional: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--org" and i + 1 < len(args):
            org = args[i + 1]
            i += 2
        else:
            positional.append(args[i])
            i += 1

    root = Path(positional[0]).resolve() if positional else find_project_root()
    if not root:
        UI.error("Not in an otaman project")
        return 1

    UI.header("Environment Check")

    result = run_script("doctor.py", str(root), capture=True)
    if result.returncode == 2:
        UI.error(result.stderr or result.stdout)
        return 2

    import json

    try:
        report = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        UI.error("Failed to parse doctor report")
        print(result.stdout)
        return 2

    if "error" in report:
        UI.error(report["error"])
        return 1

    # Display results
    summary = report.get("summary", {})
    checks = report.get("checks", [])

    status_icon = {
        "ok": UI.badge("OK", C.GREEN),
        "warn": UI.badge("WARN", C.YELLOW),
        "fail": UI.badge("FAIL", C.RED),
    }

    check_labels = {
        "git_identity": "Git Identity",
        "git_platform": "Git Platform & CLI",
        "runtimes": "Runtimes & SDKs",
        "claude_cli": "Claude CLI",
        "openspec": "OpenSpec CLI",
        "ssh_keys": "SSH Keys",
        "mcp_dependencies": "MCP Dependencies",
        "tmux": "tmux (connection resilience)",
        "maestro_plugin": "Otaman Setup",
        "secrets_leaks": "Secrets Hygiene",
        "git_host": "Git Host PAT",
    }

    for check in checks:
        name = check_labels.get(check["check"], check["check"])
        icon = status_icon.get(check["status"], "?")
        details = check.get("details", {})

        # Build detail string
        detail_parts = []
        if check["check"] == "git_identity":
            if details.get("user_name"):
                detail_parts.append(f"{details['user_name']} <{details.get('user_email', '?')}>")
        elif check["check"] == "git_platform":
            if details.get("provider"):
                detail_parts.append(details["provider"])
                if details.get("cli_installed"):
                    detail_parts.append(f"{details['cli']} CLI")
                if details.get("authenticated"):
                    detail_parts.append("authenticated")
                if details.get("pr_enabled"):
                    detail_parts.append("PR ready")
        elif check["check"] == "runtimes":
            for rt, info in details.items():
                if isinstance(info, dict) and info.get("version"):
                    detail_parts.append(f"{rt} {info['version']}")
        elif check["check"] == "claude_cli":
            if details.get("version"):
                detail_parts.append(details["version"])
        elif check["check"] == "openspec":
            if details.get("skipped"):
                detail_parts.append("not required")
            elif details.get("version"):
                detail_parts.append(f"v{details['version']}")
                if details.get("via_npx"):
                    detail_parts.append("via npx")
        elif check["check"] == "ssh_keys":
            if details.get("ssh_repos"):
                detail_parts.append(f"{details['ssh_repos']} SSH repos")
            if details.get("https_repos"):
                detail_parts.append(f"{details['https_repos']} HTTPS repos")
        elif check["check"] == "tmux":
            if details.get("version"):
                detail_parts.append(details["version"])

        detail_str = f" ({', '.join(detail_parts)})" if detail_parts else ""
        print(f"  {icon} {name}{C.DIM}{detail_str}{C.RESET}")

    # Issues
    issues = report.get("issues", [])
    if issues:
        UI.subheader(f"Issues ({len(issues)}):")
        for issue in issues:
            severity = issue.get("severity", "medium")
            if severity == "critical":
                UI.blocked(issue["issue"])
            elif severity == "high":
                UI.error(issue["issue"])
            else:
                UI.warn(issue["issue"])
            UI.muted(f"Fix: {issue['fix']}")

    # pm-sync health check
    print()
    UI.subheader("[pm-sync]")
    try:
        import yaml as _yaml

        _pm_platform_yaml = root / "platform.yaml"
        if _pm_platform_yaml.is_file():
            _pm_config = _yaml.safe_load(_pm_platform_yaml.read_text(encoding="utf-8")) or {}
        else:
            _pm_config = {}
        _pm_sync = _pm_config.get("pm-sync")
        if not _pm_sync:
            UI.warn(
                "[pm-sync] PM sync not configured — "
                "run `otaman pm configure <provider> --url <url>`"
            )
        else:
            # Check required fields
            _pm_provider = _pm_sync.get("provider") or ""
            _pm_base_url = _pm_sync.get("base-url") or ""
            _pm_project_map = _pm_sync.get("project-map")
            _pm_webhook_target = (_pm_sync.get("webhook-target") or "").strip()

            if not _pm_provider:
                UI.warn("[pm-sync] provider not set in pm-sync block")
            else:
                UI.ok(f"[pm-sync] provider: {_pm_provider}")

            if not _pm_base_url:
                UI.warn("[pm-sync] base-url not set in pm-sync block")
            else:
                UI.ok(f"[pm-sync] base-url: {_pm_base_url}")

            if _pm_project_map is None:
                UI.warn("[pm-sync] project-map not set in pm-sync block")
            elif not _pm_project_map:
                UI.warn("[pm-sync] pm init not run — run `otaman pm init <provider>`")
            else:
                UI.ok(f"[pm-sync] project-map: {len(_pm_project_map)} mapping(s)")

            # Webhook reachability
            if _pm_webhook_target:
                try:
                    import urllib.request as _urllib_req

                    _pm_req = _urllib_req.Request(_pm_webhook_target, method="HEAD")
                    _pm_resp = _urllib_req.urlopen(_pm_req, timeout=3)
                    UI.ok(
                        f"[pm-sync] webhook-target reachable ({_pm_resp.status}): "
                        f"{_pm_webhook_target}"
                    )
                except Exception as _pm_exc:
                    UI.warn(
                        f"[pm-sync] webhook-target unreachable ({_pm_webhook_target}): {_pm_exc}"
                    )
            else:
                UI.warn(
                    "[pm-sync] webhooks not configured — run with `--no-webhooks` flag or "
                    "set `pm-sync.webhook-target`"
                )
    except Exception as _pm_outer_exc:
        UI.warn(f"[pm-sync] check failed: {_pm_outer_exc}")

    # Summary line
    print()
    p, w, f_ = summary.get("passed", 0), summary.get("warned", 0), summary.get("failed", 0)
    total = summary.get("total", 0)
    if f_ == 0 and w == 0:
        UI.ok(f"All {total} checks passed — environment ready")
    elif f_ == 0:
        UI.warn(f"{p} passed, {w} warnings — mostly ready")
    else:
        UI.error(f"{p} passed, {w} warnings, {f_} failed — fix issues above")

    base_rc = 1 if report["summary"]["failed"] > 0 else 0

    # ce-bootstrap-harness-deps task 3.1 — additive `--org` harness check
    if org:
        org_rc, results = _check_org_harnesses(root, org)
        _print_org_harness_report(org, results)
        return 1 if (base_rc or org_rc) else 0

    return base_rc


register(
    CommandSpec(
        name="doctor",
        handler=cmd_doctor,
        help="Check environment readiness (git, runtimes, CLI, tmux, MCP)",
    )
)
