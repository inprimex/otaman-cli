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
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from otaman_cli.console.bus import Program, Proposal, discover_programs, list_pending_proposals


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

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "rescan", "Rescan"),
    ]

    def __init__(self, programs: list[Program]) -> None:
        super().__init__()
        self._programs = programs

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
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
        ("escape", "back", "Back"),
        ("r", "refresh", "Refresh"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, program: Program) -> None:
        super().__init__()
        self.program = program

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(f"Program: {self.program.name}", id="prog-header", markup=False)
        yield ListView(id="pending-list")
        yield Footer()

    def on_mount(self) -> None:
        self._reload()

    def _reload(self) -> None:
        lv = self.query_one("#pending-list", ListView)
        lv.clear()
        proposals = list_pending_proposals(self.program)
        if proposals:
            for p in proposals:
                lv.append(_ProposalItem(p))
        else:
            lv.append(ListItem(Label("No pending proposals.")))

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self._reload()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        proposal = getattr(event.item, "proposal", None)
        if proposal is not None:
            # Task 1.2 replaces this with a rendered ProposalScreen + approve/reject.
            self.app.notify(f"Selected: {proposal.stem} (viewer lands in task 1.2)")


class OtamanConsole(App):
    """`otaman -i` — the human console shell."""

    TITLE = "Otaman Console"

    def __init__(self, programs: list[Program], *, search_root=None) -> None:
        super().__init__()
        self._programs = programs
        self._search_root = search_root

    def on_mount(self) -> None:
        self.push_screen(ProgramPickerScreen(self._programs))

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
]
