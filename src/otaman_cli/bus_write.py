"""Create-exclusive bus-message writes (propose-hardening, 2026-09-05).

Bus stems are second-precision (``<YYYYMMDDTHHMMSS>-<from>-to-<to>-<type>``); two
messages created in the same second collide, and a plain ``write_text`` silently
OVERWRITES the first — losing a whole SCR/message (cofounder-agent hit this live:
two ``otaman propose`` calls in one second, the second destroyed the first, its
blocked entry left orphaned).

Every distinct-content message write goes through :func:`write_message_exclusive`,
which never overwrites: it creates the file with ``open(..., "x")`` (O_EXCL) and,
on collision, appends ``-2``/``-3``/… to the stem until it finds a free name,
returning the path actually written. Callers MUST use the returned path for any
downstream reference (stem, report line) so the collision suffix is honored.

Idempotent ack writes (``resolved`` every time) deliberately do NOT use this —
overwriting an ack with identical content loses nothing.
"""

from __future__ import annotations

from pathlib import Path

#: Safety backstop so a pathological loop can't spin forever; far above any real
#: number of same-second, same-route messages.
_MAX_COLLISION_SUFFIX = 10_000


class BusMessageValidationError(Exception):
    """A rendered bus message failed its own validator — refuse the write.

    bus-writer-self-validation 1.2: the platform must never write a message its
    own validator would reject. Writers catch this and surface the errors to the
    sender as a hard failure; NOTHING is written.
    """

    def __init__(self, errors: list[str], path: Path | None = None) -> None:
        self.errors = errors
        self.path = path
        super().__init__(
            f"message failed self-validation ({len(errors)} error(s)): " + "; ".join(errors)
        )


def assert_message_valid(content: str, path: Path | None = None) -> None:
    """Raise :class:`BusMessageValidationError` if *content* would fail the
    bus-message validator (the write-time gate, bus-writer-self-validation 1.2).

    Uses otaman-core's ``validate_message_before_write`` (errors only; warnings
    never block). A no-op when the validator can't be imported, so the gate
    never turns a missing dependency into a write failure."""
    try:
        from otaman_core.validate_message import validate_message_before_write
    except Exception:  # noqa: BLE001 - validator unavailable → don't block writes
        return
    errors = validate_message_before_write(content)
    if errors:
        raise BusMessageValidationError(errors, path)


def write_message_exclusive(
    path: Path, content: str, *, encoding: str = "utf-8", validate: bool = False
) -> Path:
    """Write *content* to *path* without ever overwriting an existing file.

    On collision, tries ``<stem>-2<suffix>``, ``<stem>-3<suffix>``, … in *path*'s
    directory until one is free. Returns the :class:`Path` actually written.
    Raises :class:`FileExistsError` only if every candidate up to the backstop is
    taken (not reachable in practice).

    When ``validate=True``, the rendered *content* is run through the write-time
    validator BEFORE any file is created; a failure raises
    :class:`BusMessageValidationError` and nothing is written (bus-writer-self-
    validation 1.2). Opt-in for now: the gate is wired into the conformant
    writers (send/propose here; complete/approve/notify call
    :func:`assert_message_valid` directly). The console fleet-broadcast writers
    still emit ``type: info`` with ``to: all`` — nonconforming under the new
    canon, pending a broadcast-type ruling — so the default stays off until they
    are aligned, rather than silently breaking them here.
    """
    if validate:
        assert_message_valid(content, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate = path
    n = 2
    while True:
        try:
            with open(candidate, "x", encoding=encoding) as f:
                f.write(content)
            return candidate
        except FileExistsError:
            if n > _MAX_COLLISION_SUFFIX:
                raise
            candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
            n += 1


__all__ = [
    "write_message_exclusive",
    "assert_message_valid",
    "BusMessageValidationError",
]
