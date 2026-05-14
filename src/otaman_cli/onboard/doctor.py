"""onboard doctor — read-only diagnostic for users.yaml + state layout."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from otaman_cli.onboard.audit import OnboardAudit
from otaman_cli.onboard.state import (
    StateError,
    default_state_dir,
    load_users,
    users_path,
    validate_email,
    validate_roles,
)


@dataclass
class CheckResult:
    name: str
    status: str          # "OK" | "WARN" | "FAIL"
    detail: str = ""


def _check_state_dir_exists(state_dir: Path) -> CheckResult:
    if not state_dir.is_dir():
        return CheckResult(
            name="state directory",
            status="WARN",
            detail=f"{state_dir} does not exist yet — first `add-user` will create it",
        )
    return CheckResult(name="state directory", status="OK", detail=str(state_dir))


def _check_users_yaml_parseable(state_dir: Path) -> CheckResult:
    path = users_path(state_dir)
    if not path.is_file():
        return CheckResult(
            name="users.yaml",
            status="WARN",
            detail=f"{path} does not exist (no users registered yet)",
        )
    try:
        users = load_users(state_dir)
    except StateError as exc:
        return CheckResult(
            name="users.yaml parse",
            status="FAIL",
            detail=str(exc),
        )
    return CheckResult(
        name="users.yaml parse",
        status="OK",
        detail=f"{len(users)} user(s) registered",
    )


def _check_each_user_valid(state_dir: Path) -> list[CheckResult]:
    out: list[CheckResult] = []
    try:
        users = load_users(state_dir)
    except StateError:
        return out  # already caught by upstream check
    for u in users:
        try:
            validate_email(u.email)
        except StateError as exc:
            out.append(CheckResult(
                name=f"user {u.email!r} email",
                status="FAIL",
                detail=str(exc),
            ))
            continue
        try:
            validate_roles(u.roles)
        except StateError as exc:
            out.append(CheckResult(
                name=f"user {u.email!r} roles",
                status="FAIL",
                detail=str(exc),
            ))
            continue
        out.append(CheckResult(
            name=f"user {u.email!r}",
            status="OK",
            detail=f"roles={','.join(sorted(u.roles))}",
        ))
    return out


def _check_audit_writable(state_dir: Path) -> CheckResult:
    audit_dir = state_dir / "audit"
    if not audit_dir.exists():
        return CheckResult(
            name="audit directory",
            status="WARN",
            detail=f"{audit_dir} does not exist — will be created on first event",
        )
    # Try a probe write
    probe = audit_dir / ".doctor-probe"
    try:
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return CheckResult(
            name="audit directory writable",
            status="FAIL",
            detail=f"{audit_dir}: {exc}",
        )
    return CheckResult(name="audit directory writable", status="OK", detail=str(audit_dir))


def _check_duplicate_emails(state_dir: Path) -> CheckResult:
    try:
        users = load_users(state_dir)
    except StateError:
        return CheckResult(name="duplicate emails", status="WARN", detail="(skipped — users.yaml unreadable)")
    seen = set()
    dupes = []
    for u in users:
        if u.email in seen:
            dupes.append(u.email)
        seen.add(u.email)
    if dupes:
        return CheckResult(
            name="duplicate emails",
            status="FAIL",
            detail=f"duplicate emails in users.yaml: {dupes}",
        )
    return CheckResult(name="duplicate emails", status="OK", detail=f"{len(seen)} unique")


def run_doctor(state_dir: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    results.append(_check_state_dir_exists(state_dir))
    results.append(_check_users_yaml_parseable(state_dir))
    results.extend(_check_each_user_valid(state_dir))
    results.append(_check_duplicate_emails(state_dir))
    results.append(_check_audit_writable(state_dir))
    return results


def cmd_doctor(args: argparse.Namespace) -> int:
    state_dir = Path(args.state_dir) if args.state_dir else default_state_dir()
    results = run_doctor(state_dir)

    fail_count = sum(1 for r in results if r.status == "FAIL")
    warn_count = sum(1 for r in results if r.status == "WARN")

    # Print human-readable table
    name_width = max(len(r.name) for r in results)
    for r in results:
        marker = {"OK": "  OK  ", "WARN": " WARN ", "FAIL": " FAIL "}.get(r.status, "      ")
        print(f"[{marker}] {r.name:<{name_width}}  {r.detail}")
    print()
    print(f"summary: {fail_count} fail, {warn_count} warn, {len(results) - fail_count - warn_count} ok")

    # Audit the run
    audit = OnboardAudit(state_dir / "audit")
    operator = os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown"
    audit.doctor_run(actor=operator, fail_count=fail_count, warn_count=warn_count)

    return 1 if fail_count > 0 else 0
