"""The one door onto core's registry access contract (registry-access-contract 1.2).

Every register read-for-write and every register write in this CLI goes through
`otaman_core.registry_access`, and every module reaches that module through here. The
delta names a direct registry-file access outside the contract a *conformance defect*,
so a second home for the probe would be the beginning of a second home for the access:
one `contract()` means one place that decides whether the bundle carries the contract,
and one message when it does not.

Display paths are the documented exception and are tracked as debt, not fixed here —
see `docs/registry-access-debt.md`. The contract's `load_register` is a ruamel
round-trip (~700ms on the live register); the console's memoised `yaml_read` is ~0.1ms,
and Home alone takes six reads against a 500ms frame budget. Read-only rendering cannot
corrupt a register, so the chokepoint that matters is the write path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: The contract's names this CLI actually calls. A bundle missing any of them is an old
#: bundle: refuse, rather than fall back to a direct write (see `NO_CONTRACT`).
REQUIRED: tuple[str, ...] = (
    "load_register",
    "save_register",
    "get",
    "create_record",
    "apply_transition",
)

#: Why a verb refuses when the contract is absent. Phrased as the remedy plus the
#: reason, because "update the bundle" alone reads like a version pin — the point is
#: that writing around the contract is the defect the contract removes.
NO_CONTRACT = (
    "this otaman-core does not carry the registry access contract "
    "(registry-access-contract 1.1) — update the bundle. Refusing to write the "
    "register directly: that is the conformance defect the contract removes."
)


def contract() -> Any | None:
    """`otaman_core.registry_access` when it carries `REQUIRED`, else None.

    An attribute probe, never a version pin: a bundle is judged by the names it
    actually exposes.
    """
    try:
        from otaman_core import registry_access
    except Exception:  # noqa: BLE001 - absent → refuse, never write directly
        return None
    return registry_access if all(hasattr(registry_access, n) for n in REQUIRED) else None


def open_register(core: Any, path: Path, *, records_key: str) -> Any:
    """The contract's `Register` for *path*, empty-but-valid when the file is absent.

    `load_register` opens the path and raises `FileNotFoundError`, where the
    `yaml_load` it replaces returned `{}` for exactly this case — the program whose
    register does not exist until its first `add`. Constructing `core.Register` stays
    inside the contract (it is part of its public API, and nothing writes the file but
    `save_register`); reported to core so the affordance can move there.
    """
    if path.is_file():
        register = core.load_register(path, records_key=records_key)
    else:
        register = core.Register(data={records_key: []}, records_key=records_key)
    if not isinstance(register.data, dict):
        register.data = {records_key: []}
    if register.data.get(records_key) is None:
        register.data[records_key] = []
    return register
