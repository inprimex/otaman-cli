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

#: Where agent declarations live when platform.yaml does not inline an `agents:`
#: block. The first version of this surface read platform.yaml ALONE and reported
#: "(no agents declared in platform.yaml)" on a program with nineteen agents in its
#: registry — honest about where it looked, and looking in the wrong place, which
#: made "doctor shows effective routing per agent" show nothing at all.
REGISTRY_REL = ".agents/agents.yaml"


@dataclass
class AgentRoute:
    """One agent's effective route, as resolved by core."""

    agent: str
    family: str = ""
    model: str = ""
    local: bool = False
    #: True when the agent declares no route at all — the default path.
    default_path: bool = True
    #: core's canonical route key (`Route.id`, core #121), carried so the display
    #: label and the telemetry key cannot drift. Empty on a bundle that predates it.
    route_id: str = ""

    @property
    def key(self) -> str:
        """The presentation-free route key — CORE's `Route.id` when it is available.

        core owns this string as of #121 (e2a8ef8), after plugin's lrb-1.6 call site
        needed one and found that every caller would otherwise invent its own. It
        encodes family, model AND `local`, because the same family/model run
        tenant-local vs off-tenant is a different route for cost and sensitivity and
        must not collapse into one telemetry bucket — which is exactly what the flat
        `family/model` rendering below used to do.

        The fallback is for an older core only. It deliberately reproduces `Route.id`'s
        form, `@local` included, rather than the old flat one: a key that silently drops
        the local bit is the collapse the gate measures, so an old bundle gets a
        correct-shaped key rather than a subtly wrong one.
        """
        if self.route_id:
            return self.route_id
        base = self.family if not self.model else f"{self.family}/{self.model}"
        return f"{base}@local" if self.local else base

    @property
    def label(self) -> str:
        """What a HUMAN reads: core's key plus cli's own where-suffix.

        The split is core's instruction on #121: `id` is the telemetry key and may not
        move for presentation reasons; the suffix is display and may. Rendering the key
        here rather than re-deriving `family/model` is what keeps the two from drifting
        — the defect this replaces had cli composing the identifier half itself.
        """
        if self.default_path:
            return NO_ROUTE
        where = "local" if self.local else "leaves tenant"
        return f"{self.key} ({where})"


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
    #: Which file the agent declarations came from — `platform.yaml` when it inlines
    #: an `agents:` block, else the registry, else "" for neither. Rendered, because
    #: "this program declares no agents" and "I read the file that has none" are
    #: different facts and the empty listing looks identical.
    agents_source: str = ""

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


def _registry_entries(root: Path) -> list[dict[str, Any]]:
    """Agent declarations (name + body) from `.agents/agents.yaml`.

    core's registry reader (`validate_message.load_known_agents`) returns NAMES, and
    a route lives in the entry's BODY — so this reads the file. It PROBES core for an
    entries reader first (`load_agent_declarations`), so the day core exports one this
    stops being a place that knows where the registry lives; until then the location
    is restated here and in `cleanup_bus.get_agents`, which is the duplication to
    close when that reader appears.
    """
    try:
        from otaman_core import validate_message

        reader = getattr(validate_message, "load_agent_declarations", None)
        if reader is not None:
            entries = reader(root)
            return [e for e in entries if isinstance(e, dict)]
    except Exception:  # noqa: BLE001 - fall through to the file
        pass
    path = root / ".agents" / "agents.yaml"
    if not path.is_file():
        return []
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - unreadable registry → no declarations found
        return []
    raw = data.get("agents") if isinstance(data, dict) else None
    if isinstance(raw, list):
        return [e for e in raw if isinstance(e, dict)]
    if isinstance(raw, dict):
        return [{"name": name, **body} for name, body in raw.items() if isinstance(body, dict)]
    return []


def _declaration_config(root: Path, config: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """``(config core resolves against, where the declarations came from)``.

    platform.yaml wins when it inlines `agents:` — a program that puts its
    declarations there means them. Otherwise the registry is merged in under the key
    core reads, so `effective_route` stays the single resolution point either way
    (nothing here parses a `route`).
    """
    if config.get("agents"):
        return config, "platform.yaml"
    entries = _registry_entries(root)
    if entries:
        return {**config, "agents": entries}, REGISTRY_REL
    return config, ""


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

    config, surface.agents_source = _declaration_config(root, config)
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
        surface.routes.append(_agent_route(name, route))
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
    where = f" from {surface.agents_source}" if surface.agents_source else ""
    lines.append(f"routes{where} (resolved by otaman_core.llm_router.effective_route):")
    if not surface.routes:
        lines.append(f"  (no agents declared — checked platform.yaml and {REGISTRY_REL})")
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


@dataclass
class Declared:
    """The outcome of declaring a route — what changed, where, or why not.

    `changed=False` with no `error` means the declaration already said this: a
    re-declaration is a no-op that reports itself rather than rewriting the file and
    claiming work (nss — a verb that did nothing must not report success).
    """

    agent: str
    where: str = ""
    changed: bool = False
    before: str = ""
    after: str = ""
    error: str = ""


def _route_mapping(family: str, model: str | None, local: bool) -> dict[str, Any]:
    """The `route:` value to write. A family-only route is written as a bare string.

    Core accepts both forms, and the shorter one is what a human writing it by hand
    would put there — a surface that always emitted the three-key mapping would make
    every hand-written declaration look wrong by comparison.
    """
    if not model and not local:
        return family
    mapping: dict[str, Any] = {"family": family}
    if model:
        mapping["model"] = model
    if local:
        mapping["local"] = True
    return mapping


def declare(
    root: Path, agent: str, family: str, *, model: str = "", local: bool = False
) -> Declared:
    """Write *agent*'s route into the file that declares it (llm-router 1.4).

    Validated through CORE before the write: the candidate declaration is handed to
    `effective_route`, so a malformed route is refused by the same parser the bridge
    dispatch uses rather than discovered at dispatch. An unknown agent is refused
    naming the file that was checked — silently creating a declaration for a
    misspelled agent would put a route on nobody.
    """
    core = _core()
    if core is None:
        return Declared(agent=agent, error="otaman-core does not carry the llm-router seam")
    if not family:
        return Declared(agent=agent, error="a route needs a family (e.g. --family ollama)")

    from otaman_cli.registries.loader import yaml_dump, yaml_load

    platform_path = root / "platform.yaml"
    registry_path = root / ".agents" / "agents.yaml"
    for path in (platform_path, registry_path):
        doc = yaml_load(path) or {}
        entries = doc.get("agents") if isinstance(doc, dict) else None
        entry = None
        if isinstance(entries, list):
            for candidate in entries:
                if isinstance(candidate, dict) and candidate.get("name") == agent:
                    entry = candidate
                    break
        elif isinstance(entries, dict) and isinstance(entries.get(agent), dict):
            entry = entries[agent]
        if entry is None:
            continue

        route = _route_mapping(family, model or None, local)
        probe = {"agents": [{"name": agent, "route": route}]}
        try:
            resolved = core.effective_route(probe, agent)
        except Exception as exc:  # noqa: BLE001 - core's RouterError names the problem
            return Declared(agent=agent, error=f"refused by core: {exc}")
        if resolved is None:  # pragma: no cover - a built route always resolves
            return Declared(agent=agent, error="core resolved no route from that declaration")

        before = entry.get("route")
        after_label = _label_of(resolved)
        if before == route:
            return Declared(
                agent=agent,
                where=_rel(root, path),
                changed=False,
                before=after_label,
                after=after_label,
            )
        before_label = NO_ROUTE
        if before is not None:
            try:
                prior = core.effective_route({"agents": [{"name": agent, "route": before}]}, agent)
                before_label = _label_of(prior) if prior else NO_ROUTE
            except Exception:  # noqa: BLE001 - an invalid prior value is still replaced
                before_label = f"(invalid: {before!r})"
        entry["route"] = route
        yaml_dump(doc, path)
        return Declared(
            agent=agent,
            where=_rel(root, path),
            changed=True,
            before=before_label,
            after=after_label,
        )

    return Declared(
        agent=agent,
        error=(
            f"{agent!r} is not declared in platform.yaml or {REGISTRY_REL} — "
            "a route cannot be declared for an agent the program does not know"
        ),
    )


def _agent_route(agent: str, route: Any) -> AgentRoute:
    """An `AgentRoute` for a core `Route`, carrying core's `id` when the bundle has it.

    `getattr(route, "id", "")` rather than an import-time probe: `Route.id` is a
    property on core's frozen dataclass (#121), so its presence is the bundle's
    answer and an older core simply yields "" and takes the local fallback.
    """
    return AgentRoute(
        agent=agent,
        family=getattr(route, "family", ""),
        model=getattr(route, "model", "") or "",
        local=bool(getattr(route, "local", False)),
        default_path=False,
        route_id=str(getattr(route, "id", "") or ""),
    )


def _label_of(route: Any) -> str:
    """An `AgentRoute` label for a core `Route`, so one vocabulary renders both."""
    return _agent_route("", route).label


def _rel(root: Path, path: Path) -> str:
    """The written file as a program-relative POSIX path.

    `as_posix`, not `str`: on Windows the latter renders `.agents\agents.yaml`, which
    is right for the OS and wrong for this string's two jobs — it is printed next to
    `REGISTRY_REL` (a forward-slash literal, as the docs and the spec write it) and it
    is what a reader copies into a message or a config. One spelling everywhere, which
    is also what let the Windows leg of CI catch this.
    """
    try:
        return path.relative_to(root).as_posix()
    except ValueError:  # pragma: no cover - both paths are built from root
        return path.as_posix()


__all__ = [
    "NOT_CHECKED",
    "NO_ROUTE",
    "REGISTRY_REL",
    "AgentRoute",
    "Declared",
    "Surface",
    "declare",
    "load",
    "render_lines",
]
