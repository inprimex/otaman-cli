"""llm-router-backend 1.4 (cli half) — the route surfaces over core's resolution.

Core owns everything that decides: the `router:` block parse, the ModelBackend
seam, and — answering the question I asked before building (20261001T135210) —
ROUTE RESOLUTION. `effective_route(config, agent)` is the single resolution point,
so this surface and the bridge's dispatch cannot disagree about which route an
agent is on. That mattered more than it sounds: the thing the bridge enforces at
dispatch is a sensitivity guard, and a doctor view that resolved the raw field
itself could show a human a route the guard would refuse.

So nothing here parses `agents[].route`, and nothing here decides whether a target
leaves the tenant. This module reads the config, asks core per agent, and renders.

Three positions:

**An agent with no route is RENDERED, not omitted.** "Uses the default path" and
"not configured" and "I did not look at this agent" are three different facts, and
an agent missing from a listing is indistinguishable from all three. Same rule as
sghc 1.2's opt-out rendering.

**An unreadable platform.yaml is NOT an unconfigured one.** A program with no
`router:` block has opted out and the default path is byte-identical — that is the
spec's first scenario. A config that exists and does not parse means routing could
not be determined, and reporting that as "no routing configured" is the fail-open
class this repo has now corrected at four sites (#223 twice, #235).

**A route the guard would refuse is reported BEFORE dispatch.** If the program
declares `local_only_classes` and an agent routes to a non-local target, every
guarded call on that agent will refuse at dispatch. Core supplies the predicate;
surfacing it here turns a runtime refusal into a config finding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Rendered where an agent declares no route — the default path, explicitly.
NO_ROUTE = "(none — default path)"

#: Verdict words shared with the other doctor surfaces, so one run does not speak
#: two dialects (`security_gates`, runtime freshness).
NOT_CHECKED = "not-checked"


@dataclass
class AgentRoute:
    """One agent's effective route, as resolved by core."""

    agent: str
    family: str = ""
    model: str = ""
    local: bool = False
    #: True when the agent declares no route at all — the default path.
    default_path: bool = True

    @property
    def label(self) -> str:
        if self.default_path:
            return NO_ROUTE
        where = "local" if self.local else "leaves tenant"
        return f"{self.family}{'/' + self.model if self.model else ''} ({where})"


@dataclass
class Surface:
    """What `otaman policy routes` and the doctor check both render.

    `error` set means routing could not be determined — distinct from
    `configured=False`, which means the program declared no router and the default
    path applies.
    """

    configured: bool = False
    backend: str = ""
    base_url: str = ""
    local_only_classes: tuple[str, ...] = ()
    routes: list[AgentRoute] = field(default_factory=list)
    error: str | None = None

    @property
    def guarded_routes(self) -> list[AgentRoute]:
        """Routes that leave the tenant while local-only classes are declared.

        Every guarded call on these agents refuses at dispatch (bridge 1.3). Not an
        error — a cloud route is legitimate for unguarded content — but a human
        reading the config is entitled to know it before a refusal tells them.
        """
        if not self.local_only_classes:
            return []
        return [r for r in self.routes if not r.default_path and not r.local]


def _core() -> Any | None:
    """core's llm_router module, or None on a bundle that predates it."""
    try:
        from otaman_core import llm_router
    except Exception:  # noqa: BLE001 - absent → not-checked, never "no routing"
        return None
    needed = ("parse_router_config", "effective_route", "select_backend", "route_leaves_tenant")
    return llm_router if all(hasattr(llm_router, n) for n in needed) else None


def _declared_agents(config: dict[str, Any]) -> list[str]:
    """Agent names in declaration order, from either `agents:` shape.

    Both shapes are accepted because core accepts both, and a surface that only
    understood the list form would silently show zero agents for a program using
    the mapping form — worse than refusing.
    """
    raw = config.get("agents", [])
    if isinstance(raw, list):
        return [
            str(a.get("name"))
            for a in raw
            if isinstance(a, dict) and isinstance(a.get("name"), str) and a.get("name")
        ]
    if isinstance(raw, dict):
        return [str(name) for name in raw if isinstance(name, str) and name]
    return []


def load(root: Path, *, agent: str = "") -> Surface:
    """Resolve the effective route for every declared agent (or just *agent*)."""
    surface = Surface()
    core = _core()
    if core is None:
        surface.error = (
            "otaman-core does not carry the llm-router seam (llm-router-backend 1.1)"
            " — cannot resolve routing; update the bundle"
        )
        return surface

    platform = root / "platform.yaml"
    if not platform.is_file():
        surface.error = f"no platform.yaml at {platform} — cannot resolve routing"
        return surface
    try:
        import yaml

        config = yaml.safe_load(platform.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - unreadable is NOT unconfigured
        surface.error = f"platform.yaml could not be read: {exc}"
        return surface
    if not isinstance(config, dict):
        surface.error = "platform.yaml is not a mapping — cannot resolve routing"
        return surface

    try:
        rc = core.parse_router_config(config)
    except Exception as exc:  # noqa: BLE001 - core raises RouterError on a bad block
        # The block EXISTS and is wrong. Saying "no routing configured" here would
        # report an opt-out the program did not choose.
        surface.error = f"router: block is invalid: {exc}"
        return surface

    surface.configured = rc.backend is not None
    surface.backend = rc.backend or getattr(core, "DEFAULT_BACKEND", "default")
    surface.base_url = rc.base_url or ""
    surface.local_only_classes = tuple(sorted(rc.local_only_classes))

    names = [agent] if agent else _declared_agents(config)
    for name in names:
        try:
            route = core.effective_route(config, name)
        except Exception as exc:  # noqa: BLE001 - one bad declaration, named
            surface.routes.append(AgentRoute(agent=f"{name} [invalid: {exc}]"))
            continue
        if route is None:
            surface.routes.append(AgentRoute(agent=name))
            continue
        surface.routes.append(
            AgentRoute(
                agent=name,
                family=route.family,
                model=route.model or "",
                local=bool(route.local),
                default_path=False,
            )
        )
    return surface


def render_lines(surface: Surface) -> list[str]:
    """How routing reads at review — one line per agent, backend named first."""
    if surface.error:
        return [f"llm routing: {NOT_CHECKED} — {surface.error}"]
    lines = []
    if surface.configured:
        where = f" → {surface.base_url}" if surface.base_url else ""
        lines.append(f"backend: {surface.backend}{where}")
    else:
        # The spec's first scenario, stated rather than left to inference.
        lines.append(f"backend: {surface.backend} (no router: block — native path, unchanged)")
    if surface.local_only_classes:
        lines.append("local-only classes: " + ", ".join(surface.local_only_classes))
    else:
        lines.append("local-only classes: none declared — no route is guarded")
    lines.append("")
    lines.append("routes (resolved by otaman_core.llm_router.effective_route):")
    if not surface.routes:
        lines.append("  (no agents declared in platform.yaml)")
    for route in surface.routes:
        lines.append(f"  - {route.agent}: {route.label}")
    guarded = surface.guarded_routes
    if guarded:
        lines.append("")
        lines.append("these routes leave the tenant while local-only classes are declared:")
        for route in guarded:
            lines.append(f"  - {route.agent} → {route.family} — guarded calls refuse at dispatch")
    if not surface.configured:
        lines.append("")
        lines.append("to declare a route: agents[].route: <family>, or")
        lines.append("  route: {family: <f>, model: <m>, local: true} for an in-tenant target")
    return lines


__all__ = [
    "NOT_CHECKED",
    "NO_ROUTE",
    "AgentRoute",
    "Surface",
    "load",
    "render_lines",
]
