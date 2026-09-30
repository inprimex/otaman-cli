"""console-reactive-store 2.3 — no filesystem read on a render path.

The whole change rests on one property: views read the store, and IO is a
background concern with exactly one owner. That property is invisible in a diff
— a screen that quietly re-reads the bus looks like any other screen, and #172
already proved a console cache can vanish in a refactor with nobody noticing.

So it is enforced here, structurally, over the AST rather than by grep: a method
on a Screen-like class that performs filesystem IO is a defect unless it is
REGISTERED below with the reason it is allowed.

Per the debt-register convention this repo canonised (see
`test_blocked_list_consumes_core.py`): the register is keyed on CONTENT, not
line numbers — a register that needs re-syncing after every unrelated edit gets
re-synced carelessly, and then it is not a register. Anything not listed fails,
and a listed entry that no longer exists fails too, so the register cannot
quietly widen the blind spot it documents.
"""

from __future__ import annotations

import ast
from pathlib import Path

import otaman_cli.console as console_pkg

#: Attribute calls that touch the filesystem.
_IO_ATTRS = frozenset(
    {
        "read_text",
        "write_text",
        "glob",
        "rglob",
        "iterdir",
        "stat",
        "exists",
        "is_file",
        "is_dir",
        "open",
        "mkdir",
        "unlink",
    }
)

#: Functions that DERIVE from the filesystem. Calling one on a render path is
#: the same defect as reading a file directly — it just reads more of them.
_DERIVATIONS = frozenset(
    {
        "list_human_queue",
        "list_pending_proposals",
        "build_artifact_tree",
        "derive_lifecycle_rows",
        "tree_fallback_notice",
        "read_body",
        "_load_raw",
        "node_detail_text",
        "role_emphasis",
        "list_authored_changes",
        "list_programs",
    }
)

#: Classes that render. A method on one of these runs on the UI thread unless
#: it is a worker.
_RENDERING_BASES = frozenset({"Screen", "ModalScreen", "App", "Widget", "ListItem", "Static"})

#: The register: `Class.method` → why its IO is deliberate.
#:
#: Every entry is a decision, not an exemption. Three shapes recur:
#:   * LAZY BODY — the design says bodies are fetched when a detail opens; one
#:     read there is free where 700 in a list were not.
#:   * ACTION PATH — a write needs CURRENT truth, and a cached projection would
#:     be the wrong thing to check before mutating.
#:   * TRIVIAL PROBE — a single existence check, not a scan.
_REGISTERED = {
    "InboxMessageScreen.compose": (
        "LAZY BODY — bus.read_body for the ONE message being opened. The list "
        "carries no bodies precisely so this read is affordable here."
    ),
    "ProposalScreen.compose": ("LAZY BODY — same as InboxMessageScreen, for the decision view."),
    "CapabilityDetailScreen.compose": (
        "LAZY BODY — one capability file, read when its detail opens."
    ),
    "RegistryDetailScreen._status_verb": (
        "ACTION PATH — re-reads outcomes.yaml before promote/demote. Checking a "
        "cached projection before a write would validate against state the file "
        "may no longer have."
    ),
    "TreeScreen._capability_detail": (
        "TRIVIAL PROBE — a single is_file() to decide whether a capability doc exists, not a scan."
    ),
    "OtamanConsole._open_session_log": (
        "ACTION PATH — opens the session log for the human; the point of the verb is the file."
    ),
}


def _console_modules() -> list[Path]:
    return sorted(Path(console_pkg.__file__).parent.glob("*.py"))


def _offenders() -> dict[str, set[str]]:
    """`{"Class.method": {markers}}` for every rendering method doing IO."""
    found: dict[str, set[str]] = {}
    for path in _console_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {
                b.id if isinstance(b, ast.Name) else getattr(b, "attr", "") for b in node.bases
            }
            if not (bases & _RENDERING_BASES):
                continue
            for fn in node.body:
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                # A `_worker` runs on a thread — that is where IO belongs.
                if fn.name.endswith("_worker"):
                    continue
                markers: set[str] = set()
                for sub in ast.walk(fn):
                    if isinstance(sub, ast.Attribute) and sub.attr in _IO_ATTRS:
                        markers.add(sub.attr)
                    elif isinstance(sub, ast.Name) and sub.id in _DERIVATIONS:
                        markers.add(sub.id)
                if markers:
                    found[f"{node.name}.{fn.name}"] = markers
    return found


def test_no_unregistered_filesystem_read_on_a_render_path():
    """The property the whole change rests on.

    A new screen that scans the bus, or an old one that starts to again, fails
    here — which is the only way this survives a refactor, because it looks like
    ordinary code in a diff.
    """
    unregistered = {k: sorted(v) for k, v in _offenders().items() if k not in _REGISTERED}
    assert not unregistered, (
        "filesystem IO on a render path:\n  "
        + "\n  ".join(f"{k} -> {v}" for k, v in sorted(unregistered.items()))
        + "\n\nMove it into a `*_worker`, read the store or a projection instead, "
        "or register it above WITH THE REASON it is deliberate."
    )


def test_the_register_has_no_stale_entries():
    """A registered site that no longer does IO is stale — drop it, or the
    register quietly widens the guard's blind spot."""
    stale = sorted(set(_REGISTERED) - set(_offenders()))
    assert not stale, f"stale debt-register entries: {stale}"


def test_every_register_entry_gives_a_reason():
    """An entry without a reason is an exemption, which is what this pattern
    exists to avoid."""
    for site, reason in _REGISTERED.items():
        assert len(reason) > 40, f"{site} is registered without a real reason"
        assert any(shape in reason for shape in ("LAZY BODY", "ACTION PATH", "TRIVIAL PROBE")), (
            f"{site} does not name which allowed shape it is"
        )


def test_the_guard_can_actually_see_io():
    """Both-directions verification, in the guard itself: a scanner that finds
    nothing would pass this suite forever while the property rotted."""
    assert _offenders(), "the detector found NOTHING — it is not looking correctly"
    assert set(_offenders()) >= set(_REGISTERED), "the detector lost sites it used to see"


def test_the_guard_catches_a_planted_read(tmp_path, monkeypatch):
    """The planted-defect check 3.1's gate also runs. Verified against a real
    module written to disk, not a string — the guard parses files."""
    planted = tmp_path / "planted.py"
    planted.write_text(
        "from textual.screen import Screen\n\n\n"
        "class PlantedScreen(Screen):\n"
        "    def compose(self):\n"
        "        return self.program.root.read_text()\n",
        encoding="utf-8",
    )
    import tests.test_console_no_render_path_io as mod

    monkeypatch.setattr(mod, "_console_modules", lambda: [planted])
    offenders = mod._offenders()
    assert "PlantedScreen.compose" in offenders
    assert "read_text" in offenders["PlantedScreen.compose"]


def test_a_worker_is_allowed_to_read(tmp_path, monkeypatch):
    """IO belongs on a thread. A guard that flagged workers too would push
    people to do the reading inline, which is the opposite of the point."""
    ok = tmp_path / "ok.py"
    ok.write_text(
        "from textual.screen import Screen\n\n\n"
        "class OkScreen(Screen):\n"
        "    def _load_worker(self):\n"
        "        return self.path.read_text()\n",
        encoding="utf-8",
    )
    import tests.test_console_no_render_path_io as mod

    monkeypatch.setattr(mod, "_console_modules", lambda: [ok])
    assert mod._offenders() == {}
