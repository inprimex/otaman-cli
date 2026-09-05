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


def write_message_exclusive(path: Path, content: str, *, encoding: str = "utf-8") -> Path:
    """Write *content* to *path* without ever overwriting an existing file.

    On collision, tries ``<stem>-2<suffix>``, ``<stem>-3<suffix>``, … in *path*'s
    directory until one is free. Returns the :class:`Path` actually written.
    Raises :class:`FileExistsError` only if every candidate up to the backstop is
    taken (not reachable in practice).
    """
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


__all__ = ["write_message_exclusive"]
