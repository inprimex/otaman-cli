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
- ``otaman policy apply [--dry-run] [--apply-live]`` — generate-and-diff the
  branch protection from the effective git policy; ``cto`` role gate (D5);
  HUMAN-DECISION when it would tighten a human-owned branch; writes the plan to
  ``policy/generated/branch-protection.json``. Plan-only by default. With
  ``--apply-live`` (or ``policies.live_apply: auto``) it shells to deploy's
  non-interactive entrypoint ``otaman-policy-apply-live --root <root>`` AFTER the
  confirm to converge live protection (D12) — cli never loads the deploy
  credential and never PUTs protection itself (STOP-AT); an absent entrypoint is
  reported and apply stops plan-only (no silent manual fallback). ``--dry-run``
  previews the plan without the gates, the write, or the live handoff.
- ``otaman policy check-merge <base-branch> [--repo NAME]`` — the merge guard:
  exit non-zero to refuse an agent session merging into a human-owned (or
  owner-less) branch. Owner intent comes from ``resolve_branch_owner`` +
  ``policy/git/branch-owners.yaml``, never from live protection state (D4a).
- ``otaman policy annotate <base-branch> [--repo NAME]`` — 4.2 PR annotation:
  print an admissibility annotation for a PR's base branch (owner-less /
  human-owned / agent-owned). Read-only, always exit 0 — for display on a PR;
  the gate is ``check-merge``.
- ``otaman policy validate`` — every selected policy parses and composes with no
  refused loosening; non-zero exit on any structural error or loosening.

The other half of 4.2 — the ``otaman doctor`` branch-policy section (ownership /
delegation / drift per repo) — lives in ``commands/doctor.py``.
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
    return _gh_json([f"repos/{slug}", "--jq", ".default_branch"]) if slug else None


def _repo_default_branch(repo_dir, slug: str) -> str:
    """The repo's default branch — LOCAL git first (no network/auth), then gh,
    then 'main'. The gh-only path was fragile (any auth/network hiccup made every
    default branch resolve owner-less in check-merge — the 5.1 gate blocker)."""
    import subprocess

    if repo_dir is not None:
        try:
            r = subprocess.run(
                ["git", "-C", str(repo_dir), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().rsplit("/", 1)[-1]  # origin/main -> main
        except (OSError, subprocess.SubprocessError):
            pass
    return _default_branch(slug) or "main"


def _read_live_protection(slug: str, branch: str) -> dict | None:
    """Live branch protection, or None when the branch is unprotected/unreadable."""
    data = _gh_json([f"repos/{slug}/branches/{branch}/protection"])
    return data if isinstance(data, dict) else None


#: The canonical aggregator job: when a repo has it, it is the ONE context that
#: actually gates merge — requiring the individual jobs alongside it is redundant
#: (and harmful when some are `continue-on-error`, whose conclusions report
#: failure though ci-ok ignores them — deploy live incident 2026-09-03).
_AGGREGATOR_CHECK = "ci-ok"


def _live_check_contexts(slug: str, branch: str) -> list[str]:
    """The repo's live required-check contexts (D4a: per-repo, from live CI).

    If the repo has the ``ci-ok`` aggregator, require ONLY it — that is what
    gates merge for the repo (an opt-in aggregator pattern); requiring the
    individual jobs too is redundant and breaks on `continue-on-error` jobs.
    Otherwise require every discovered check name.
    """
    names = _gh_json(
        [f"repos/{slug}/commits/{branch}/check-runs", "--jq", "[.check_runs[].name] | unique"]
    )
    live = [n for n in names if isinstance(n, str)] if isinstance(names, list) else []
    if _AGGREGATOR_CHECK in live:
        return [_AGGREGATOR_CHECK]
    return live


def _gh_available() -> bool:
    """True if `gh` is installed AND authenticated — so a caller (e.g. the doctor
    branch-policy section) can skip live reads instead of reporting false drift
    from an unauthenticated environment."""
    import shutil
    import subprocess

    if not shutil.which("gh"):
        return False
    try:
        return (
            subprocess.run(["gh", "auth", "status"], capture_output=True, timeout=15).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


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


def _plan_repos(root, config: dict):
    """Yield one plan-record dict per repo in platform.yaml — the shared basis of
    `diff` and `apply`.

    keys: ``repo, slug, branch, kind, desired, changes, mode``. ``kind`` is
    ``human``/``agent`` for a resolved repo, or ``skip``/``error``. A repo with
    no resolvable GitHub remote yields ``kind='skip'`` — reported, never dropped
    (no-silent-caps).
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
            yield {
                "repo": name,
                "slug": None,
                "branch": None,
                "kind": "skip",
                "desired": None,
                "changes": ["no GitHub remote resolved"],
                "mode": "skip",
            }
            continue
        slug = info.slug
        branch = _repo_default_branch(repo_dir, slug)
        is_human = owner in humans
        try:
            eff, _v = effective_policy(root, config, "git", repo=name)
        except PolicyError as exc:
            yield {
                "repo": name,
                "slug": slug,
                "branch": branch,
                "kind": "error",
                "desired": None,
                "changes": [f"policy error: {exc}"],
                "mode": "error",
            }
            continue
        # D4a: contexts come from the repo's live CI. If enumeration comes back
        # empty (no check-runs on the tip, or gh unreadable), fall back to the
        # fleet's `ci-ok` aggregate rather than emit zero required checks —
        # empty contexts would apply live as NO required checks and silently
        # weaken the gate (spec-agent 5.1 finding). Precise single-vs-multi
        # workflow context selection (D9) is plugin's generator contract.
        contexts = _live_check_contexts(slug, branch) or ["ci-ok"]
        desired = _desired_protection(eff.rules, is_human_owned=is_human, contexts=contexts)
        live = _read_live_protection(slug, branch)
        changes, mode = _diff_protection(desired, live)
        yield {
            "repo": name,
            "slug": slug,
            "branch": branch,
            "kind": "human" if is_human else "agent",
            "desired": desired,
            "changes": changes,
            "mode": mode,
        }


def _cmd_diff() -> int:
    root, config = _load_context()
    if root is None:
        return 1
    if not _repos(config):
        UI.muted("No repos in platform.yaml.")
        return 0

    UI.header("Policy — branch-protection diff (desired vs live)")
    drift = False
    for rec in _plan_repos(root, config):
        name, branch, kind, mode = rec["repo"], rec["branch"], rec["kind"], rec["mode"]
        if kind == "skip":
            UI.warn(f"{name}: skipped — {rec['changes'][0]}")
            continue
        if kind == "error":
            UI.error(f"{name} ({branch}): {rec['changes'][0]}")
            drift = True
            continue
        if mode == "conformant":
            UI.ok(f"{name} ({branch}, {kind}-owned): conformant")
            continue
        drift = True
        UI.warn(f"{name} ({branch}, {kind}-owned): {mode}")
        for c in rec["changes"]:
            UI.muted(f"    {c}")
    return 1 if drift else 0


def _acting_is_cto(root) -> tuple[bool, str]:
    """(is_cto, message). Resolves OTAMAN_HUMAN against the roster; policy edits
    require the `cto` role (D5). On success `message` is the resolved name."""
    import os

    from otaman_core.human_roster import load_human_roster, resolve_roster_human

    try:
        roster = load_human_roster(root / "platform.yaml")
    except Exception:  # noqa: BLE001 - absent/invalid roster → treat as no cto
        roster = []
    entry = resolve_roster_human(roster, os.environ.get("OTAMAN_HUMAN"))
    if entry is None:
        return False, (
            "`otaman policy apply` edits enforcement and requires the 'cto' roster role — "
            "no acting human resolved (set OTAMAN_HUMAN to a roster human whose roles "
            "include 'cto')."
        )
    if "cto" not in entry.roles:
        return False, (
            f"{entry.name!r} lacks the required 'cto' role for `otaman policy apply` "
            f"(has: {', '.join(entry.roles) or 'none'})."
        )
    return True, entry.name


def _live_apply(root, config: dict) -> int:
    """4.4 / D12 apply->live handoff: shell to deploy's non-interactive entrypoint
    AFTER the plan is written. cli never loads the deploy credential — the child
    (`otaman-policy-apply-live`) resolves its own. Entrypoint absent => report and
    stop plan-only (NO silent manual fallback — the D11 anti-pattern)."""
    import shutil
    import subprocess

    entry = shutil.which("otaman-policy-apply-live")
    if not entry:
        UI.warn(
            "live-apply requested but 'otaman-policy-apply-live' is not on PATH — the plan was "
            "written, but protection was NOT applied. Install deploy's entrypoint (CE bootstrap), "
            "then re-run `otaman policy apply --apply-live`. (Not falling back to manual API "
            "calls — D11/D12.)"
        )
        return 2
    UI.header("Policy — live apply (deploy entrypoint)")
    UI.muted(f"$ otaman-policy-apply-live --root {root}")
    try:
        rc = subprocess.run([entry, "--root", str(root)]).returncode
    except (OSError, subprocess.SubprocessError) as exc:
        return _bail(f"live-apply entrypoint failed to run: {exc}", code=2)
    if rc == 0:
        UI.ok("Live protection converged to the plan.")
    else:
        UI.error(f"live-apply reported failures (exit {rc}) — see the per-repo output above.")
    return rc


def _cmd_apply(dry_run: bool, apply_live: bool = False) -> int:
    root, config = _load_context()
    if root is None:
        return 1
    if not _repos(config):
        UI.muted("No repos in platform.yaml.")
        return 0

    # policies.live_apply: auto behaves like --apply-live.
    policies = config.get("policies")
    live = apply_live or (isinstance(policies, dict) and policies.get("live_apply") == "auto")

    records = list(_plan_repos(root, config))
    for rec in records:
        if rec["kind"] == "skip":
            UI.warn(f"{rec['repo']}: skipped — {rec['changes'][0]}")
        elif rec["kind"] == "error":
            return _bail(f"{rec['repo']} ({rec['branch']}): {rec['changes'][0]}", code=2)

    actionable = [
        r for r in records if r["kind"] in ("human", "agent") and r["mode"] != "conformant"
    ]
    if not actionable:
        UI.ok("All repos conformant — nothing to apply.")
        return 0

    UI.header("Policy — apply plan (generate-and-diff)")
    tightens_human = False
    for r in actionable:
        UI.warn(f"{r['repo']} ({r['branch']}, {r['kind']}-owned): {r['mode']}")
        for c in r["changes"]:
            UI.muted(f"    {c}")
        if r["kind"] == "human":
            tightens_human = True

    if dry_run:
        need = "the 'cto' role" + (" + a human decision" if tightens_human else "")
        UI.muted(f"(dry-run: nothing written; applying would require {need})")
        return 0

    # D5: policy edits are a cto act.
    ok, who = _acting_is_cto(root)
    if not ok:
        return _bail(who, code=2)

    # D5: tightening live protection on a human-owned branch is a HUMAN-DECISION.
    if tightens_human:
        from otaman_cli import safety

        if not safety.confirm_human_decision(
            "otaman policy apply will TIGHTEN branch protection on human-owned branch(es)"
        ):
            return _bail("apply aborted — human decision not confirmed", code=2)

    # Emit the plan for deploy (step 3) to push live. Per the STOP-AT, cli never
    # PUTs branch protection itself.
    import json

    plan = {
        "generated_by": "otaman policy apply",
        "pack": "git",
        "acting_cto": who,
        "repos": [
            {
                "repo": r["repo"],
                "slug": r["slug"],
                "branch": r["branch"],
                "owner_kind": r["kind"],
                "mode": r["mode"],
                "desired": r["desired"],
            }
            for r in actionable
        ],
    }
    gen_dir = root / "policy" / "generated"
    gen_dir.mkdir(parents=True, exist_ok=True)
    plan_path = gen_dir / "branch-protection.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    UI.ok(f"Wrote apply plan for deploy: {plan_path.relative_to(root)}")

    if not live:
        UI.muted(
            "Plan-only. Pass --apply-live (or set policies.live_apply: auto) to apply it live "
            "via deploy's entrypoint; cli itself never pushes protection."
        )
        return 0
    return _live_apply(root, config)


# ---------------------------------------------------------------------------
# merge guard (2.1: check-merge)
#
# A read-only pre-merge check the launcher/agent calls before a `gh pr merge`
# into a base branch: exit non-zero => refuse. Owner intent comes from
# resolve_branch_owner + policy/git/branch-owners.yaml, NEVER from live branch
# protection (D4a point 3 — "protection configured" and "owner admits
# personally" look identical from the API; conflating them re-creates the
# false-block incident). Pairs with generated protection as the rails layer.

_GUARD_REFUSED = 3


def _branch_owners(root) -> dict:
    """The ``policy/git/branch-owners.yaml`` registry (branch -> owner), or {}."""
    import yaml

    path = root / "policy" / "git" / "branch-owners.yaml"
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict):
        return {}
    bo = data.get("branch-owners", data)
    if not isinstance(bo, dict):
        return {}
    return {k: v for k, v in bo.items() if isinstance(k, str) and isinstance(v, str)}


def _resolve_repo(root, config: dict, repo_arg: str | None) -> dict | None:
    """The repos[] entry for --repo, else the one whose path contains cwd."""
    repos = _repos(config)
    if repo_arg:
        return next((r for r in repos if r.get("name") == repo_arg), None)
    from pathlib import Path

    cwd = Path.cwd().resolve()
    for r in repos:
        path = r.get("path")
        if not path:
            continue
        rp = (root / path).resolve()
        if cwd == rp or rp in cwd.parents:
            return r
    return None


def _caller_identity(root) -> tuple[bool, str | None]:
    """(is_agent, name). A resolvable OTAMAN_HUMAN roster entry => human caller;
    otherwise the acting agent identity."""
    import os

    human = os.environ.get("OTAMAN_HUMAN")
    if human:
        from otaman_core.human_roster import load_human_roster, resolve_roster_human

        try:
            roster = load_human_roster(root / "platform.yaml")
        except Exception:  # noqa: BLE001 - absent/invalid roster → not a human caller
            roster = []
        entry = resolve_roster_human(roster, human)
        if entry is not None:
            return False, entry.name
    from otaman_cli.identity import resolve_agent_identity

    return True, resolve_agent_identity(root)


def _resolve_target_owner(
    root, config: dict, branch: str, repo_arg: str | None
) -> tuple[str | None, bool]:
    """(owner, owner_is_human) for a base branch. ``owner`` is None when owner-less.

    Owner intent only: branch-owners.yaml registry → `<type>/<owner>/<topic>`
    convention → the repo's declared owner for its default branch. Never reads
    live branch protection (D4a).
    """
    from otaman_core.policy import resolve_branch_owner

    repo = _resolve_repo(root, config, repo_arg)
    repo_owner = repo.get("owner") if repo else None

    is_default = False
    if repo and repo.get("path"):
        from otaman_core.git_host import detect_remote_for_repo

        repo_dir = (root / repo["path"]).resolve()
        exists = repo_dir.exists()
        info = detect_remote_for_repo(repo_dir) if exists else None
        slug = info.slug if info is not None and info.provider == "github" else ""
        default = _repo_default_branch(repo_dir if exists else None, slug)
        is_default = branch == default

    owner = resolve_branch_owner(
        branch,
        repo_owner=repo_owner,
        is_default_branch=is_default,
        branch_owners=_branch_owners(root),
    )
    return owner, (owner in _human_names(root) if owner else False)


def _cmd_check_merge(branch: str | None, repo_arg: str | None) -> int:
    root, config = _load_context()
    if root is None:
        return 1
    if not branch:
        return _bail("Usage: otaman policy check-merge <base-branch> [--repo NAME]", code=2)

    owner, owner_is_human = _resolve_target_owner(root, config, branch, repo_arg)
    caller_is_agent, caller = _caller_identity(root)

    if owner is None:
        return _bail(
            f"Refused — target branch {branch!r} is owner-less (no `<type>/<owner>/<topic>` "
            "convention, no branch-owners.yaml entry, not a mapped default branch). PRs into "
            "it are unadmittable; assign an owner before merging.",
            code=_GUARD_REFUSED,
        )

    # Owner-admission (D6): ONLY the owner — or a per-branch delegate, captured by
    # resolve_branch_owner via branch-owners.yaml — admits merges. This holds for
    # agent-owned branches too (spec-agent canon-gap 2026-09-03): a DIFFERENT agent
    # merging into another agent's repo is refused, not just the human-owned case.
    # Self-merge is the special case caller == owner.
    if caller != owner:
        if owner_is_human:
            return _bail(
                f"Refused — {branch!r} is human-owned (owner: {owner}); only {owner} or a "
                f"delegate admits it. caller {caller or 'unknown'} is neither — ask {owner} to "
                f"merge, or open the PR for {owner}'s review.",
                code=_GUARD_REFUSED,
            )
        return _bail(
            f"Refused — {branch!r} is owned by agent {owner}; only {owner} (its owner) or a "
            f"delegate admits merges into it. caller {caller or 'unknown'} is not the owner "
            f"(agents self-merge only their OWN repos' branches).",
            code=_GUARD_REFUSED,
        )

    caller_kind = "agent" if caller_is_agent else "human"
    owner_kind = "human" if owner_is_human else "agent"
    UI.ok(
        f"OK to merge into {branch} — owner {owner} [{owner_kind}] "
        f"== caller [{caller_kind}] (self-admit)."
    )
    return 0


def _cmd_annotate(branch: str | None, repo_arg: str | None) -> int:
    """4.2 PR annotation: print an admissibility annotation for a PR's base branch.

    Read-only, ALWAYS exit 0 — this is for display (a PR comment / CI annotation),
    not a gate. The gate is `check-merge` (exit non-zero). A PR-comment poster
    (CI / `otaman git-host`) pipes this text onto the PR.
    """
    root, config = _load_context()
    if root is None:
        return 1
    if not branch:
        return _bail("Usage: otaman policy annotate <base-branch> [--repo NAME]", code=2)

    owner, owner_is_human = _resolve_target_owner(root, config, branch, repo_arg)
    if owner is None:
        print(
            f"⛔ UNADMITTABLE: base branch `{branch}` is owner-less — no `<type>/<owner>/<topic>` "
            "match, no branch-owners.yaml entry. Assign an owner before this PR can be admitted."
        )
    elif owner_is_human:
        print(
            f"👤 Owner: `{owner}` (human). Only {owner} or a delegate admits this PR — "
            "agents must not merge it; it awaits the owner's review/merge."
        )
    else:
        print(
            f"🤖 Owner: `{owner}` (agent). The owning agent self-merges its own green PR — "
            "no human merge required."
        )
    return 0


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
        UI.muted("       otaman policy apply [--dry-run] [--apply-live]")
        UI.muted("       otaman policy check-merge <base-branch> [--repo NAME]")
        UI.muted("       otaman policy annotate <base-branch> [--repo NAME]")
        UI.muted("       otaman policy validate")
        return 0 if args else 1

    action, rest = args[0], args[1:]
    if action == "list":
        return _cmd_list()
    if action == "diff":
        return _cmd_diff()
    if action == "apply":
        return _cmd_apply(dry_run="--dry-run" in rest, apply_live="--apply-live" in rest)
    if action in ("check-merge", "annotate"):
        branch = None
        repo_arg = None
        i = 0
        while i < len(rest):
            a = rest[i]
            if a == "--repo" and i + 1 < len(rest):
                repo_arg = rest[i + 1]
                i += 2
            elif not a.startswith("-") and branch is None:
                branch = a
                i += 1
            else:
                return _bail(f"Unexpected argument: {a}")
        if action == "check-merge":
            return _cmd_check_merge(branch, repo_arg)
        return _cmd_annotate(branch, repo_arg)
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

    return _bail(
        f"Unknown action {action!r}. "
        "Actions: list, show, diff, apply, check-merge, annotate, validate"
    )


register(
    CommandSpec(
        name="policy",
        handler=cmd_policy,
        help="Policy engine: list packs | show effective policy | validate",
    )
)

__all__ = ["cmd_policy"]
