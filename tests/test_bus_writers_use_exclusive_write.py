"""No bus writer may silently overwrite a message (deploy-agent 20260921T190949).

This guard exists because the bug class came BACK. `bus_write` was written in
2026-09-05 (propose-hardening) after cofounder-agent lost an SCR to a same-second
collision. It solved the problem — and then `notify-change` shipped, imported
that module's VALIDATOR, and wrote with a plain `write_text` anyway. deploy-agent
lost 2 of 6 spec-change dispatches to the identical failure sixteen days later,
with every call exiting 0.

So the fix for the reported bug is not enough on its own: what has to be pinned
is that no FUTURE writer can reintroduce it. This test reads the source and
fails on a bare `write_text` into the bus, which is the shape both incidents had.

Adding a legitimate exception means adding it to ALLOWED below with a reason —
which is the point. The doctrine is not "never write_text"; it is that
overwriting a distinct-content message loses data, while overwriting an
idempotent one does not, and the difference must be a decision somebody made on
purpose rather than a default nobody noticed.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "otaman_cli"

#: Writes into a bus directory that may stay plain `write_text`, each with the
#: reason it cannot lose anything. file -> (fragment of the line, why)
ALLOWED = {
    "commands/approve.py": [
        ('ack_file.write_text("approved', "ack is idempotent: same content every time"),
        ('ack_file.write_text("rejected', "ack is idempotent: same content every time"),
    ],
    "console/decision.py": [
        ('.human.ack").write_text', "ack is idempotent: same content every time"),
    ],
    "commands/bus_messaging.py": [
        (
            "msg_path.write_text(content",
            "rewrites the file write_message_exclusive just created, at the path it "
            "RETURNED, to correct the id: field — same path, same intended content",
        ),
        (
            "cc_path.write_text(cc_content",
            "same id-correction, on the CC copy's returned path",
        ),
        (
            "ack_file.write_text(status",
            "an ack records the CURRENT status; overwriting read->resolved is the "
            "state transition, not a lost message",
        ),
    ],
    "hitl/messages.py": [
        ('ack_path.write_text("resolved', "ack is idempotent: same content every time"),
    ],
}

#: Directory expressions that mean "this path is in the bus".
_BUS_DIRS = frozenset({"active_dir", "bus_active", "acks_dir", "archive_dir"})


def _bus_path_names(tree: ast.AST) -> set[str]:
    """Locals bound to a path under a bus directory — `x = active_dir / "y.md"`.

    Name-matching was the first version of this guard and it was too weak: it
    listed the variable names the existing writers happened to use, so simply
    calling the variable something else walked straight past it. (Proven, not
    assumed — reintroducing the notify-change bug as `written = bus_active / f`
    passed the name-based guard.) The next writer to get this wrong will not
    consult the list, so the guard has to follow the value instead.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        # Any `/` join whose left side mentions a bus directory.
        for sub in ast.walk(node.value):
            if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Div):
                if any(isinstance(n, ast.Name) and n.id in _BUS_DIRS for n in ast.walk(sub.left)):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            names.add(target.id)
    return names


def _write_text_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """``(lineno, receiver-name)`` for every ``<something>.write_text(...)``.

    The receiver is the variable when there is one, or the literal source of the
    expression for a direct `(active_dir / "x").write_text(...)`.
    """
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "write_text"):
            continue
        recv = func.value
        if isinstance(recv, ast.Name):
            out.append((node.lineno, recv.id))
        elif isinstance(recv, ast.BinOp) and isinstance(recv.op, ast.Div):
            if any(isinstance(n, ast.Name) and n.id in _BUS_DIRS for n in ast.walk(recv.left)):
                out.append((node.lineno, "<bus-dir expression>"))
    return out


def _offenders() -> list[str]:
    out: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover - the suite would be failing anyway
            continue
        lines = source.splitlines()
        bus_names = _bus_path_names(tree)
        allowed = ALLOWED.get(rel, [])
        for lineno, receiver in _write_text_calls(tree):
            if receiver not in bus_names and receiver != "<bus-dir expression>":
                continue
            line = lines[lineno - 1].strip()
            if any(fragment in line for fragment, _why in allowed):
                continue
            out.append(f"{rel}:{lineno}: {line}")
    return out


def test_no_bus_writer_uses_a_bare_write_text():
    offenders = _offenders()
    assert not offenders, (
        "These write into the bus with a plain write_text, which silently "
        "overwrites a same-named message:\n  "
        + "\n  ".join(offenders)
        + "\n\nUse otaman_cli.bus_write.write_message_exclusive (and use the path it "
        "RETURNS). If the write is genuinely idempotent, add it to ALLOWED in this "
        "test with the reason."
    )


def test_the_guard_can_actually_see_a_violation():
    """A structural guard that matches nothing would pass forever.

    Includes the evasion that defeated the first version of this guard: binding
    the bus path to an unlisted variable name. If that shape stops being
    detected, the guard has regressed to the version that missed a live bug.
    """
    samples = [
        # notify-change, 2026-09-21 — the reported incident
        'msg_path = bus_active / name\nmsg_path.write_text(body, encoding="utf-8")',
        # the same bug under a name no list would have contained
        'written = bus_active / name\nwritten.write_text(body, encoding="utf-8")',
        # complete.py / approve.py shape
        'filepath = active_dir / filename\nfilepath.write_text(content, encoding="utf-8")',
        # no intermediate variable at all
        '(active_dir / f"{ts}-x.md").write_text(msg, encoding="utf-8")',
    ]
    for src in samples:
        tree = ast.parse(src)
        names = _bus_path_names(tree)
        hits = [
            recv
            for _lineno, recv in _write_text_calls(tree)
            if recv in names or recv == "<bus-dir expression>"
        ]
        assert hits, f"guard no longer detects this shape:\n{src}"


def test_allowed_entries_still_exist():
    """An exception that no longer matches anything is stale — drop it.

    Keeps ALLOWED honest: a stale entry silently widens the guard's blind spot.
    """
    for rel, entries in ALLOWED.items():
        text = (SRC / rel).read_text(encoding="utf-8")
        for fragment, why in entries:
            assert fragment in text, f"stale ALLOWED entry in {rel}: {fragment!r} ({why})"
