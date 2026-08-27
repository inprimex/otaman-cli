"""The Textual console app (interactive-human-console, Iteration 1 skeleton).

Imported only when the `console` extra (Textual, exact-pinned) is present.
Task 1.1 lands the shell: a program-picker screen, a per-program pending
spec-change-request list, and footer key bindings. The proposal viewer +
approve/reject decision flow is task 1.2 (it pushes a ProposalScreen from the
pending list); the event-source refresh is task 1.3; the surviving tmux seat
is task 1.4.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, MarkdownViewer, Static

from otaman_cli.console.bus import Program, Proposal, discover_programs, list_pending_proposals


def _header() -> Header:
    # icon="" removes the default HeaderIcon glyph (⭘), which rendered as a
    # stray 'c' top-left in some terminals (5.1 finding #2.1). The command
    # palette is still reachable via ctrl+p.
    return Header(show_clock=False, icon="")


class _ProgramItem(ListItem):
    def __init__(self, program: Program) -> None:
        # markup=False: names/paths are arbitrary data — `[...]` must render
        # literally, not be parsed as Textual console markup.
        super().__init__(Label(f"{program.name}   ({program.root})", markup=False))
        self.program = program


class _ProposalItem(ListItem):
    def __init__(self, proposal: Proposal) -> None:
        super().__init__(
            Label(
                f"[{proposal.priority}] {proposal.subject}  —  from {proposal.from_agent}",
                markup=False,
            )
        )
        self.proposal = proposal


class ProgramPickerScreen(Screen):
    """Pick which program's bus to work on (one bus at a time — Q8)."""

    # priority=True so plain q/r fire even when the ListView has focus
    # (5.1 finding #2.2: plain keys previously did nothing; only ctrl+ worked).
    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("r", "rescan", "Rescan", priority=True),
    ]

    def __init__(self, programs: list[Program]) -> None:
        super().__init__()
        self._programs = programs

    def compose(self) -> ComposeResult:
        yield _header()
        if self._programs:
            yield Static("Select a program (Enter):", id="picker-hint")
            yield ListView(*[_ProgramItem(p) for p in self._programs], id="program-list")
        else:
            yield Static(
                "No programs found (no platform.yaml under the search root).",
                id="picker-empty",
            )
        yield Footer()

    def action_rescan(self) -> None:
        self.app.rescan_programs()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        program = getattr(event.item, "program", None)
        if program is not None:
            self.app.push_screen(PendingListScreen(program))


class PendingListScreen(Screen):
    """Pending spec-change-requests for the picked program."""

    BINDINGS = [
        Binding("escape", "back", "Back", priority=True),
        Binding("r", "refresh", "Refresh", priority=True),
        Binding("q", "quit", "Quit", priority=True),
    ]

    def __init__(self, program: Program, *, event_source=None) -> None:
        super().__init__()
        self.program = program
        # Injectable per task 1.3: the console consumes changes through ONE
        # event-source interface (polling in I1; fswatch/NATS later) with no
        # console rework. Tests pass a fake source.
        self._source = event_source
        self._own_source = event_source is None
        # Session cache of the pending list (5.1 finding #5): navigation renders
        # from this instantly; only the mount-time load and the polling source's
        # incremental refresh ever re-scan the bus. None = not loaded yet.
        self._cache: list[Proposal] | None = None

    def compose(self) -> ComposeResult:
        yield _header()
        yield Static(f"Program: {self.program.name}", id="prog-header", markup=False)
        yield ListView(id="pending-list")
        yield Footer()

    def on_mount(self) -> None:
        # First load: paint a loading row, then scan off the UI thread (5.1
        # finding #2.4). Subsequent navigation renders from cache (finding #5).
        self._refresh(show_loading=True)
        if self._source is None:
            from otaman_cli.console.events import make_event_source

            self._source = make_event_source(self.program)
        # The polling source drives INCREMENTAL refresh: when the pending set
        # moves it re-scans off-thread and updates the cache — navigation never
        # triggers a scan (finding #5). Marshal the callback onto the UI thread.
        self._source.start(lambda: self.app.call_from_thread(self._refresh))

    def on_unmount(self) -> None:
        if self._source is not None and self._own_source:
            self._source.stop()

    def on_screen_resume(self) -> None:
        # Back from a ProposalScreen: render the cached list INSTANTLY (no
        # rescan — finding #5, back-navigation used to re-pay the full scan),
        # then reconcile in the background so a just-decided proposal drops off.
        self._paint(self._cache or [])
        self._refresh(show_loading=False)

    def _refresh(self, *, show_loading: bool = False) -> None:
        """(Re)scan the bus off the UI thread, updating the cache.

        Shows a loading row only on the very first load (empty cache); a
        background reconcile repaints in place without a blank flash.
        """
        if show_loading and not self._cache:
            lv = self.query_one("#pending-list", ListView)
            lv.clear()
            lv.append(ListItem(Label("Loading pending proposals…")))
        self.run_worker(self._load_worker, thread=True, exclusive=True, group="load")

    def _load_worker(self) -> None:
        proposals = list_pending_proposals(self.program)  # bus scan, off the UI thread
        self.app.call_from_thread(self._apply, proposals)

    def _apply(self, proposals: list[Proposal]) -> None:
        self._cache = proposals
        self._paint(proposals)

    def _paint(self, proposals: list[Proposal]) -> None:
        lv = self.query_one("#pending-list", ListView)
        lv.clear()
        if proposals:
            for p in proposals:
                lv.append(_ProposalItem(p))
        else:
            lv.append(ListItem(Label("No pending proposals.")))

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self._refresh(show_loading=not self._cache)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        proposal = getattr(event.item, "proposal", None)
        if proposal is not None:
            self.app.push_screen(ProposalScreen(self.program, proposal))


class ProposalScreen(Screen):
    """Read the rendered proposal and approve/reject it (task 1.2).

    The human's keypress here is the confirmation — no LLM, no adapter prompt.
    Approve/reject route through the same ledger-gated privileged writer as
    `otaman approve`, stamped with the SSH-derived identity.
    """

    BINDINGS = [
        Binding("a", "approve", "Approve", priority=True),
        Binding("x", "reject", "Reject", priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("q", "quit", "Quit", priority=True),
    ]

    def __init__(self, program: Program, proposal: Proposal) -> None:
        super().__init__()
        self.program = program
        self.proposal = proposal

    def compose(self) -> ComposeResult:
        yield _header()
        yield Static(
            f"{self.proposal.subject}   —   from {self.proposal.from_agent}",
            id="proposal-title",
            markup=False,
        )
        yield MarkdownViewer(self.proposal.body, show_table_of_contents=False, id="proposal-body")
        yield Footer()

    def _decide(self, ok: bool, message: str) -> None:
        self.app.notify(message, severity="information" if ok else "error", timeout=8)
        if ok:
            self.app.pop_screen()  # PendingListScreen.on_screen_resume refreshes

    def action_approve(self) -> None:
        from otaman_cli.console import decision
        from otaman_cli.console.identity import resolve_identity

        identity = resolve_identity(self.program.root)
        ok, message = decision.approve(self.program, self.proposal, identity)
        self._decide(ok, message)

    def action_reject(self) -> None:
        from otaman_cli.console import decision
        from otaman_cli.console.identity import resolve_identity

        identity = resolve_identity(self.program.root)
        ok, message = decision.reject(self.program, self.proposal, identity)
        self._decide(ok, message)

    def action_back(self) -> None:
        self.app.pop_screen()


class OtamanConsole(App):
    """`otaman -i` — the human console shell."""

    TITLE = "Otaman Console"

    def __init__(self, programs: list[Program], *, search_root=None) -> None:
        super().__init__()
        self._programs = programs
        self._search_root = search_root

    def on_mount(self) -> None:
        # Restore the operator's saved theme (5.1 finding #2.3: the command
        # palette's theme choice didn't persist across sessions).
        from otaman_cli.console.prefs import load_prefs

        saved = load_prefs().get("theme")
        if saved:
            try:
                self.theme = saved
            except Exception:  # noqa: BLE001 - unknown/removed theme → default
                pass
        self.push_screen(ProgramPickerScreen(self._programs))

    def watch_theme(self, theme: str) -> None:
        # Persist whenever the theme changes (e.g. via the ctrl+p palette).
        from otaman_cli.console.prefs import load_prefs, save_prefs

        prefs = load_prefs()
        if prefs.get("theme") != theme:
            prefs["theme"] = theme
            save_prefs(prefs)

    def rescan_programs(self) -> None:
        if self._search_root is not None:
            self._programs = discover_programs(self._search_root)
        # Rebuild the picker with the fresh list.
        self.pop_screen()
        self.push_screen(ProgramPickerScreen(self._programs))


__all__ = [
    "OtamanConsole",
    "PendingListScreen",
    "ProgramPickerScreen",
    "ProposalScreen",
]
