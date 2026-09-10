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
    Tree,
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
            self.app.push_screen(HomeScreen(program))


class HomeScreen(Screen):
    """The orientation landing after program pick (console-ux-redesign 1.1 / D1).

    Aggregates existing surfaces into one glance — the human's queue, fleet and
    program status in the lifecycle canon's own vocabulary, the messages-to-human
    inbox, enabled processes, and team/connections/secrets/skills stats — before
    any navigation (S1). Jump keys prefer the action's first letter (D3). The
    feature-usage score is a RESERVED slot: an undefined number is never shown.
    Data loads off the UI thread; Home aggregates, never re-derives.
    """

    BINDINGS = [
        Binding("d", "decisions", "Decisions", priority=True),
        Binding("m", "messages", "Messages", priority=True),
        Binding("t", "tree", "Artifact tree", priority=True),
        Binding("l", "lifecycle", "Lifecycle", priority=True),
        Binding("b", "review", "Spec review", priority=True),
        Binding("a", "agents", "Agents", priority=True),
        Binding("s", "setup", "Setup", priority=True),
        Binding("r", "refresh", "Refresh", priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("q", "app.quit", "Quit", priority=True),
    ]

    def __init__(self, program: Program) -> None:
        super().__init__()
        self.program = program
        self._summary = None

    def compose(self) -> ComposeResult:
        yield _header()
        yield _identity_badge_widget(self.program.root)
        yield Static("", id="home-header", markup=False)
        yield _mode_banner(
            f"Home — orientation for {self.program.name}",
            "d decisions · m messages · t tree · l lifecycle · b review · "
            "a agents · s setup · r refresh · q quit",
        )
        with VerticalScroll(id="home-scroll"):
            yield Static("Loading…", id="home-body", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load, thread=True, exclusive=True, group="home")

    def action_refresh(self) -> None:
        self.on_mount()

    def _load(self) -> None:
        from otaman_cli.console.home import build_home_summary

        summary = build_home_summary(self.program)
        self.app.call_from_thread(self._apply_summary, summary)

    def _apply_summary(self, summary) -> None:
        self._summary = summary
        self.query_one("#home-header", Static).update(self._header_text(summary))
        self.query_one("#home-body", Static).update(self._body_text(summary))

    # -- text builders (pure, unit-tested via build_home_summary + these) -----

    def _header_text(self, summary) -> str:
        from otaman_cli.console.identity import resolve_identity

        try:
            ident = resolve_identity(self.program.root).audit_label
        except Exception:  # noqa: BLE001
            ident = "(unresolved)"
        fleet = self._fleet_line(summary)
        return "\n".join(
            [
                f"OTAMAN  ▸ program: {self.program.name}   you: {ident}",
                f"{fleet}    decisions: {summary.decisions_total} ⏳",
                f"[HOME]    policy: {summary.policy}",
            ]
        )

    @staticmethod
    def _fleet_line(summary) -> str:
        if not summary.presence_enabled:
            return "agents: presence disabled"
        if not summary.fleet:
            return "agents: none reporting"
        # adaptive: only the states that actually exist, in a stable order
        order = ["working", "waiting", "blocked", "idle"]
        keys = [k for k in order if k in summary.fleet] + [
            k for k in sorted(summary.fleet) if k not in order
        ]
        return "agents: " + " · ".join(f"{summary.fleet[k]} {k}" for k in keys)

    @staticmethod
    def _body_text(summary) -> str:
        lines: list[str] = []
        lines.append("YOUR QUEUE")
        lines.append(f"  {summary.scr_count} spec-change requests ⏳")
        lines.append(f"  {summary.outcome_count} outcome-proposals")
        lines.append(f"  {summary.ratify_blocked} ratify-blocked")
        lines.append(f"  {summary.spec_review} awaiting spec review")
        lines.append("")
        lines.append("MESSAGES TO YOU")
        lines.append(f"  {summary.inbox_count} in inbox   (m to open)")
        lines.append("")
        lines.append("PROGRAM")
        lines.append(f"  {summary.changes_total} active changes")
        if summary.triage:
            for t, n in summary.triage.items():
                if n:
                    lines.append(f"    {n} {t}")
        lines.append("")
        lines.append("PROCESSES")
        if summary.process_level:
            lines.append(f"  process level: {summary.process_level}")
        if summary.processes_enabled:
            lines.append("  enabled: " + ", ".join(summary.processes_enabled))
        else:
            lines.append("  none enabled")
        lines.append("")
        lines.append("SETUP STATS")
        lines.append(f"  team: {summary.team_humans} humans · {summary.team_agents} agents")
        lines.append(f"  connections: {summary.connections}   secrets: {summary.secrets}")
        lines.append(f"  skills: {summary.skills}")
        # feature-usage score: RESERVED — an undefined number is never displayed.
        return "\n".join(lines)

    # -- navigation (every advertised key dispatches with a visible ack) ------

    def action_decisions(self) -> None:
        self.app.push_screen(PendingListScreen(self.program))

    def action_lifecycle(self) -> None:
        self.app.push_screen(LifecycleScreen(self.program))

    def action_review(self) -> None:
        self.app.push_screen(ArtifactBrowserScreen(self.program))

    def action_messages(self) -> None:
        self.app.push_screen(InboxScreen(self.program))

    def action_agents(self) -> None:
        self.app.push_screen(AgentsScreen(self.program))

    def action_tree(self) -> None:
        self.app.push_screen(TreeScreen(self.program))

    def action_setup(self) -> None:
        self.app.push_screen(SetupScreen(self.program))

    def action_back(self) -> None:
        self.app.pop_screen()


class _SetupItem(ListItem):
    def __init__(self, verb) -> None:
        hint = "otaman " + " ".join(verb.argv)
        super().__init__(Label(f"{verb.label}   ($ {hint})", markup=False))
        self.verb = verb


class SetupScreen(Screen):
    """Setup — administration by visibly shelling out to the tested CLI verbs
    (console-ux-redesign wave 2 / D4 / S7). Read-first menu; enter runs the verb
    against the picked program and surfaces its result."""

    BINDINGS = [
        Binding("a", "add_project", "Add project", priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("q", "app.quit", "Quit", priority=True),
    ]

    def __init__(self, program: Program) -> None:
        super().__init__()
        self.program = program

    def compose(self) -> ComposeResult:
        yield _header()
        yield _identity_badge_widget(self.program.root)
        yield _mode_banner(
            f"Setup — administration · {self.program.name}",
            "enter run · a add project · esc back · q quit  (shells out to otaman verbs)",
        )
        yield ListView(id="setup-list")
        yield Footer()

    def on_mount(self) -> None:
        from otaman_cli.console.setup import SETUP_VERBS

        lv = self.query_one("#setup-list", ListView)
        for verb in SETUP_VERBS:
            lv.append(_SetupItem(verb))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, _SetupItem):
            self.app.push_screen(SetupResultScreen(self.program, tuple(item.verb.argv)))

    def action_add_project(self) -> None:
        self.app.push_screen(AddProjectScreen(self.program))

    def action_back(self) -> None:
        self.app.pop_screen()


class AddProjectScreen(Screen):
    """Register a project without leaving the console (D4/S7): collect the repo
    path + owner, then visibly shell out `otaman project assign <path> --owner
    <agent>` and surface the result. A form over the tested verb — no new
    administration logic in the TUI."""

    BINDINGS = [
        Binding("escape", "back", "Back", priority=True),
        Binding("q", "app.quit", "Quit", priority=True),
    ]

    def __init__(self, program: Program) -> None:
        super().__init__()
        self.program = program

    def compose(self) -> ComposeResult:
        yield _header()
        yield _identity_badge_widget(self.program.root)
        yield _mode_banner(
            f"Setup — add a project · {self.program.name}",
            "type path + owner, Enter to run `otaman project assign` · esc back · q quit",
        )
        yield Static("Repo path (relative to the program, e.g. ../my-repo):", markup=False)
        yield Input(placeholder="../my-repo", id="proj-path")
        yield Static("Owner agent (e.g. backend-agent):", markup=False)
        yield Input(placeholder="backend-agent", id="proj-owner")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#proj-path", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        path = self.query_one("#proj-path", Input).value.strip()
        owner = self.query_one("#proj-owner", Input).value.strip()
        if not path:
            self.query_one("#proj-path", Input).focus()
            self.app.notify("Repo path is required.", severity="error", timeout=4)
            return
        if not owner:
            self.query_one("#proj-owner", Input).focus()
            self.app.notify("Owner agent is required.", severity="error", timeout=4)
            return
        # visible shell-out of the tested verb chain (S7 scenario)
        self.app.push_screen(
            SetupResultScreen(self.program, ("project", "assign", path, "--owner", owner))
        )

    def action_back(self) -> None:
        self.app.pop_screen()


class SetupResultScreen(Screen):
    """Runs one Setup verb and surfaces its output (visible execution, D4)."""

    BINDINGS = [
        Binding("r", "rerun", "Re-run", priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("q", "app.quit", "Quit", priority=True),
    ]

    def __init__(self, program: Program, argv: tuple[str, ...]) -> None:
        super().__init__()
        self.program = program
        self.argv = argv
        self._result = None

    def compose(self) -> ComposeResult:
        yield _header()
        yield _identity_badge_widget(self.program.root)
        yield _mode_banner(
            f"Setup — otaman {' '.join(self.argv)}",
            "r re-run · esc back · q quit",
        )
        with VerticalScroll(id="setup-result-scroll"):
            yield Static("running…", id="setup-result", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self._run()

    def action_rerun(self) -> None:
        self._run()

    def _run(self) -> None:
        self.query_one("#setup-result", Static).update(
            f"$ otaman {' '.join(self.argv)}\n\nrunning…"
        )
        self.run_worker(self._exec, thread=True, exclusive=True, group="setup")

    def _exec(self) -> None:
        from otaman_cli.console.setup import run_verb

        result = run_verb(self.program, self.argv)
        self.app.call_from_thread(self._show_result, result)

    def _show_result(self, result) -> None:
        self._result = result
        status = "OK" if result.ok else f"FAILED (rc={result.returncode})"
        self.query_one("#setup-result", Static).update(
            f"$ {result.command}   [{status}]\n\n{result.output}"
        )

    def action_back(self) -> None:
        self.app.pop_screen()


class _AgentItem(ListItem):
    def __init__(self, tasks) -> None:
        summary = (
            f"active {len(tasks.active)} · queued {len(tasks.queued)} · "
            f"blocked {len(tasks.blocked)}"
        )
        super().__init__(Label(f"{tasks.agent}   ({summary})", markup=False))
        self.tasks = tasks


class AgentsScreen(Screen):
    """Assigned tasks per agent (interactive-human-console): one row per agent
    with its open-work counts, from the program's queue files. Enter drills into
    an agent's tasks. A read view — not the captured-items/backlog concept."""

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
            f"Agents — assigned tasks · {self.program.name}",
            "enter open · r refresh · esc back · q quit",
        )
        yield ListView(id="agents-list")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load, thread=True, exclusive=True, group="agents")

    def action_refresh(self) -> None:
        self.on_mount()

    def _load(self) -> None:
        from otaman_cli.console.tasks import list_agent_tasks

        rows = list_agent_tasks(self.program)
        self.app.call_from_thread(self._populate, rows)

    def _populate(self, rows) -> None:
        lv = self.query_one("#agents-list", ListView)
        lv.clear()
        # agents with open work first, then by name
        rows = sorted(rows, key=lambda t: (t.open_count == 0, t.agent))
        if rows:
            for t in rows:
                lv.append(_AgentItem(t))
        else:
            lv.append(ListItem(Label("No agent queues found for this program.")))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, _AgentItem):
            self.app.push_screen(AgentTasksScreen(self.program, item.tasks))

    def action_back(self) -> None:
        self.app.pop_screen()


class AgentTasksScreen(Screen):
    """One agent's assigned tasks, grouped active / queued / blocked."""

    BINDINGS = [
        Binding("escape", "back", "Back", priority=True),
        Binding("q", "app.quit", "Quit", priority=True),
    ]

    def __init__(self, program: Program, tasks) -> None:
        super().__init__()
        self.program = program
        self.tasks = tasks

    def compose(self) -> ComposeResult:
        yield _header()
        yield _identity_badge_widget(self.program.root)
        yield _mode_banner(
            f"Tasks — {self.tasks.agent} · {self.program.name}",
            "esc back · q quit",
        )
        with VerticalScroll(id="agent-tasks-scroll"):
            yield Static(self._body_text(self.tasks), id="agent-tasks-body", markup=False)
        yield Footer()

    @staticmethod
    def _body_text(tasks) -> str:
        lines: list[str] = []
        for label, items in (
            ("ACTIVE", tasks.active),
            ("QUEUED", tasks.queued),
            ("BLOCKED", tasks.blocked),
        ):
            lines.append(f"{label} ({len(items)})")
            if items:
                for t in items:
                    lines.append(f"  • {t[:110]}")
            else:
                lines.append("  —")
            lines.append("")
        lines.append(f"(done: {tasks.done})")
        return "\n".join(lines)

    def action_back(self) -> None:
        self.app.pop_screen()


class _InboxItem(ListItem):
    def __init__(self, message) -> None:
        tag = message.msg_type
        sender = f"{message.from_agent}{' (human)' if message.from_human else ''}"
        super().__init__(
            Label(
                f"[{tag}] [{message.priority}] {message.subject}  —  from {sender}",
                markup=False,
            )
        )
        self.message = message


class InboxScreen(Screen):
    """Messages-to-human inbox (console-ux-redesign 1.2): bus messages addressed
    to the human — from agents OR other humans — that aren't decisions. Enter opens
    the full read view. Reuses the pending-list machinery (ListView + off-thread
    refresh)."""

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
            f"Messages to you — inbox · {self.program.name}",
            "enter open · r refresh · esc back · q quit",
        )
        yield ListView(id="inbox-list")
        yield Footer()

    def action_refresh(self) -> None:
        self._load()

    def on_screen_resume(self) -> None:
        # SINGLE load path (fires on push AND on return) — loading also from
        # on_mount double-populates the list (the #99 ArtifactBrowser race).
        self._load()

    def _load(self) -> None:
        from otaman_cli.console.inbox import list_inbox_messages

        lv = self.query_one("#inbox-list", ListView)
        lv.clear()
        messages = list_inbox_messages(self.program)
        if messages:
            for m in messages:
                lv.append(_InboxItem(m))
        else:
            lv.append(ListItem(Label("No messages to you.")))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, _InboxItem):
            self.app.push_screen(InboxMessageScreen(self.program, item.message))

    def action_back(self) -> None:
        self.app.pop_screen()


_STATUS_STYLE = {
    "Done": "green",
    "Complete": "green",
    "Approved": "green",
    "complete-unarchived": "green",
    "Discarded": "red",
    "Retired": "dim",
    "In-Progress": "yellow",
    "in-flight": "yellow",
    "Drafting": "yellow",
    "Considering": "yellow",
    "Backlog": "cyan",
}


class TreeScreen(Screen):
    """The linked artifact tree (console-ux-redesign 1.3 / D2): outcomes →
    solutions → changes as one navigable tree, priority-sorted, status-colored,
    BLOCKED naming the blocker; outcome-first where registries are enabled,
    simplified otherwise. Native ↑↓/→/← move+expand+collapse; enter opens the
    change detail; f toggles closed items (hidden by default). Loads off-thread."""

    BINDINGS = [
        Binding("f", "toggle_closed", "Show/hide closed", priority=True),
        Binding("r", "refresh", "Refresh", priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("q", "app.quit", "Quit", priority=True),
    ]

    def __init__(self, program: Program) -> None:
        super().__init__()
        self.program = program
        self._show_closed = False

    def compose(self) -> ComposeResult:
        yield _header()
        yield _identity_badge_widget(self.program.root)
        yield _mode_banner(
            f"Artifact tree · {self.program.name}",
            "↑↓ move · → expand · ← collapse · enter open · "
            "f closed · r refresh · esc back · q quit",
        )
        notice = Static("", id="tree-notice", markup=False)
        notice.display = False
        yield notice
        yield Tree("artifacts", id="artifact-tree")
        yield Footer()

    def on_mount(self) -> None:
        self._reload()

    def action_refresh(self) -> None:
        self._reload()

    def action_toggle_closed(self) -> None:
        self._show_closed = not self._show_closed
        self._reload()

    def _reload(self) -> None:
        self.run_worker(self._load, thread=True, exclusive=True, group="tree")

    def _load(self) -> None:
        from otaman_cli.console.tree import build_artifact_tree, tree_fallback_notice

        roots = build_artifact_tree(self.program, show_closed=self._show_closed)
        notice = tree_fallback_notice(self.program)
        self.app.call_from_thread(self._populate, roots, notice)

    def _populate(self, roots, notice=None) -> None:
        notice_w = self.query_one("#tree-notice", Static)
        if notice:
            from rich.text import Text

            notice_w.update(Text(f"⚠ {notice}", style="bold red"))
            notice_w.display = True
        else:
            notice_w.update("")
            notice_w.display = False
        tree = self.query_one("#artifact-tree", Tree)
        tree.clear()
        closed_hint = "showing closed" if self._show_closed else "closed hidden"
        tree.root.set_label(f"artifacts ({closed_hint})")
        tree.root.expand()
        if not roots:
            tree.root.add_leaf("(no artifacts)")
            return
        # clip rows to the tree's content width so no row overflows → ←/→ stay
        # collapse/expand instead of being hijacked into horizontal scroll.
        width = tree.size.width - 4 if tree.size.width else 0
        for r in roots:
            self._add(tree.root, r, width)

    def _add(self, parent, node, width=0) -> None:
        from rich.text import Text

        label = Text(
            node.display_label(),
            style="red" if node.blocked_by else _STATUS_STYLE.get(node.status, ""),
        )
        if width and label.cell_len > width:
            label.truncate(width, overflow="ellipsis")
        if node.children:
            branch = parent.add(label, data=node, expand=True)
            for child in node.children:
                self._add(branch, child, width)
        else:
            parent.add_leaf(label, data=node)

    def on_tree_node_selected(self, event) -> None:
        node = getattr(event.node, "data", None)
        if node is None:
            return
        if node.kind == "change":
            self.app.push_screen(ChangeDetailScreen(self.program, node.id))
        elif node.kind in ("outcome", "solution"):
            # enter opens the FULL artifact content (otaman <kind> show, in-console),
            # not a status-only popup (cofounder addendum, Roman feedback).
            self.app.push_screen(RegistryDetailScreen(self.program, node.kind, node.id))
        else:
            extra = f" — BLOCKED by {node.blocked_by}" if node.blocked_by else ""
            self.app.notify(f"{node.id}: {node.status or node.kind}{extra}", timeout=5)

    def action_back(self) -> None:
        self.app.pop_screen()


class RegistryDetailScreen(Screen):
    """Full outcome/solution artifact detail (console-ux-redesign, cofounder
    addendum): the in-console equivalent of ``otaman outcome show`` / ``otaman
    solution show``, opened by enter on an outcome/solution node — replacing the
    old status-only popup so a clipped row's full content is one keypress away."""

    BINDINGS = [
        Binding("escape", "back", "Back", priority=True),
        Binding("q", "app.quit", "Quit", priority=True),
    ]

    def __init__(self, program: Program, kind: str, node_id: str) -> None:
        super().__init__()
        self.program = program
        self.kind = kind
        self.node_id = node_id

    def compose(self) -> ComposeResult:
        yield _header()
        yield _identity_badge_widget(self.program.root)
        yield _mode_banner(
            f"{self.kind.capitalize()} detail — {self.node_id}",
            "↑↓ scroll · esc back · q quit",
        )
        with VerticalScroll(id="registry-detail-scroll"):
            yield Static("Loading…", id="registry-detail", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        from otaman_cli.console.registry_detail import node_detail_text

        text = node_detail_text(self.program, self.kind, self.node_id) or "(no detail)"
        self.query_one("#registry-detail", Static).update(text)

    def action_back(self) -> None:
        self.app.pop_screen()


class InboxMessageScreen(Screen):
    """Full read view of one inbox message (console-ux-redesign 1.2)."""

    BINDINGS = [
        Binding("escape", "back", "Back", priority=True),
        Binding("q", "app.quit", "Quit", priority=True),
    ]

    def __init__(self, program: Program, message) -> None:
        super().__init__()
        self.program = program
        self.message = message

    def compose(self) -> ComposeResult:
        yield _header()
        yield _identity_badge_widget(self.program.root)
        sender = f"{self.message.from_agent}{' (human)' if self.message.from_human else ''}"
        yield _mode_banner(
            f"Message — {self.message.subject}",
            f"from {sender} · {self.message.msg_type} · esc back · q quit",
        )
        yield Static(
            f"{self.message.subject}   —   from {sender}   ({self.message.timestamp})",
            id="inbox-msg-title",
            markup=False,
        )
        yield MarkdownViewer(
            self.message.body or "(empty message)",
            show_table_of_contents=False,
            id="inbox-msg-body",
        )
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()


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

    def _apply_decision(self, verb: str, reason: str, *, delivery: str | None = None) -> None:
        from otaman_cli.console import decision
        from otaman_cli.console.identity import resolve_identity
        from otaman_cli.console.journal import run_decision_action

        identity = resolve_identity(self.program.root)
        if verb == "approve":

            def fn():
                return decision.approve(
                    self.program, self.proposal, identity, reason=reason, delivery=delivery
                )
        else:
            verb_fn = {"reject": decision.reject, "defer": decision.defer}[verb]

            def fn():
                return verb_fn(self.program, self.proposal, identity, reason=reason)

        label = f"{verb}-auto" if delivery == "auto" else verb
        ok, _ = run_decision_action(self.app, action=label, target=self.proposal.stem, fn=fn)
        if ok:
            self.app.pop_screen()  # PendingListScreen.on_screen_resume refreshes

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
            from otaman_cli.console.journal import run_decision_action
            from otaman_cli.lifecycle import send_nudge

            ok, _ = run_decision_action(
                self.app,
                action="nudge",
                target=row.name,
                fn=lambda: send_nudge(self.program, row, note=note),
            )
            if ok:
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
            from otaman_cli.console.journal import run_decision_action
            from otaman_cli.console.lifecycle import ratify_change

            by = resolve_identity(self.program.root).operator
            ok, _ = run_decision_action(
                self.app,
                action="ratify",
                target=row.name,
                fn=lambda: ratify_change(self.program, row.name, by=by, reason=reason),
            )
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
        from otaman_cli.console.journal import run_decision_action
        from otaman_cli.console.lifecycle import archive_change

        ok, _ = run_decision_action(
            self.app,
            action="archive",
            target=row.name,
            fn=lambda: archive_change(self.program, row.name),
        )
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
        self._meta = None

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
        from otaman_cli.console.metadata import change_metadata

        self._detail = change_detail(self.program, self.change_name)
        self._meta = change_metadata(self.program, self.change_name)
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
        # S9/S10 metadata (creator/when/priority/deadline slot/pm-sync id).
        meta = getattr(self, "_meta", None)
        if meta is not None:
            from otaman_cli.console.metadata import metadata_lines

            lines.extend(metadata_lines(meta))
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

    def action_refresh(self) -> None:
        self._load()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_screen_resume(self) -> None:
        # SINGLE load path: ScreenResume fires on the initial push AND on every
        # return from a review, so this covers both. Loading ALSO from on_mount
        # double-populated the list on first show — ListView.append adds children
        # synchronously while clear() is deferred, so two loads in one burst
        # briefly stacked to 2 items (the intermittent Windows `assert 2 == 1`
        # in test_browser_lists_authored_changes). One trigger, no race.
        self._load()

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
        from otaman_cli.console.journal import run_decision_action

        if verb == "approve":

            def fn():
                return artifacts.advance_to_spec_approved(
                    self.program, self.change.name, reason=reason
                )

            action = "approve (spec-approved)"
        else:

            def fn():
                return artifacts.request_changes(self.program, self.change.name, reason)

            action = "request-changes"
        # The silent-loss net: an exception in fn (e.g. read_openspec on a
        # .openspec.yaml being rewritten by a concurrent merge — the incident
        # shape) is caught, journaled, and shown LOUDLY instead of vanishing.
        ok, _ = run_decision_action(self.app, action=action, target=self.change.name, fn=fn)
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

    def __init__(self, programs: list[Program], *, search_root=None, log_dir=None) -> None:
        super().__init__()
        self._programs = programs
        self._search_root = search_root
        self._log_dir = log_dir
        # Per-session observability log (silent-approval-loss fix). Opened in
        # on_mount so a filesystem hiccup degrades a live app, not construction.
        self.session_log = None

    def _open_session_log(self) -> None:
        # Full-session trace to ~/.otaman/console-logs/<ts>.log: no tmux on
        # Roman's seat means no scrollback, so the file IS the record. Identity
        # is best-effort (first program's roster context, else the picker root).
        from otaman_cli.console.identity import resolve_identity
        from otaman_cli.console.journal import ConsoleLog

        root = self._programs[0].root if self._programs else _NO_PROGRAM_ROOT
        try:
            ident = resolve_identity(root).audit_label
        except Exception:  # noqa: BLE001 - identity is a label, never block logging
            ident = ""
        self.session_log = ConsoleLog.open(
            log_dir=self._log_dir,
            identity=ident,
            programs=[p.name for p in self._programs],
        )

    def on_mount(self) -> None:
        self._open_session_log()
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

    def push_screen(self, screen, *args, **kwargs):
        # Journal every screen transition into the session log (spec-agent
        # addendum 3: the full-session trace). Cosmetic-only — never block nav.
        log = getattr(self, "session_log", None)
        if log is not None:
            name = screen if isinstance(screen, str) else type(screen).__name__
            log.event("screen", to=name)
        return super().push_screen(screen, *args, **kwargs)

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
    "AddProjectScreen",
    "AgentTasksScreen",
    "AgentsScreen",
    "HomeScreen",
    "InboxMessageScreen",
    "InboxScreen",
    "OtamanConsole",
    "PendingListScreen",
    "ProgramPickerScreen",
    "ProposalScreen",
    "ReasonModal",
    "RegistryDetailScreen",
    "SetupResultScreen",
    "SetupScreen",
    "TreeScreen",
]
