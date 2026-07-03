"""Flag-parsing helpers shared by the outcome/solution/persona registry
commands. Moved verbatim from main.py during the F020 decomposition
(these three were only ever used by those three commands).
"""

from __future__ import annotations

from otaman_cli.main import UI


def _parse_flag_value(rest: list[str], flag: str, *, default: str | None = None) -> str | None:
    """Consume `--flag VALUE` from *rest* (mutates), returning VALUE or default."""
    if flag in rest:
        i = rest.index(flag)
        if i + 1 < len(rest):
            value = rest[i + 1]
            del rest[i:i + 2]
            return value
    return default


def _parse_flag_list(rest: list[str], flag: str) -> list[str]:
    """Consume all `--flag VALUE` occurrences from *rest* (mutates), returning list."""
    values: list[str] = []
    while flag in rest:
        i = rest.index(flag)
        if i + 1 < len(rest):
            values.append(rest[i + 1])
            del rest[i:i + 2]
        else:
            del rest[i:i + 1]
            break
    return values


def _parse_dependencies(deps: list[str]) -> list[dict]:
    """Parse `--depends-on KIND:VALUE` strings into typed dependency dicts.

    Format:
        outcome:JTBD-3-foo        → {kind: outcome, ref: JTBD-3-foo}
        solution:SOL-1-bar        → {kind: solution, ref: SOL-1-bar}
        external:Email provider   → {kind: external, name: "Email provider"}
    """
    out: list[dict] = []
    for d in deps:
        if ":" not in d:
            UI.warn(f"Ignoring malformed --depends-on (need KIND:VALUE): {d!r}")
            continue
        kind, value = d.split(":", 1)
        kind = kind.strip()
        value = value.strip()
        if kind in ("outcome", "solution"):
            out.append({"kind": kind, "ref": value})
        elif kind == "external":
            out.append({"kind": "external", "name": value})
        else:
            UI.warn(f"Ignoring --depends-on with unknown kind {kind!r}: {d!r}")
    return out
