"""delivery-authorization-envelope 2.2 — the cli side of the envelope.

Core owns the vocabulary: the class registry, the floor, and what a well-formed
`authorizes:` block is. This owns the three places a human meets it — authoring
one at propose, seeing one at review, and MEASURING whether declaring a class
actually buys anything at runtime.

That last one is design D6, and it is the part worth being careful about. Every
class in core's registry starts `runtime-honored: limited`, which is the
fail-safe unmeasured value: declaring it authorizes nothing. A class only earns
`yes` by being measured against the live runtime — because an envelope that
promises autonomy the runtime refuses is worse than no envelope at all. The
agent proceeds believing it is authorized, hits a prompt nobody is watching, and
freezes: precisely the halt this whole change exists to remove.

So the measurement here is deliberately narrow and says what it covers. It
probes ONE runtime surface — the destructive-op guard — by asking it, for a
representative command per class, whether it would demand fresh confirmation. It
cannot see a permission-mode prompt, an MCP approval, or a tool the runtime has
not been told about. A class measuring `yes` here means "this guard does not
stop it", never "nothing will".
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

#: One representative command per class, used to ask the guard what it would do.
#: Chosen to be the SHAPE the class describes, not a command anyone runs — the
#: probe never executes them; it only asks the hook.
_PROBES: dict[str, str] = {
    "hooks-wiring": "cp hooks.json ~/.otaman/otaman-plugin-tree/hooks/hooks.json",
    "release-publish": "gh release create v0.0.0-probe",
    "branch-protection": "gh api -X PUT repos/o/r/branches/main/protection",
    "tenant-ssh-write": "ssh tenant@host 'cat > ~/.config/otaman/x'",
    "schema-migration": "otaman db migrate --apply",
    "service-restart": "systemctl --user restart otaman-runner",
    "org-secret-write": "gh secret set OTAMAN_TOKEN",
}


def _core() -> Any | None:
    """core's envelope module, or None on an install that predates it."""
    try:
        from otaman_core import delivery_envelope
    except Exception:  # noqa: BLE001 - absent → the caller says so, never "fine"
        return None
    needed = ("validate_envelope", "CLASS_REGISTRY", "FLOOR", "EnvelopeError")
    return delivery_envelope if all(hasattr(delivery_envelope, n) for n in needed) else None


def parse_cli_classes(values: list[str]) -> list[Any]:
    """`--authorizes` values into the shape core parses.

    Accepts `hooks-wiring` and `release-publish:otaman-deploy,otaman-cli`, and
    splits comma lists so one flag can carry several. Shape only — the registry
    and floor rules are core's, and re-checking them here would be a second
    opinion about canon.
    """
    out: list[Any] = []
    for value in values:
        for piece in str(value).split(","):
            token = piece.strip()
            if not token:
                continue
            if ":" in token:
                name, _, scope = token.partition(":")
                targets = [s.strip() for s in scope.split("|") if s.strip()]
                out.append({name.strip(): targets} if targets else name.strip())
            else:
                out.append(token)
    return out


def validate(values: list[str]) -> tuple[dict | None, str]:
    """``(envelope, error)`` for `--authorizes` *values*.

    A floor violation refuses NAMING THE FLOOR — the operator has to know which
    of the six they touched, not merely that something was rejected.
    """
    core = _core()
    if core is None:
        return None, (
            "otaman-core does not carry the envelope registry "
            "(delivery-authorization-envelope 2.1) — cannot validate; update the bundle"
        )
    try:
        return core.validate_envelope(parse_cli_classes(values)), ""
    except Exception as exc:  # noqa: BLE001 - core raises EnvelopeError with the reason
        return None, str(exc)


def validate_raw(raw: Any) -> tuple[dict | None, str]:
    """``(envelope, error)`` for an `authorizes:` value already read from YAML.

    The review-side twin of :func:`validate`, which takes CLI strings. Both go
    through core's `validate_envelope`, so what refuses at propose is exactly
    what refuses at review — a change that could be authored but not approved
    would be a trap.
    """
    core = _core()
    if core is None:
        return None, "otaman-core does not carry the envelope registry"
    try:
        return core.validate_envelope(raw), ""
    except Exception as exc:  # noqa: BLE001 - core raises EnvelopeError with the reason
        return None, str(exc)


def render_lines(envelope: dict | None) -> list[str]:
    """How an envelope reads at review — one line per class, scope and marker.

    The `runtime-honored` marker is shown BESIDE each class deliberately: a
    reviewer approving `release-publish` is approving something that, today,
    authorizes nothing at runtime, and hiding that would make the approval mean
    less than it appears to.
    """
    if not envelope:
        return ["authorizes: (nothing — every action escalates)"]
    core = _core()
    registry = getattr(core, "CLASS_REGISTRY", {}) if core else {}
    lines = ["authorizes:"]
    for name in sorted(envelope):
        scope = envelope.get(name)
        where = f" → {', '.join(scope)}" if scope else " → any target"
        marker = registry.get(name, "unknown")
        note = "" if marker == "yes" else f"   (runtime-honored: {marker} — authorizes nothing yet)"
        lines.append(f"  - {name}{where}{note}")
    return lines


# ---------------------------------------------------------------------------
# D6 — measurement, not assumption.


def _guard_script(root: Path) -> Path | None:
    """The installed destructive-op guard, if this host has one."""
    for candidate in (
        Path.home() / ".otaman" / "otaman-plugin-tree" / "scripts" / "check-destructive-op.sh",
        root / ".." / "otaman-plugin" / "scripts" / "check-destructive-op.sh",
    ):
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def _guard_would_ask(script: Path, command: str) -> bool | None:
    """Would the guard demand fresh confirmation for *command*?

    None when the probe could not run — which is NOT "it would not ask". An
    unmeasured class stays `limited`, so an unanswerable probe must never be
    read as a pass.
    """
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    try:
        result = subprocess.run(
            ["bash", str(script)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return '"permissionDecision":"ask"' in (result.stdout or "").replace(" ", "")


def measure(root: Path) -> dict[str, dict[str, Any]]:
    """`{class: {verdict, evidence}}` — what the live runtime does per class.

    EVERY verdict is `limited`, and that is the honest output of this probe
    rather than a placeholder. It sees one surface, so it can show that a class
    IS stopped, never that nothing stops it. The evidence line distinguishes
    three states — blocked here, not blocked here, unmeasurable — which is what
    a later probe of the other surfaces would build on.
    """
    core = _core()
    classes = sorted(getattr(core, "CLASS_REGISTRY", {}) or _PROBES)
    script = _guard_script(root)
    out: dict[str, dict[str, Any]] = {}
    for name in classes:
        probe = _PROBES.get(name)
        if script is None:
            out[name] = {
                "verdict": "limited",
                "evidence": "no destructive-op guard installed — nothing measured",
            }
            continue
        if probe is None:
            out[name] = {
                "verdict": "limited",
                "evidence": "no probe defined for this class — unmeasured",
            }
            continue
        asked = _guard_would_ask(script, probe)
        if asked is None:
            out[name] = {"verdict": "limited", "evidence": f"probe failed to run: {probe}"}
        elif asked:
            out[name] = {
                "verdict": "limited",
                "evidence": f"BLOCKED — the guard demands confirmation for: {probe}",
            }
        else:
            # NOT `yes`. This probe sees one runtime surface; a class earns `yes`
            # only when every surface that could prompt has been measured, and
            # permission modes, MCP approvals and the tool-permission layer are
            # not visible from here. Certifying on one negative would put
            # `runtime-honored: yes` on a class the runtime may still stop —
            # which is the halt this change exists to remove, with a promise
            # attached.
            out[name] = {
                "verdict": "limited",
                "evidence": f"not blocked by the destructive-op guard: {probe}",
                "unblocked_here": True,
            }
    return out


#: What a single-surface probe can and cannot conclude. Stated as data so the
#: command prints it rather than leaving the reader to infer it.
MEASUREMENT_SCOPE = (
    "Measures ONE runtime surface: the destructive-op guard. A class is reported "
    "`limited` unless every surface that could prompt has been measured — this "
    "probe can falsify (the guard stops it) but cannot certify (nothing else "
    "does). Promoting a class to `runtime-honored: yes` is a core registry "
    "change and needs the other surfaces measured first."
)


__all__ = [
    "MEASUREMENT_SCOPE",
    "measure",
    "parse_cli_classes",
    "render_lines",
    "validate",
    "validate_raw",
]
