"""`otaman policy …` — the operator surface for the policy engine (policy-engine 2.1 + 4.2).

The engine itself is ``otaman_core.policy`` (loader, tightest-wins composition,
effective-policy API, owner resolution); this command reimplements none of that
algebra — it is the read / generate / guard surface on top of it.

This module lands the **read-only** verbs first:

- ``otaman policy list`` — registered packs + the policy selected for each, and
  whether it resolves on disk or falls back to the Otaman-shipped standard.
- ``otaman policy show [<pack>] [--repo R] [--agent A] [--json]`` — the composed,
  tightest-wins effective policy for a pack (default ``git``), plus any refused
  loosening attempts.
- ``otaman policy validate`` — every selected policy parses and composes with no
  refused loosening; non-zero exit on any structural error or loosening.

The remaining verbs land in follow-up PRs (kept out of the dispatcher until then
so the surface never advertises a verb it can't honor): ``diff``/``apply``
(generate-and-diff protection, HUMAN-DECISION when tightening a human-owned
branch, ``cto`` role gate), ``check-merge`` (the agent-into-human-owned-branch
merge guard), and the 4.2 observability surface.
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
        UI.muted("       otaman policy validate")
        return 0 if args else 1

    action, rest = args[0], args[1:]
    if action == "list":
        return _cmd_list()
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

    return _bail(f"Unknown action {action!r}. Actions: list, show, validate")


register(
    CommandSpec(
        name="policy",
        handler=cmd_policy,
        help="Policy engine: list packs | show effective policy | validate",
    )
)

__all__ = ["cmd_policy"]
