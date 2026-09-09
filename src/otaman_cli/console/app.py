"""The Textual console app (interactive-human-console, Iteration 1 skeleton).

Imported only when the `console` extra (Textual, exact-pinned) is present.
Task 1.1 lands the shell: a program-picker screen, a per-program pending
spec-change-request list, and footer key bindings. The proposal viewer +
approve/reject decision flow is task 1.2 (it pushes a ProposalScreen from the
pending list); the event-source refresh is task 1.3; the surviving tmux seat
is task 1.4.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    MarkdownViewer,
    Static,
)

from otaman_cli.console.bus import Program, Proposal, discover_programs, list_pending_proposals

# A path that can never be a program root — used to resolve the identity badge
# on the picker (no program picked yet) without a cwd platform.yaml false-match.
_NO_PROGRAM_ROOT = Path("/nonexistent-otaman-console-picker")


def _header() -> Header:
    # icon="" removes the default HeaderIcon glyph (⭘), which rendered as a
    # stray 'c' top-left in some terminals (5.1 finding #2.1). The command
    # palette is still reachable via ctrl+p.
    return Header(show_clock=False, icon="")


def _identity_badge_widget(program_root: Path) -> Static:
    """A persistent top-right badge showing whether the operator is VERIFIED
    against *program_root*'s roster (Roman's request via deploy 2.1). Pure
    render of resolve_identity() — no new identity logic. markup=False so the
    OTAMAN_HUMAN value can't be parsed as console markup; colour comes from the
    verified/unverified CSS class, not inline markup."""
    from otaman_cli.console.identity import identity_badge, resolve_identity

    ident = resolve_identity(program_root)
    return Static(
        identity_badge(ident),
        id="identity-badge",
        markup=False,
        classes="verified" if ident.verified else "unverified",
    )


def _mode_banner(view: str, hints: str) -> Static:
    """Persistent plain-words header for a console screen (D9, Roman UX ruling).

    Every screen states, in plain words, what the human is looking at + the key
    hints — so no view is a bare list the operator has to decode. Applies to all
    current and future views.
    """
    return Static(f"{view}\n{hints}", id="mode-banner", markup=False)


class _ProgramItem(ListItem):
    def __init__(self, program: Program) -> None:
        # markup=False: names/paths are arbitrary data — `[...]` must render
        # literally, not be parsed as Textual console markup.
        super().__init__(Label(f"{program.name}   ({program.root})", markup=False))
        self.program = program


_TYPE_TAG = {"spec-change-request": "SCR", "outcome-proposal": "outcome"}


class _ProposalItem(ListItem):
    def __init__(self, proposal: Proposal) -> None:
        tag = _TYPE_TAG.get(proposal.msg_type, proposal.msg_type)
        super().__init__(
            Label(
                f"[{tag}] [{proposal.priority}] {proposal.subject}  —  from {proposal.from_agent}",
                markup=False,
            )
        )
        self.proposal = proposal


class ProgramPickerScreen(Screen):
    """Pick which program's bus to work on (one bus at a time — Q8)."""

    # priority=True so plain q/r fire even when the ListView has focus
    # (5.1 finding #2.2: plain keys previously did nothing; only ctrl+ worked).
    BINDINGS = [
        Binding("q", "app.quit", "Quit", priority=True),
        Binding("r", "rescan", "Rescan", priority=True),
    ]

    def __init__(self, programs: list[Program]) -> None:
        super().__init__()
        self._programs = programs

    def compose(self) -> ComposeResult:
        yield _header()
        # Resolve the badge against the first program's roster (best-effort at
        # the picker; a picked program re-resolves against its own root).
        yield _identity_badge_widget(self._programs[0].root if self._programs else _NO_PROGRAM_ROOT)
        yield _mode_banner(
            "Programs — pick one to review", "↑↓ select · enter open · r rescan · q quit"
        )
        if self._programs:
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
        Binding("l", "lifecycle", "Lifecycle", priority=True),
        Binding("b", "browse", "Spec review", priority=True),
        Binding("q", "app.quit", "Quit", priority=True),
    ]

    def action_lifecycle(self) -> None:
        self.app.push_screen(LifecycleScreen(self.program))

    def action_browse(self) -> None:
        self.app.push_screen(ArtifactBrowserScreen(self.program))

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
        yield _identity_badge_widget(self.program.root)
        yield _mode_banner(
            f"Proposals — pending SCRs & outcome-proposals · {self.program.name}",
            "enter open · l lifecycle · b spec review · r refresh · esc back · q quit",
        )
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


class ReasonModal(ModalScreen[str | None]):
    """Capture a one-line reason for a decision (1.2: every verb records a reason).

    Enter submits the typed reason (may be empty); Esc cancels the decision. The
    result is delivered to the push_screen callback: a string (possibly "") to
    proceed, or None when cancelled.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True)]

    def __init__(self, verb: str) -> None:
        super().__init__()
        self._verb = verb

    def compose(self) -> ComposeResult:
        yield Static(
            f"{self._verb.capitalize()} — enter a reason (optional), Enter to confirm:",
            id="reason-prompt",
            markup=False,
        )
        yield Input(placeholder="reason…", id="reason-input")

    def on_mount(self) -> None:
        self.query_one("#reason-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ProposalScreen(Screen):
    """Read the rendered item and approve / reject / defer it (task 1.2).

    The human's keypress here is the confirmation — no LLM, no adapter prompt.
    Each verb first captures a reason (ReasonModal), then routes through
    decision.py stamped with the SSH-derived identity: a spec-change-request
    approve mints `spec-change-approved` (parity with `/otaman:approve`);
    outcome-proposal decisions are audit sign-offs; defer records an audit entry
    and leaves the item pending. Every decision leaves a bus audit entry.
    """

    BINDINGS = [
        Binding("a", "approve", "Approve", priority=True),
        Binding("A", "approve_auto", "Approve (auto-delivery)", priority=True),
        Binding("x", "reject", "Reject", priority=True),
        Binding("d", "defer", "Defer", priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("q", "app.quit", "Quit", priority=True),
    ]

    def __init__(self, program: Program, proposal: Proposal) -> None:
        super().__init__()
        self.program = program
        self.proposal = proposal

    def compose(self) -> ComposeResult:
        yield _header()
        yield _identity_badge_widget(self.program.root)
        yield _mode_banner(
            "Proposal — read & decide",
            "a approve · A approve-auto · x reject · d defer · esc back · q quit",
        )
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

    def _apply_decision(self, verb: str, reason: str, *, delivery: str | None = None) -> None:
        from otaman_cli.console import decision
        from otaman_cli.console.identity import resolve_identity

        identity = resolve_identity(self.program.root)
        if verb == "approve":
            ok, message = decision.approve(
                self.program, self.proposal, identity, reason=reason, delivery=delivery
            )
        else:
            fn = {"reject": decision.reject, "defer": decision.defer}[verb]
            ok, message = fn(self.program, self.proposal, identity, reason=reason)
        self._decide(ok, message)

    def _prompt_and_decide(self, verb: str, *, delivery: str | None = None) -> None:
        def _after(reason: str | None) -> None:
            if reason is not None:  # None = cancelled
                self._apply_decision(verb, reason, delivery=delivery)

        label = "approve-auto" if delivery == "auto" else verb
        self.app.push_screen(ReasonModal(label), _after)

    def action_approve_auto(self) -> None:
        self._prompt_and_decide("approve", delivery="auto")

    def action_approve(self) -> None:
        self._prompt_and_decide("approve")

    def action_reject(self) -> None:
        self._prompt_and_decide("reject")

    def action_defer(self) -> None:
        self._prompt_and_decide("defer")

    def action_back(self) -> None:
        self.app.pop_screen()


_TRIAGE_ABBR = {
    "active": "active",
    "archive-candidate": "arch-cand",
    "paused-decision": "paused",
    "absorbed": "absorbed",
    "dormant": "dormant",
}


class LifecycleScreen(Screen):
    """The lifecycle TABLE (IHC 1.5 / D10): every active change, one row, columns
    from the spec, grouped by triage class (active rows first — the moving work
    separates from the dormant tail at a glance). ``n`` nudges the highlighted
    row's next actor; the row shows when it was last nudged (anti-spam).
    """

    BINDINGS = [
        Binding("escape", "back", "Back", priority=True),
        Binding("r", "refresh", "Refresh", priority=True),
        Binding("n", "nudge", "Nudge", priority=True),
        Binding("y", "ratify", "Ratify", priority=True),
        Binding("a", "archive", "Archive", priority=True),
        Binding("q", "app.quit", "Quit", priority=True),
    ]

    _COLUMNS = (
        "triage",
        "change",
        "stage",
        "state",
        "tasks",
        "days",
        "next actor",
        "last touch",
        "nudged",
    )

    def __init__(self, program: Program) -> None:
        super().__init__()
        self.program = program
        self._rows: list = []  # ChangeRow in table order, indexed by cursor_row

    def compose(self) -> ComposeResult:
        yield _header()
        yield _identity_badge_widget(self.program.root)
        yield _mode_banner(
            f"Lifecycle — all changes by triage · {self.program.name}",
            "↑↓ rows · n nudge · y ratify · a archive · r refresh · esc back · q quit",
        )
        table = DataTable(id="lifecycle-table", cursor_type="row", zebra_stripes=True)
        yield table
        yield Static("", id="lifecycle-detail", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#lifecycle-table", DataTable)
        for col in self._COLUMNS:
            table.add_column(col, key=col)
        self._load()

    def action_refresh(self) -> None:
        self._load()

    def action_back(self) -> None:
        self.app.pop_screen()

    def _load(self) -> None:
        self.run_worker(self._worker, thread=True, exclusive=True, group="lifecycle")

    def _worker(self) -> None:
        from otaman_cli.console.lifecycle import _specs_changes_dir
        from otaman_cli.lifecycle import derive_change_table

        active_dir, _ = self.program.bus_paths()
        rows = derive_change_table(
            changes_dir=_specs_changes_dir(self.program),
            bus_active_dir=active_dir if active_dir.is_dir() else None,
        )
        self.app.call_from_thread(self._paint, rows)

    def _paint(self, rows: list) -> None:
        self._rows = rows
        table = self.query_one("#lifecycle-table", DataTable)
        table.clear()
        for r in rows:
            name_cell = f"{r.name} [auto]" if r.delivery == "auto" else r.name
            table.add_row(
                _TRIAGE_ABBR.get(r.triage, r.triage or "—"),
                name_cell,
                r.stage or "—",
                r.state,
                f"{r.tasks_done}/{r.tasks_total}",
                r.age,
                r.next_actor,
                r.last_touch,
                r.last_nudged or "—",
            )
        if not rows:
            self.query_one("#lifecycle-detail", Static).update("No changes found.")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._rows):
            r = self._rows[idx]
            note = f" — {r.triage_note}" if r.triage_note else ""
            self.query_one("#lifecycle-detail", Static).update(
                f"{r.name}: {r.triage or 'untriaged'}{note}"
            )

    def action_nudge(self) -> None:
        table = self.query_one("#lifecycle-table", DataTable)
        idx = table.cursor_row
        if not (0 <= idx < len(self._rows)):
            return
        row = self._rows[idx]

        def _after(note: str | None) -> None:
            if note is None:
                return
            from otaman_cli.lifecycle import send_nudge

            ok, msg = send_nudge(self.program, row, note=note)
            self.app.notify(msg, severity="information" if ok else "error", timeout=6)
            self._load()  # refresh so the last-nudged column updates

        self.app.push_screen(ReasonModal("nudge"), _after)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Enter opens the per-change detail view (1.3).
        idx = event.cursor_row
        if 0 <= idx < len(self._rows):
            self.app.push_screen(ChangeDetailScreen(self.program, self._rows[idx].name))

    def _highlighted(self):
        table = self.query_one("#lifecycle-table", DataTable)
        idx = table.cursor_row
        return self._rows[idx] if 0 <= idx < len(self._rows) else None

    def action_ratify(self) -> None:
        # D1: ratify is offered only where the human is the next actor (a
        # ratify-blocked row); otherwise the key is a clear no-op with a hint.
        row = self._highlighted()
        if row is None:
            return
        if "human" not in row.next_actor or "ratify" not in row.next_actor:
            self.app.notify(f"Ratify not applicable — {row.name} is not ratify-blocked.", timeout=5)
            return

        def _after(reason: str | None) -> None:
            if reason is None:
                return
            if not reason.strip():
                self.app.notify("Ratify needs a reason.", severity="error", timeout=5)
                return
            from otaman_cli.console.identity import resolve_identity
            from otaman_cli.console.lifecycle import ratify_change

            by = resolve_identity(self.program.root).operator
            ok, msg = ratify_change(self.program, row.name, by=by, reason=reason)
            self.app.notify(msg, severity="information" if ok else "error", timeout=8)
            if ok:
                self._load()

        self.app.push_screen(ReasonModal("ratify"), _after)

    def action_archive(self) -> None:
        # D1: archive is offered only on a complete-unarchived row whose archive
        # gate is ALLOWED (the gate is re-checked inside archive_change).
        from otaman_cli.lifecycle import COMPLETE_UNARCHIVED

        row = self._highlighted()
        if row is None:
            return
        if row.state != COMPLETE_UNARCHIVED:
            self.app.notify(
                f"Archive not applicable — {row.name} is not complete-unarchived.", timeout=5
            )
            return
        from otaman_cli.console.lifecycle import archive_change

        ok, msg = archive_change(self.program, row.name)
        self.app.notify(msg, severity="information" if ok else "error", timeout=8)
        if ok:
            self._load()


class ChangeDetailScreen(Screen):
    """Per-change detail (console-lifecycle-actions 1.3): stage, triage, tasks,
    gate results with block reasons, delivery badge, artifacts (per-file), and the
    actions available to the viewer on this change."""

    BINDINGS = [
        Binding("escape", "back", "Back", priority=True),
        Binding("q", "app.quit", "Quit", priority=True),
    ]

    def __init__(self, program: Program, name: str) -> None:
        super().__init__()
        self.program = program
        self.change_name = name
        self._detail: dict = {}

    def compose(self) -> ComposeResult:
        yield _header()
        yield _identity_badge_widget(self.program.root)
        yield _mode_banner(
            f"Change detail — {self.change_name}",
            "↑↓ files · esc back · q quit",
        )
        yield Static("", id="detail-summary", markup=False)
        yield ListView(id="detail-files")
        with VerticalScroll(id="detail-view-scroll"):
            yield Static("", id="detail-view", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        from otaman_cli.console.lifecycle import change_detail

        self._detail = change_detail(self.program, self.change_name)
        self.query_one("#detail-summary", Static).update(self._summary_text())
        files = self._detail.get("artifacts", [])
        lv = self.query_one("#detail-files", ListView)
        for f in files:
            lv.append(_FileItem(f))
        if files:
            self._show(files[0])

    def _summary_text(self) -> str:
        d = self._detail
        if not d:
            return f"(no detail for {self.change_name})"
        badge = "  [auto-delivery]" if d.get("delivery") == "auto" else ""
        lines = [
            f"stage: {d.get('stage') or '—'}   state: {d.get('state')}{badge}",
            f"triage: {d.get('triage') or 'untriaged'}"
            + (f" — {d['triage_note']}" if d.get("triage_note") else ""),
            f"tasks: {d.get('tasks_done', 0)}/{len(d.get('tasks', []))}"
            f"   next: {d.get('next_actor')}",
            "gates: " + " · ".join(self._gate_labels(d.get("gates", {}))),
            "actions: " + ", ".join(d.get("actions", [])),
        ]
        return "\n".join(lines)

    @staticmethod
    def _gate_labels(gates: dict) -> list[str]:
        out = []
        for g, v in gates.items():
            if v["violations"]:
                out.append(f"{g}=BLOCKED(" + "; ".join(v["violations"]) + ")")
            else:
                out.append(f"{g}=ok")
        return out

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, _FileItem):
            self._show(item.relname)

    def _show(self, relname: str) -> None:
        change_dir = self._detail.get("change_dir")
        if change_dir is None:
            return
        from otaman_cli.console.artifacts import read_artifact

        text = read_artifact(change_dir, relname) or "(empty)"
        self.query_one("#detail-view", Static).update(f"── {relname} ──\n\n{text}")

    def action_back(self) -> None:
        self.app.pop_screen()


class _AuthoredItem(ListItem):
    def __init__(self, change) -> None:
        super().__init__(Label(f"{change.name}  ({len(change.files)} files)", markup=False))
        self.change = change


class _FileItem(ListItem):
    def __init__(self, relname: str) -> None:
        super().__init__(Label(relname, markup=False))
        self.relname = relname


class ArtifactBrowserScreen(Screen):
    """IHC iteration 2 — changes at stage `authored` awaiting spec-approved review.

    Wired to SLE's stage machine: selecting a change opens ChangeReviewScreen,
    whose approve action mints the real spec-approved signal (set_stage +
    notification). The feature guard is dropped now that core's substrate exists.
    """

    BINDINGS = [
        Binding("escape", "back", "Back", priority=True),
        Binding("r", "refresh", "Refresh", priority=True),
        Binding("q", "app.quit", "Quit", priority=True),
    ]

    def __init__(self, program: Program) -> None:
        super().__init__()
        self.program = program

    def compose(self) -> ComposeResult:
        yield _header()
        yield _identity_badge_widget(self.program.root)
        yield _mode_banner(
            f"Spec review — authored changes awaiting approval · {self.program.name}",
            "enter review · r refresh · esc back · q quit",
        )
        yield ListView(id="authored-list")
        yield Footer()

    def on_mount(self) -> None:
        self._load()

    def action_refresh(self) -> None:
        self._load()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_screen_resume(self) -> None:
        self._load()  # returning from a review refreshes the authored list

    def _load(self) -> None:
        from otaman_cli.console.artifacts import list_authored_changes

        lv = self.query_one("#authored-list", ListView)
        lv.clear()
        changes = list_authored_changes(self.program)
        if changes:
            for c in changes:
                lv.append(_AuthoredItem(c))
        else:
            lv.append(ListItem(Label("No changes awaiting spec-approved review.")))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, _AuthoredItem):
            self.app.push_screen(ChangeReviewScreen(self.program, item.change))


class ChangeReviewScreen(Screen):
    """Per-file view of an authored change; approve → spec-approved / request changes."""

    BINDINGS = [
        Binding("a", "approve", "Approve (spec-approved)", priority=True),
        Binding("c", "request_changes", "Request changes", priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("q", "app.quit", "Quit", priority=True),
    ]

    def __init__(self, program: Program, change) -> None:
        super().__init__()
        self.program = program
        self.change = change

    def compose(self) -> ComposeResult:
        yield _header()
        yield _identity_badge_widget(self.program.root)
        yield _mode_banner(
            f"Spec review — {self.change.name}",
            "↑↓ files · a approve (→ spec-approved) · c request changes · esc back",
        )
        yield ListView(id="artifact-files")
        with VerticalScroll(id="artifact-view-scroll"):
            yield Static("", id="artifact-view", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        lv = self.query_one("#artifact-files", ListView)
        for f in self.change.files:
            lv.append(_FileItem(f))
        if self.change.files:
            self._show(self.change.files[0])

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, _FileItem):
            self._show(item.relname)

    def _show(self, relname: str) -> None:
        from otaman_cli.console.artifacts import read_artifact

        text = read_artifact(self.change.change_dir, relname) or "(empty)"
        self.query_one("#artifact-view", Static).update(f"── {relname} ──\n\n{text}")

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_approve(self) -> None:
        self._prompt("approve")

    def action_request_changes(self) -> None:
        self._prompt("request changes")

    def _prompt(self, verb: str) -> None:
        def _after(reason: str | None) -> None:
            if reason is None:
                return
            self._apply(verb, reason)

        self.app.push_screen(ReasonModal(verb), _after)

    def _apply(self, verb: str, reason: str) -> None:
        from otaman_cli.console import artifacts

        if verb == "approve":
            ok, msg = artifacts.advance_to_spec_approved(
                self.program, self.change.name, reason=reason
            )
        else:
            ok, msg = artifacts.request_changes(self.program, self.change.name, reason)
        self.app.notify(msg, severity="information" if ok else "error", timeout=8)
        if ok:
            self.app.pop_screen()  # ArtifactBrowserScreen.on_screen_resume refreshes


class OtamanConsole(App):
    """`otaman -i` — the human console shell."""

    TITLE = "Otaman Console"

    # The identity badge sits on an overlay layer docked top-right, so it rides
    # over the header's right corner on every screen (deploy 2.1 / Roman).
    CSS = """
    Screen { layers: base overlay; }
    #identity-badge {
        layer: overlay;
        dock: top;
        height: 1;
        content-align-horizontal: right;
        padding: 0 2 0 0;
    }
    #identity-badge.verified { color: $success; }
    #identity-badge.unverified { color: $warning; }
    """

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

    def on_key(self, event) -> None:
        # Keypress echo (Roman: "react on key pressing visually to confirm keys
        # were caught"). The lightest native affordance — flash the caught key in
        # the header sub-title. on_key sees UNBOUND keys; bound keys are echoed by
        # run_action below (their priority binding consumes the event first).
        self.sub_title = f"⌨ {event.key}"

    async def run_action(self, action, *args, **kwargs):
        # Echo BOUND keys: every binding dispatches through run_action, so flash
        # the action that fired (confirms the key was caught, even when a priority
        # binding consumed it before on_key).
        try:
            if isinstance(action, str):
                self.sub_title = f"⌨ {action.split('(')[0].removeprefix('app.')}"
        except Exception:  # noqa: BLE001 - echo is cosmetic, never break dispatch
            pass
        return await super().run_action(action, *args, **kwargs)

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
    "ReasonModal",
]
