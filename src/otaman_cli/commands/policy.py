"""`otaman policy …` — the operator surface for the policy engine (policy-engine 2.1 + 4.2).

The engine itself is ``otaman_core.policy`` (loader, tightest-wins composition,
effective-policy API, owner resolution); this command reimplements none of that
algebra — it is the read / generate / guard surface on top of it.

Implemented verbs:

- ``otaman policy list`` — registered packs + the policy selected for each, and
  whether it resolves on disk or falls back to the Otaman-shipped standard.
- ``otaman policy show [<pack>] [--repo R] [--agent A] [--json]`` — the composed,
  tightest-wins effective policy for a pack (default ``git``), plus any refused
  loosening attempts.
- ``otaman policy diff`` — per repo, the branch protection the effective git
  policy wants vs what is live on the host (read-only; D4a failure-mode
  classification). Exit non-zero when any repo drifts.
- ``otaman policy validate`` — every selected policy parses and composes with no
  refused loosening; non-zero exit on any structural error or loosening.

The remaining verbs land in follow-up PRs (kept out of the dispatcher until then
so the surface never advertises a verb it can't honor): ``apply`` (generate-and-
diff + ``cto`` role gate + HUMAN-DECISION when tightening a human-owned branch;
emits the plan for deploy to push live — cli never PUTs protection itself),
``check-merge`` (the agent-into-human-owned-branch merge guard), and the 4.2
observability surface.
"""

from __future__ import annotations

from otaman_cli.commands import CommandSpec, register
from otaman_cli.identity import find_project_root
from otaman_cli.main import UI, C

DEFAULT_PACK = "git"


def _bail(msg: str, code: int = 1) -> int:
    UI.error(msg)
    return code


def _load_context() -> tuple[object | None, dict]:
    """``(meta_root, platform_config)`` for the current program, or ``(None, {})``.

    ``meta_root`` is where ``platform.yaml`` and ``policy/`` live — the same root
    ``otaman_core.policy`` reads from.
    """
    import yaml

    root = find_project_root()
    if root is None:
        UI.error("Not in an otaman project (no platform.yaml in cwd or ancestors)")
        return None, {}
    pf = root / "platform.yaml"
    try:
        config = yaml.safe_load(pf.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        UI.error(f"failed to read platform.yaml: {exc}")
        return None, {}
    if not isinstance(config, dict):
        UI.error("platform.yaml is not a mapping")
        return None, {}
    return root, config


def _registered_packs(root) -> dict:
    """Registered packs from ``policy/index.yaml``, falling back to the shipped
    index so a program that ships no index still shows the git pack."""
    from otaman_core.policy import load_policy_index, shipped_index

    index = load_policy_index(root)
    return index.packs or shipped_index().packs


def _cmd_list() -> int:
    root, config = _load_context()
    if root is None:
        return 1
    from otaman_core.policy import (
        DEFAULT_POLICY_NAME,
        PolicyError,
        load_policy,
        select_policy_names,
    )

    try:
        packs = _registered_packs(root)
    except PolicyError as exc:
        return _bail(f"policy index invalid: {exc}", code=2)

    UI.header("Policy — registered packs")
    if not packs:
        UI.muted("No packs registered (no policy/index.yaml and no shipped pack).")
        return 0

    for pack in sorted(packs):
        UI.kv("pack", pack, C.BOLD)
        try:
            selection = select_policy_names(config, pack, repo=None, agent=None)
        except PolicyError as exc:
            UI.error(f"  selection error: {exc}")
            continue
        for layer_name, name in selection:
            on_disk = load_policy(root, pack, name) is not None
            src = (
                "on disk"
                if on_disk
                else ("shipped standard" if name == DEFAULT_POLICY_NAME else "MISSING")
            )
            UI.kv(f"  {layer_name}", f"{name} ({src})")
    return 0


def _cmd_show(pack: str, repo: str | None, agent: str | None, as_json: bool) -> int:
    root, config = _load_context()
    if root is None:
        return 1
    from otaman_core.policy import PolicyError, effective_policy

    try:
        eff, violations = effective_policy(root, config, pack, repo=repo, agent=agent)
    except PolicyError as exc:
        return _bail(f"cannot resolve effective {pack} policy: {exc}", code=2)

    if as_json:
        import json

        print(
            json.dumps(
                {
                    "pack": eff.pack,
                    "repo": repo,
                    "agent": agent,
                    "rules": eff.rules,
                    "loosening_refused": [
                        {"rule": v.rule, "layer": v.layer, "attempted": v.attempted, "kept": v.kept}
                        for v in violations
                    ],
                }
            )
        )
        return 0

    scope = f"{pack}" + (f" @ repo={repo}" if repo else "") + (f" agent={agent}" if agent else "")
    UI.header(f"Effective policy — {scope}")
    if not eff.rules:
        UI.muted("(no rules)")
    for key in sorted(eff.rules):
        UI.kv(key, str(eff.rules[key]))
    if violations:
        UI.warn(f"{len(violations)} loosening attempt(s) REFUSED (tightest-wins kept):")
        for v in violations:
            UI.muted(f"  {v.rule}: layer {v.layer!r} tried {v.attempted!r}; kept {v.kept!r}")
    return 0


# ---------------------------------------------------------------------------
# git-pack branch-protection generate-and-diff (2.1: diff)
#
# `diff` computes the branch protection the effective git policy WANTS and
# compares it to what is live on the host, per repo — read-only. `apply` (a
# later PR) reuses this, adds the cto gate + HUMAN-DECISION, and emits the plan
# for deploy (step 3) to push live; cli never PUTs protection itself.
#
# The three gh-calling helpers below are the seam tests monkeypatch.


def _repos(config: dict) -> list[dict]:
    return [r for r in (config.get("repos") or []) if isinstance(r, dict)]


def _human_names(root) -> set[str]:
    """Roster human names — a repo/branch owner in this set is human-owned."""
    from otaman_core.human_roster import load_human_roster

    try:
        return {e.name for e in load_human_roster(root / "platform.yaml")}
    except Exception:  # noqa: BLE001 - absent/invalid roster → no humans classified
        return set()


def _gh_json(args: list[str]):
    import json
    import subprocess

    try:
        r = subprocess.run(["gh", "api", *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def _default_branch(slug: str) -> str | None:
    return _gh_json([f"repos/{slug}", "--jq", ".default_branch"])


def _read_live_protection(slug: str, branch: str) -> dict | None:
    """Live branch protection, or None when the branch is unprotected/unreadable."""
    data = _gh_json([f"repos/{slug}/branches/{branch}/protection"])
    return data if isinstance(data, dict) else None


def _live_check_contexts(slug: str, branch: str) -> list[str]:
    """The repo's live CI check names (D4a: required-check context is per-repo,
    read from live CI, never a constant like a hardcoded ``ci-ok``)."""
    names = _gh_json(
        [f"repos/{slug}/commits/{branch}/check-runs", "--jq", "[.check_runs[].name] | unique"]
    )
    return [n for n in names if isinstance(n, str)] if isinstance(names, list) else []


def _desired_protection(rules: dict, *, is_human_owned: bool, contexts: list[str]) -> dict:
    """The branch protection the effective git policy wants for this branch."""
    desired: dict = {}
    if rules.get("require_status_checks"):
        desired["required_status_checks"] = {"strict": False, "contexts": sorted(contexts)}
    if rules.get("force_push_forbidden"):
        desired["allow_force_pushes"] = False
    if is_human_owned and rules.get("owner_admission_required"):
        # human-owned branch: the owner is a required reviewer (>=1 approval)
        desired["required_pull_request_reviews"] = {"required_approving_review_count": 1}
    return desired


def _diff_protection(desired: dict, live: dict | None) -> tuple[list[str], str]:
    """(changes, failure_mode) for one branch. D4a point 2: distinguish creating
    protection from nothing vs raising approvals 0->1."""
    if not live:
        if not desired:
            return [], "conformant"
        return [f"add {k}" for k in sorted(desired)], "create-from-nothing"

    changes: list[str] = []
    if "required_pull_request_reviews" in desired:
        want = desired["required_pull_request_reviews"]["required_approving_review_count"]
        have = (live.get("required_pull_request_reviews") or {}).get(
            "required_approving_review_count", 0
        )
        if have < want:
            changes.append(f"required approving reviews {have} -> {want}")
    if desired.get("allow_force_pushes") is False:
        afp = live.get("allow_force_pushes")
        have_fp = afp.get("enabled", False) if isinstance(afp, dict) else bool(afp)
        if have_fp:
            changes.append("force pushes allowed -> forbidden")
    if "required_status_checks" in desired:
        want_ctx = set(desired["required_status_checks"]["contexts"])
        have_ctx = set((live.get("required_status_checks") or {}).get("contexts") or [])
        missing = want_ctx - have_ctx
        if missing:
            changes.append(f"required checks missing: {', '.join(sorted(missing))}")

    if not changes:
        return [], "conformant"
    if len(changes) == 1 and changes[0].startswith("required approving reviews"):
        return changes, "raise-approvals-0to1"
    return changes, "update"


def _diff_repos(root, config: dict):
    """Yield (repo_name, branch, owner_kind, changes, mode) per resolvable repo.

    Emits a synthetic ('name', None, 'skip', [reason], 'skip') row for a repo
    with no resolvable GitHub remote so the caller can report it, never silently
    drop it (no-silent-caps).
    """
    from otaman_core.git_host import detect_remote_for_repo
    from otaman_core.policy import PolicyError, effective_policy

    humans = _human_names(root)
    for r in _repos(config):
        name = r.get("name") or "?"
        owner = r.get("owner") or ""
        path = r.get("path")
        repo_dir = (root / path).resolve() if path else None
        info = detect_remote_for_repo(repo_dir) if repo_dir and repo_dir.exists() else None
        if info is None or info.provider != "github":
            yield (name, None, "skip", ["no GitHub remote resolved"], "skip")
            continue
        slug = info.slug
        branch = _default_branch(slug) or "main"
        is_human = owner in humans
        try:
            eff, _v = effective_policy(root, config, "git", repo=name)
        except PolicyError as exc:
            yield (name, branch, "error", [f"policy error: {exc}"], "error")
            continue
        contexts = _live_check_contexts(slug, branch)
        desired = _desired_protection(eff.rules, is_human_owned=is_human, contexts=contexts)
        live = _read_live_protection(slug, branch)
        changes, mode = _diff_protection(desired, live)
        yield (name, branch, "human" if is_human else "agent", changes, mode)


def _cmd_diff() -> int:
    root, config = _load_context()
    if root is None:
        return 1
    if not _repos(config):
        UI.muted("No repos in platform.yaml.")
        return 0

    UI.header("Policy — branch-protection diff (desired vs live)")
    drift = False
    for name, branch, kind, changes, mode in _diff_repos(root, config):
        if kind == "skip":
            UI.warn(f"{name}: skipped — {changes[0]}")
            continue
        if kind == "error":
            UI.error(f"{name} ({branch}): {changes[0]}")
            drift = True
            continue
        if mode == "conformant":
            UI.ok(f"{name} ({branch}, {kind}-owned): conformant")
            continue
        drift = True
        UI.warn(f"{name} ({branch}, {kind}-owned): {mode}")
        for c in changes:
            UI.muted(f"    {c}")
    return 1 if drift else 0


def _cmd_validate() -> int:
    root, config = _load_context()
    if root is None:
        return 1
    from otaman_core.policy import PolicyError, effective_policy

    try:
        packs = _registered_packs(root)
    except PolicyError as exc:
        return _bail(f"policy/index.yaml invalid: {exc}", code=2)

    UI.header("Policy — validate")
    errors = 0
    loosenings = 0
    for pack in sorted(packs):
        try:
            _eff, violations = effective_policy(root, config, pack, repo=None, agent=None)
        except PolicyError as exc:
            UI.error(f"{pack}: {exc}")
            errors += 1
            continue
        if violations:
            loosenings += len(violations)
            for v in violations:
                UI.warn(f"{pack}: {v.rule} — layer {v.layer!r} tried to loosen {v.attempted!r}")
        else:
            UI.ok(f"{pack}: OK")

    if errors:
        return _bail(f"{errors} pack(s) failed to resolve", code=2)
    if loosenings:
        return _bail(
            f"{loosenings} refused loosening attempt(s) — tighten the offending layer", code=1
        )
    UI.ok("All registered packs resolve cleanly.")
    return 0


def cmd_policy(args: list[str]) -> int:
    """`otaman policy <list|show|validate> …`."""
    if not args or args[0] in ("-h", "--help"):
        UI.muted("Usage: otaman policy list")
        UI.muted("       otaman policy show [<pack>] [--repo R] [--agent A] [--json]")
        UI.muted("       otaman policy diff")
        UI.muted("       otaman policy validate")
        return 0 if args else 1

    action, rest = args[0], args[1:]
    if action == "list":
        return _cmd_list()
    if action == "diff":
        return _cmd_diff()
    if action == "validate":
        return _cmd_validate()
    if action == "show":
        pack = DEFAULT_PACK
        repo = agent = None
        as_json = False
        i = 0
        while i < len(rest):
            a = rest[i]
            if a == "--repo" and i + 1 < len(rest):
                repo = rest[i + 1]
                i += 2
            elif a == "--agent" and i + 1 < len(rest):
                agent = rest[i + 1]
                i += 2
            elif a == "--json":
                as_json = True
                i += 1
            elif not a.startswith("-"):
                pack = a
                i += 1
            else:
                return _bail(f"Unexpected argument: {a}")
        return _cmd_show(pack, repo, agent, as_json)

    return _bail(f"Unknown action {action!r}. Actions: list, show, diff, validate")


register(
    CommandSpec(
        name="policy",
        handler=cmd_policy,
        help="Policy engine: list packs | show effective policy | validate",
    )
)

__all__ = ["cmd_policy"]
