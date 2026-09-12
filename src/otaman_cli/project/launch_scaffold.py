"""Launch-block scaffolding for `otaman project assign` (identity-divergence 1.2).

The pmeets incident: a post-init repo registered via `project assign` got its
`repos[]` ownership entry but NO launch block, so its agent started via an
untracked path and wrote a status file under a name nothing validated — a
phantom agent for 17 hours. `otaman init --update` only PATCHES launch commands
that already exist; it never creates one, so the gap never closed on its own.

Canon (cli-ux spec): assign SHALL leave the assigned repo launchable — scaffold
the launch block with `OTAMAN_AGENT` set to the owner's agent name from
agents.yaml, idempotently on re-assign — or refuse naming exactly what is
missing. "Ownership entry but no launch path" is not a reachable end state.

The scaffold derives its shape from a SIBLING repo's existing launch block, so
the program's own conventions (nvm sourcing, plugin-dir path, ssh-vs-local
shell, respawn loop) carry over instead of being guessed. Everything here is
pure: no I/O, no subprocess — the caller owns reading/writing platform.yaml.
"""

from __future__ import annotations

import re
from typing import Any

#: Fallback when no sibling repo has a launch block to copy conventions from.
#: Mirrors the shape the launcher generates: continue the session if one exists,
#: else start fresh on the bus check.
_DEFAULT_COMMAND = (
    "OTAMAN_AGENT={agent} claude --continue "
    "--plugin-dir ~/.otaman/otaman-plugin-tree "
    "|| OTAMAN_AGENT={agent} claude "
    "--plugin-dir ~/.otaman/otaman-plugin-tree /otaman:check"
)

_AGENT_ENV_PAT = re.compile(r"(OTAMAN_AGENT=\S+\s+)?claude\b")


def inject_agent_env(command: str, agent: str) -> str:
    """Prepend/replace ``OTAMAN_AGENT=<agent>`` before each ``claude`` call.

    Idempotent: an existing ``OTAMAN_AGENT=<old>`` is rewritten rather than
    doubled, so re-running the scaffold over its own output is a no-op. Mirrors
    ``commands.init._inject_agent_env_into_command`` but rewrites EVERY claude
    invocation in the command (the conventional block has two — continue and
    fresh), not just the first.
    """
    if not agent or not command:
        return command
    return _AGENT_ENV_PAT.sub(f"OTAMAN_AGENT={agent} claude", command)


def derive_title(repo_name: str) -> str:
    """A short pane title from a repo name: ``otaman-cli`` -> ``Cli``.

    Matches the convention in existing launch blocks (Core, Plugin, Bridge);
    a name without the program prefix keeps its own words title-cased.
    """
    stem = re.sub(r"^otaman[-_]", "", repo_name or "", flags=re.IGNORECASE)
    stem = stem or repo_name or "Agent"
    return " ".join(part.capitalize() for part in re.split(r"[-_\s]+", stem) if part)


def _sibling_launch(repos: list[Any], exclude_name: str) -> dict[str, Any] | None:
    """The first other repo's launch block that has commands, or None."""
    for repo in repos or []:
        if not isinstance(repo, dict) or repo.get("name") == exclude_name:
            continue
        launch = repo.get("launch")
        if isinstance(launch, dict) and launch.get("commands"):
            return launch
    return None


def build_launch_block(
    data: dict[str, Any],
    repo_name: str,
    agent: str,
    *,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The launch block for *repo_name* running as *agent*.

    Conventions (shell, command shape) come from a sibling repo's block when one
    exists, else from :data:`_DEFAULT_COMMAND`. An *existing* block is REFRESHED
    in place — its own commands keep their text with only the agent env rewritten,
    and its title/color/shell survive — so a re-assign is idempotent and never
    clobbers hand-tuning.
    """
    repos = data.get("repos") if isinstance(data, dict) else None
    repos = repos if isinstance(repos, list) else []

    if existing and existing.get("commands"):
        block = dict(existing)
        cmds = block["commands"]
        cmds = [cmds] if isinstance(cmds, str) else list(cmds)
        block["commands"] = [inject_agent_env(str(c), agent) for c in cmds]
        block.setdefault("title", derive_title(repo_name))
        return block

    sibling = _sibling_launch(repos, repo_name)
    if sibling is not None:
        template = sibling.get("commands")
        template = template[0] if isinstance(template, list) and template else template
        command = inject_agent_env(str(template), agent)
        block: dict[str, Any] = {"title": derive_title(repo_name)}
        if sibling.get("shell"):
            block["shell"] = sibling["shell"]
        block["commands"] = [command]
        return block

    return {
        "title": derive_title(repo_name),
        "commands": [_DEFAULT_COMMAND.format(agent=agent)],
    }


def owner_refusal(owner: str, known_agents: set[str]) -> str | None:
    """A refusal message when *owner* is not a registered agent, else None.

    An EMPTY *known_agents* means there is no registry to check against (no
    agents.yaml, or an empty one) — the same no-op convention the doctor's
    ``check_launch_command_agent_names`` uses, so a program that doesn't keep an
    agent registry is not blocked from assigning. When the registry DOES exist,
    an unregistered owner is refused with exactly what is missing: an unknown
    owner must never materialize as an implicit new agent (the phantom-agent
    bug class).
    """
    if not known_agents or owner in known_agents:
        return None
    listed = ", ".join(sorted(known_agents))
    return (
        f"Owner {owner!r} is not a registered agent in .agents/agents.yaml.\n"
        f"  Missing: an agents.yaml entry named {owner!r}.\n"
        f"  Registered agents: {listed}\n"
        f"  Fix: add {owner!r} to .agents/agents.yaml, or re-run with "
        f"--owner <one of the registered agents>."
    )


__all__ = [
    "build_launch_block",
    "derive_title",
    "inject_agent_env",
    "owner_refusal",
]
