"""The one door onto core's registry access contract (registry-access-contract 1.2).

Every register read-for-write and every register write in this CLI goes through
`otaman_core.registry_access`, and every module reaches that module through here. The
delta names a direct registry-file access outside the contract a *conformance defect*,
so a second home for the probe would be the beginning of a second home for the access:
one `contract()` means one place that decides whether the bundle carries the contract,
and one message when it does not.

Display paths come through here too, via `read_fast` — core #116 added the lossy
read the measurement asked for, so the console no longer opens a register itself.
What stays on this side is the MEMOISATION: core's fast read is ~34ms on the live
251KB register where cli's memoised read is ~0.03ms warm, and Home takes six reads
against a 500ms frame budget (159ms unmemoised, measured). So `read_fast` is core's
reader with cli's cache in front of it, and a register that a human edits re-parses
on the next read because the key carries mtime and size.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: The contract's names this CLI actually calls for a WRITE. A bundle missing any of
#: them is an old bundle: refuse, rather than fall back to a direct write (see
#: `NO_CONTRACT`).
REQUIRED: tuple[str, ...] = (
    "load_register",
    "save_register",
    "get",
    "create_record",
    "apply_transition",
)

#: The read-side names (core #116). Probed separately because a bundle that can WRITE
#: through the contract is conformant even if it predates the fast display read — the
#: display path degrades to the round-trip loader rather than refusing to render.
READ_FAST: tuple[str, ...] = ("read_register_fast",)

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

    `missing_ok=True` is core's own affordance for the create-fresh case (core #116,
    from this rewire's finding 1) — the program whose register does not exist until its
    first `add`. The local `Register` construction it replaces is gone; the empty case
    is now the contract's answer, not this module's.
    """
    try:
        register = core.load_register(path, records_key=records_key, missing_ok=True)
    except TypeError:
        # A bundle that carries the contract but predates `missing_ok` (core #116). The
        # write path must still work there, so fall back to the shape it does have.
        if path.is_file():
            register = core.load_register(path, records_key=records_key)
        else:
            register = core.Register(data={records_key: []}, records_key=records_key)
    return _normalised(register, records_key)


def _normalised(register: Any, records_key: str) -> Any:
    """A register whose records key is a list — the shape every caller here assumes."""
    if not isinstance(register.data, dict):
        register.data = {records_key: []}
    if register.data.get(records_key) is None:
        register.data[records_key] = []
    return register


#: Memoised fast reads, keyed on (path, mtime_ns, size, records_key) — the console reads
#: the same register several times per frame and a human's edit must not be served from
#: the cache. Mirrors `yaml_fast`'s key for the same reason, and is cleared with it.
_FAST_CACHE: dict[tuple[str, int, int, str], Any] = {}


def cache_size() -> int:
    """How many memoised display reads are held — for the refresh-conformance test."""
    return len(_FAST_CACHE)


def clear_fast_cache() -> None:
    """Drop the memoised display reads (the console's refresh path, pressing `r`)."""
    _FAST_CACHE.clear()


def read_fast(path: Path, *, records_key: str, strict: bool = False) -> Any:
    """A read-only `Register` for a DISPLAY path — core's lossy reader, memoised.

    Read-only by construction: core marks the result `read_only` and `save_register`
    refuses it, so a display read cannot be written back and silently drop the
    comments the register's author put there.

    `strict=False` (the default, for a panel) returns the empty shape for a missing or
    unreadable file: the panel renders "none" rather than taking down the frame.
    `strict=True` lets the error out, which is what the validating loaders need — the
    console's registry-load FAILURE must stay loud (a silent empty tree was the
    silent-approval-loss class), and doctor has a check whose whole subject is a
    present-but-unloadable register.

    Measured on the live 251KB register: core's `load_register` round-trip 666ms, its
    `read_register_fast` 34ms, this memoised 0.03ms warm. Home's six reads are 159ms
    unmemoised against a 500ms frame budget, which is why the cache is here and not a
    nicety.
    """
    core = contract()
    if core is None or not all(hasattr(core, n) for n in READ_FAST):
        return _fallback_read(core, path, records_key, strict=strict)
    try:
        st = path.stat()
    except OSError:
        if strict:
            raise
        return core.Register(data={records_key: []}, records_key=records_key, read_only=True)
    key = (str(path), st.st_mtime_ns, st.st_size, records_key)
    hit = _FAST_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        register = _normalised(core.read_register_fast(path, records_key=records_key), records_key)
    except Exception:  # noqa: BLE001 - an unparseable register renders empty, never raises
        if strict:
            raise
        return core.Register(data={records_key: []}, records_key=records_key, read_only=True)
    _FAST_CACHE[key] = register
    return register


def _fallback_read(core: Any, path: Path, records_key: str, *, strict: bool = False) -> Any:
    """Display read for a bundle without `read_register_fast` (or without the contract).

    A render must not refuse: an old bundle gets the round-trip loader (slow but
    correct), and no bundle at all gets an empty shape. This is the ONE place that may
    degrade, because the alternative is a console that cannot draw.
    """
    if core is None:
        if strict:
            raise RuntimeError(NO_CONTRACT)
        return _Empty(records_key)
    try:
        return _normalised(core.load_register(path, records_key=records_key), records_key)
    except Exception:  # noqa: BLE001
        if strict:
            raise
        return _Empty(records_key)


class _Empty:
    """The empty register shape, for a bundle with no contract at all."""

    read_only = True

    def __init__(self, records_key: str) -> None:
        self.records_key = records_key
        self.data: dict[str, Any] = {records_key: []}

    def records(self) -> list[Any]:
        return []
