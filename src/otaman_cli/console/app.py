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
from textual.containers import Horizontal, VerticalScroll
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

from otaman_cli.console.bus import (
    Program,
    Proposal,
    discover_programs,
    list_pending_proposals,
    read_body,
)

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


def queue_row_label(proposal: Proposal) -> str:
    """One merged-queue row's text (2.1).

    A pure function so the row's wording is testable without mounting a widget —
    a ListItem's children do not exist until it is mounted, which made the
    rendered text unreachable from a unit test.
    """
    tag = _TYPE_TAG.get(proposal.msg_type, proposal.msg_type or "msg")
    mark = "* " if proposal.is_decision else "  "
    keys = "   [a/A/x/d]" if proposal.is_decision else ""
    return (
        f"{mark}[{tag}] [{proposal.priority}] {proposal.subject}"
        f"  —  from {proposal.from_agent}{keys}"
    )


class _ProposalItem(ListItem):
    """One row of the human queue — a decision or a plain message (2.1).

    A DECISION row is marked so the action keys are discoverable on the row
    itself rather than only after opening it: the merged list carries both
    kinds, so the row has to say which it is.
    """

    def __init__(self, proposal: Proposal) -> None:
        super().__init__(Label(queue_row_label(proposal), markup=False))
        self.proposal = proposal


def invalidate_read_caches() -> None:
    """Drop the memoized YAML and bus-frontmatter reads.

    Every `r` must really re-read. The caches key on (path, mtime, size), so a
    changed file already re-parses on its own; this exists for the case the key
    cannot see — a filesystem with coarse mtime granularity rewriting a file to
    the same size — and because "refresh" that served a cached answer would
    make the key a lie. Called by EVERY screen's refresh action; the
    binding-conformance test asserts that.
    """
    from otaman_cli.console.bus_index import clear_cache as clear_bus_cache
    from otaman_cli.yaml_fast import clear_cache as clear_yaml_cache

    clear_yaml_cache()
    clear_bus_cache()


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

    #: The ADVERTISED top level is the budget (gate 6.1 F1). The surface-budget
    #: requirement says the top level SHALL be Home, Artifacts, Messages and
    #: Setup — and the presented strip IS the top level as far as a human is
    #: concerned. After P0-P4 the strip still read "d Decisions · m Messages ·
    #: t Artifact tree · l Lifecycle · b Spec review · a Agents · s Setup",
    #: so from Roman's seat nothing had shrunk.
    #:
    #: D8 invokes JTBD-108's discipline, and that pattern is explicit: retired
    #: entries become HIDDEN aliases. `d`/`l`/`b` therefore stay BOUND and keep
    #: landing on their new homes with the one-line notice — behaviour
    #: unchanged, advertisement gone (`show=False`).
    #:
    #: `a` (Agents) stays advertised deliberately: its door placement was never
    #: ruled, spec-agent has put that question to Roman, and folding it here on
    #: my own judgement would decide an open question by implementation.
    BINDINGS = [
        Binding("t", "tree", "Artifacts", priority=True),
        Binding("m", "messages", "Messages", priority=True),
        Binding("s", "setup", "Setup", priority=True),
        Binding("a", "agents", "Agents", priority=True),
        Binding("r", "refresh", "Refresh", priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("q", "app.quit", "Quit", priority=True),
        # Hidden aliases — bound, dispatching, and not in the strip.
        Binding("d", "decisions", "Decisions", priority=True, show=False),
        Binding("l", "lifecycle", "Lifecycle", priority=True, show=False),
        Binding("b", "review", "Spec review", priority=True, show=False),
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
            "t artifacts · m messages · s setup · a agents · r refresh · q quit",
        )
        with VerticalScroll(id="home-scroll"):
            yield Static("Loading…", id="home-body", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load, thread=True, exclusive=True, group="home")

    def action_refresh(self) -> None:
        invalidate_read_caches()
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
        # STALE first: it is the entry that changes what the reader believes.
        order = ["STALE", "working", "waiting", "blocked", "idle"]
        keys = [k for k in order if k in summary.fleet] + [
            k for k in sorted(summary.fleet) if k not in order
        ]
        return "agents: " + " · ".join(f"{summary.fleet[k]} {k}" for k in keys)

    @staticmethod
    def _body_text(summary) -> str:
        lines: list[str] = []
        # 4.1 / D6 — the queue is filtered by the ACTING HAT; navigation is not.
        # A solo operator (unresolved or many hats) sees the union, because an
        # empty queue would hide a human's own work from them.
        from otaman_cli.console.home import queue_label

        counts = {
            "scr": summary.scr_count,
            "outcome": summary.outcome_count,
            "cost_acceptance": summary.cost_acceptance,
            "value_decisions": summary.value_decisions,
            "solution_choices": summary.solution_choices,
            "spec_review": summary.spec_review,
            "ratify_blocked": summary.ratify_blocked,
            "assigned_tasks": summary.assigned_tasks,
        }
        hat_label = ", ".join(sorted(summary.hats)) if summary.hats else "all hats (unresolved)"
        lines.append(f"YOUR QUEUE — {hat_label}")
        shown = [r for r in (summary.queue_rows or ()) if counts.get(r)]
        if shown:
            for row in shown:
                lines.append(f"  {counts[row]} {queue_label(row)}")
        else:
            lines.append("  nothing waiting on you")
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
        skills_line = f"  skills: {summary.skills}"
        if getattr(summary, "legacy_skills", 0):
            # Names the fix, because a count of 0 next to a populated-looking
            # platform.yaml is exactly what sends someone hunting.
            skills_line += (
                f"   ({summary.legacy_skills} declared under the retired top-level "
                "`skills:` — inert; move to program.processes.skills)"
            )
        lines.append(skills_line)
        # feature-usage score: RESERVED — an undefined number is never displayed.
        return "\n".join(lines)

    # -- navigation (every advertised key dispatches with a visible ack) ------

    # console-ia-consolidation 2.3 / D8 — ALIASES, not removals. `d`, `b` and
    # `l` keep working, land on their new home, and say so in one line. Removal
    # waits for a clean usage signal and never rides in the same release as the
    # move: Roman presses these every day, and a key that silently stops working
    # is worse than one that moves.
    def _alias(self, screen, *, key: str, moved_to: str) -> None:
        self.app.push_screen(screen)
        self.app.notify(f"`{key}` now lands on {moved_to}.", timeout=6)

    def action_decisions(self) -> None:
        # Decisions merged INTO Messages (2.1) — same rows, same action keys.
        self._alias(InboxScreen(self.program), key="d", moved_to="Messages (m)")

    def action_lifecycle(self) -> None:
        # 3.1 — the lifecycle table is a LENS of Artifacts now; `l` lands there
        # with the same columns. Alias, not removal (D8).
        from otaman_cli.console.tree import LENS_LIFECYCLE

        self._alias(
            TreeScreen(self.program, lens=LENS_LIFECYCLE),
            key="l",
            moved_to="Artifacts (t) in the lifecycle lens — L cycles lenses",
        )

    def action_review(self) -> None:
        # Spec review is an ACTION on an authored change now (2.2); `b` lands on
        # Artifacts filtered to exactly those rows.
        self._alias(
            TreeScreen(self.program, authored_only=True),
            key="b",
            moved_to="Artifacts (t), filtered to authored changes",
        )

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
        from otaman_cli.console.setup import setup_item_label

        super().__init__(Label(setup_item_label(verb), markup=False))
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
        invalidate_read_caches()
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


class _DecisionActions:
    """approve / approve-auto / reject / defer, shared by every screen that can
    decide (console-ia-consolidation 2.1).

    Extracted from ProposalScreen so the merged Messages list acts through the
    SAME writes rather than a second copy — "the same writes as today" is the
    task's wording, and a second copy is how the five blocked-entry parsers
    happened. Subclasses supply `self.program`, `_decision_target()` (the
    Proposal to act on, or None) and `_after_decision()`.
    """

    def _decision_target(self):  # pragma: no cover - overridden
        raise NotImplementedError

    def _after_decision(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def _apply_decision(
        self, verb: str, reason: str, *, target=None, delivery: str | None = None
    ) -> None:
        """Run *verb* against *target* — which the CALLER captured.

        `target` is passed in rather than re-derived, and that is the whole of
        gate 6.1 F2. This re-read `self._decision_target()` here, AFTER the
        reason modal's async round-trip. On the merged Messages list the target
        comes from the ListView highlight, and dismissing the modal fires
        `on_screen_resume` → `_load()`, which rebuilds the list and clears that
        highlight BEFORE this callback runs. So the target was None by the time
        it was read, the guard below returned, and the approval evaporated:
        modal closed, no journal line, no notification, nothing written. Roman
        lost two real approvals to it before it was caught.

        The old ProposalScreen path never hit this because its target is a fixed
        attribute, not a live selection — the merged surface introduced the
        re-read. A decision belongs to the row the human was looking at when
        they pressed the key, so it is captured then and carried through.
        """
        if target is None:
            target = self._decision_target()
        if target is None:
            # LOUDLY, never a bare return. A decision that cannot find its
            # target is exactly the silent loss this whole path guards against,
            # so it is journaled and shown even though it "does nothing".
            log = getattr(self.app, "session_log", None)
            if log is not None:
                log.event("action-target-lost", action=verb, target="?")
            self.app.notify(
                f"{verb} did NOT run — the selected row was lost before the "
                "decision was applied. Nothing was written; please retry.",
                severity="error",
                timeout=10,
            )
            return
        from otaman_cli.console import decision
        from otaman_cli.console.identity import resolve_identity
        from otaman_cli.console.journal import run_decision_action

        identity = resolve_identity(self.program.root)
        if verb == "approve":

            def fn():
                return decision.approve(
                    self.program, target, identity, reason=reason, delivery=delivery
                )
        else:
            verb_fn = {"reject": decision.reject, "defer": decision.defer}[verb]

            def fn():
                return verb_fn(self.program, target, identity, reason=reason)

        label = f"{verb}-auto" if delivery == "auto" else verb
        ok, _ = run_decision_action(self.app, action=label, target=target.stem, fn=fn)
        if ok:
            self._after_decision()

    def _prompt_and_decide(self, verb: str, *, delivery: str | None = None) -> None:
        target = self._decision_target()
        if target is None:
            # A plain message has no decision to make; say so rather than
            # swallowing the keypress (the dead-end rule from P0's 1.1).
            self.app.notify(
                "Not a decision row — approve/reject/defer apply to "
                "spec-change-requests and outcome-proposals.",
                timeout=5,
            )
            return

        # Captured HERE, while the human's selection is still the truth. Do not
        # re-derive it inside the callback — see `_apply_decision` (gate 6.1 F2).
        decided = target

        def _after(reason: str | None) -> None:
            if reason is not None:  # None = cancelled
                self._apply_decision(verb, reason, target=decided, delivery=delivery)

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


class InboxScreen(_DecisionActions, Screen):
    """Messages — the ONE list of everything addressed to the human (2.1).

    Decisions used to live on a separate screen, so an item's TYPE decided which
    of two lists could see it and the human had to remember which. Now one list
    carries both: decision rows are marked `*` with their keys shown, and the
    action keys act through the same writes the standalone proposal screen uses
    (`_DecisionActions`). Enter opens the full read view either way.
    """

    BINDINGS = [
        Binding("a", "approve", "Approve", priority=True),
        Binding("A", "approve_auto", "Approve (auto-delivery)", priority=True),
        Binding("x", "reject", "Reject", priority=True),
        Binding("d", "defer", "Defer", priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("r", "refresh", "Refresh", priority=True),
        Binding("q", "app.quit", "Quit", priority=True),
    ]

    def __init__(self, program: Program, *, event_source=None) -> None:
        super().__init__()
        self.program = program
        # LIVE refresh. The event-source interface (polling now; fswatch/NATS
        # later, with no console rework) was wired only to PendingListScreen —
        # the screen 2.1 replaced with this one — and the wiring did not come
        # across. Measured: with Messages open, a decision landing on the bus
        # stayed invisible until the human pressed `r`. On the surface Roman
        # watches for things that need him, that is the surface being wrong
        # rather than merely stale.
        self._source = event_source
        self._own_source = event_source is None

    def compose(self) -> ComposeResult:
        yield _header()
        yield _identity_badge_widget(self.program.root)
        yield _mode_banner(
            f"Messages to you · {self.program.name}",
            "enter open · a approve · A approve-auto · x reject · d defer · "
            "r refresh · esc back · q quit",
        )
        yield ListView(id="inbox-list")
        yield Footer()

    def action_refresh(self) -> None:
        invalidate_read_caches()
        self._load()

    def on_mount(self) -> None:
        if self._source is None:
            from otaman_cli.console.bus import list_human_queue
            from otaman_cli.console.events import make_event_source

            # Watch the SAME set this screen renders — the merged queue, not
            # just the decisions in it.
            self._source = make_event_source(self.program, lister=list_human_queue)
        # The provider owns its trigger and calls back when the pending set may
        # have moved; marshal onto the UI thread, then reuse the SAME load path.
        self._source.start(lambda: self.app.call_from_thread(self._load))

    def on_unmount(self) -> None:
        # Only stop a source we created — an injected one belongs to the caller
        # (that is what makes the screen testable without a real poll thread).
        if self._source is not None and self._own_source:
            self._source.stop()

    def on_screen_resume(self) -> None:
        # SINGLE load path (fires on push AND on return) — loading also from
        # on_mount double-populates the list (the #99 ArtifactBrowser race).
        self._load()

    def _load(self) -> None:
        from otaman_cli.console.bus import list_human_queue

        lv = self.query_one("#inbox-list", ListView)
        lv.clear()
        rows = list_human_queue(self.program)
        if rows:
            decisions = sum(1 for r in rows if r.is_decision)
            for r in rows:
                lv.append(_ProposalItem(r))
            # The count says what needs ACTING on, not just what arrived — a
            # single list still has to distinguish those.
            self.query_one("#mode-banner", Static).update(
                f"Messages to you · {self.program.name} — "
                f"{decisions} awaiting your decision, {len(rows) - decisions} to read\n"
                "enter open · a approve · A approve-auto · x reject · d defer · "
                "r refresh · esc back · q quit"
            )
        else:
            lv.append(ListItem(Label("Nothing addressed to you.")))

    def _highlighted_proposal(self):
        lv = self.query_one("#inbox-list", ListView)
        item = lv.highlighted_child
        return getattr(item, "proposal", None)

    def _decision_target(self):
        row = self._highlighted_proposal()
        return row if row is not None and row.is_decision else None

    def _after_decision(self) -> None:
        self._load()  # stay on the list; the decided row disappears

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        row = getattr(event.item, "proposal", None)
        if row is None:
            return
        # A decision opens the decide view; a plain message opens the read view.
        if row.is_decision:
            self.app.push_screen(ProposalScreen(self.program, row))
        else:
            self.app.push_screen(InboxMessageScreen(self.program, row))

    def action_back(self) -> None:
        self.app.pop_screen()


def _authored_change_roots(program, roots):
    """*roots* reduced to the authored changes awaiting spec-approval (2.2).

    The set comes from `artifacts.list_authored_changes` — the same one the old
    standalone browser listed and `advance_to_spec_approved` accepts — so the
    filtered view and the action agree by construction. Change nodes are lifted
    to the top level: a human reviewing authored specs wants the changes, not
    the outcomes they hang under.
    """
    try:
        from otaman_cli.console import artifacts

        names = {c.name for c in artifacts.list_authored_changes(program)}
    except Exception:  # noqa: BLE001 - unresolvable specs repo → nothing to filter to
        return []
    if not names:
        return []
    found, seen = [], set()

    def walk(nodes):
        for n in nodes:
            if n.kind == "change" and n.id in names and n.id not in seen:
                seen.add(n.id)
                found.append(n)
            walk(n.children)

    walk(roots)
    return found


class TreeScreen(Screen):
    """The linked artifact tree (console-ux-redesign 1.3 / D2): outcomes →
    solutions → changes as one navigable tree, priority-sorted, status-colored,
    BLOCKED naming the blocker; outcome-first where registries are enabled,
    simplified otherwise. Native ↑↓/→/← move+expand+collapse; enter opens the
    change detail; f toggles closed items (hidden by default). Loads off-thread."""

    BINDINGS = [
        Binding("f", "toggle_closed", "Show/hide closed", priority=True),
        Binding("p", "toggle_panel", "Read panel", priority=True),
        # 2.2 — spec review is an ACTION on an authored change row, not a screen.
        Binding("v", "review", "Spec-approve (authored)", priority=True),
        # 3.1 — one key cycles value → capability → lifecycle.
        Binding("L", "cycle_lens", "Switch lens", priority=True),
        Binding("r", "refresh", "Refresh", priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("q", "app.quit", "Quit", priority=True),
    ]

    def __init__(
        self, program: Program, *, authored_only: bool = False, lens: str | None = None
    ) -> None:
        super().__init__()
        from otaman_cli.console.tree import LENS_VALUE

        self.program = program
        self._show_closed = False
        self._panel_open = False
        # 3.1 — Artifacts is ONE door with three lenses over the same objects.
        self._lens = lens or LENS_VALUE
        self._rows: list = []  # lifecycle-lens rows
        # 2.2 — `b`'s new home: Artifacts filtered to authored changes, which is
        # what the standalone spec-review browser used to be a separate screen for.
        self._authored_only = authored_only

    def compose(self) -> ComposeResult:
        yield _header()
        yield _identity_badge_widget(self.program.root)
        yield _mode_banner(
            f"Artifact tree · {self.program.name}",
            "↑↓ move · → expand · ← collapse · enter open · v spec-approve · "
            "f closed · p read · r refresh · esc back · q quit",
        )
        notice = Static("", id="tree-notice", markup=False)
        notice.display = False
        yield notice
        with Horizontal(id="tree-row"):
            yield Tree("artifacts", id="artifact-tree")
            with VerticalScroll(id="tree-side"):
                yield Static("", id="tree-side-body", markup=False)
        # The lifecycle lens is a TABLE, not a tree — same door, same objects,
        # different arrangement (D2). Both widgets exist; one is shown.
        table = DataTable(id="artifact-lifecycle", zebra_stripes=True, cursor_type="row")
        table.display = False
        yield table
        yield Footer()

    def on_mount(self) -> None:
        # the `p` side panel starts hidden; a right-side read-in-place pane (1.4)
        side = self.query_one("#tree-side")
        side.styles.width = "45%"
        side.display = False
        # Columns come from the SHARED set (3.1) so the lens and the standalone
        # screen cannot disagree about them.
        from otaman_cli.console.lifecycle import LIFECYCLE_COLUMNS

        self.query_one("#artifact-lifecycle", DataTable).add_columns(*LIFECYCLE_COLUMNS)
        self._apply_lens()
        self._reload()

    def action_cycle_lens(self) -> None:
        """Cycle value → capability → lifecycle (3.1)."""
        from otaman_cli.console.tree import next_lens

        self._lens = next_lens(self._lens)
        self._apply_lens()
        self._reload()

    def _apply_lens(self) -> None:
        """Show the widget this lens renders into, and say which lens is active."""
        from otaman_cli.console.tree import LENS_LABEL, LENS_LIFECYCLE

        is_table = self._lens == LENS_LIFECYCLE
        self.query_one("#artifact-lifecycle", DataTable).display = is_table
        self.query_one("#tree-row").display = not is_table
        label = LENS_LABEL.get(self._lens, self._lens)
        self.query_one("#mode-banner", Static).update(
            f"Artifacts · {self.program.name} — {label} lens\n"
            "↑↓ move · enter open · L lens · v spec-approve · f closed · p read · "
            "r refresh · esc back · q quit"
        )

    def action_refresh(self) -> None:
        invalidate_read_caches()
        self._reload()

    def action_toggle_closed(self) -> None:
        self._show_closed = not self._show_closed
        self._reload()

    def action_toggle_panel(self) -> None:
        """`p` toggles the right-side read-in-place panel (tree-view-polish 1.4)."""
        self._panel_open = not self._panel_open
        self.query_one("#tree-side").display = self._panel_open
        if self._panel_open:
            tree = self.query_one("#artifact-tree", Tree)
            self._update_panel(getattr(tree.cursor_node, "data", None))

    def on_tree_node_highlighted(self, event) -> None:
        if self._panel_open:
            self._update_panel(getattr(event.node, "data", None))

    def _update_panel(self, node) -> None:
        body = self.query_one("#tree-side-body", Static)
        if node is None:
            body.update("(nothing selected)")
            return
        if node.kind in ("outcome", "solution"):
            from otaman_cli.console.registry_detail import node_detail_text, role_emphasis

            text = node_detail_text(
                self.program, node.kind, node.id, emphasis=role_emphasis(self.program)
            )
            body.update(text or f"{node.id}")
        else:
            extra = f"\n  blocked by {node.blocked_by}" if node.blocked_by else ""
            body.update(f"{node.id}\n  status: {node.status or node.kind}{extra}")

    def _reload(self) -> None:
        self.run_worker(self._load, thread=True, exclusive=True, group="tree")

    def _load(self) -> None:
        from otaman_cli.console.tree import (
            LENS_LIFECYCLE,
            build_artifact_tree,
            tree_fallback_notice,
        )

        if self._lens == LENS_LIFECYCLE:
            from otaman_cli.console.lifecycle import derive_lifecycle_rows

            self.app.call_from_thread(self._paint_lifecycle, derive_lifecycle_rows(self.program))
            return

        roots = build_artifact_tree(self.program, show_closed=self._show_closed, lens=self._lens)
        if self._authored_only:
            roots = _authored_change_roots(self.program, roots)
        notice = tree_fallback_notice(self.program)
        self.app.call_from_thread(self._populate, roots, notice)

    def _paint_lifecycle(self, rows: list) -> None:
        """The lifecycle lens — the Roman-defined columns, rendered by the SAME
        cell mapping the standalone screen uses (3.1)."""
        from otaman_cli.console.lifecycle import lifecycle_row_cells

        self._rows = rows
        table = self.query_one("#artifact-lifecycle", DataTable)
        table.clear()
        for r in rows:
            table.add_row(*lifecycle_row_cells(r))

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

        # per-column colors from the one shared palette (tree-view-polish 1.2);
        # short lowercase value words, no enum reprs (1.1).
        label = Text()
        for text, style in node.row_segments():
            label.append(text, style=style or None)
        if width and label.cell_len > width:
            label.truncate(width, overflow="ellipsis")
        # A reference is always a LEAF: it navigates on activation and never
        # expands in place (D4), so it cannot grow a second copy of a subtree.
        if node.children and node.kind != "reference":
            branch = parent.add(label, data=node, expand=not node.collapsed)
            for child in node.children:
                self._add(branch, child, width)
        else:
            parent.add_leaf(label, data=node)

    def action_review(self) -> None:
        """Spec-approve the highlighted authored change (2.2).

        Reuses `artifacts.advance_to_spec_approved` — the same function the
        lifecycle key, `otaman spec approve` and the b-screen all run — so the
        transition validation, approver hat and audit record are identical from
        every entry point. A row that is not an authored change is refused by
        name rather than ignored (the dead-end rule).
        """
        node = self._cursor_node()
        if node is None:
            return
        if node.kind != "change" or not self._is_authored(node.id):
            self.app.notify(
                f"{node.id} is not an authored change awaiting spec-approval — "
                "nothing to review here.",
                timeout=6,
            )
            return

        def _after(reason: str | None) -> None:
            if reason is None:
                return
            from otaman_cli.console import artifacts
            from otaman_cli.console.journal import run_decision_action

            ok, _ = run_decision_action(
                self.app,
                action="spec-approve",
                target=node.id,
                fn=lambda: artifacts.advance_to_spec_approved(self.program, node.id, reason=reason),
            )
            if ok:
                self._reload()

        self.app.push_screen(ReasonModal("spec-approve"), _after)

    def _navigate_to(self, kind: str, node_id: str) -> None:
        """Open the referenced node's own view (D4: references navigate).

        Deliberately opens the TARGET's detail rather than scrolling the tree to
        it: the reference exists because the target lives under a different
        parent, so "go there" is a view change, not a cursor move.
        """
        if not kind or not node_id:
            return
        if kind == "change":
            self.app.push_screen(ChangeDetailScreen(self.program, node_id))
        elif kind in ("outcome", "solution"):
            self.app.push_screen(RegistryDetailScreen(self.program, kind, node_id))
        else:
            self.app.notify(f"{node_id} ({kind}) has no detail view yet.", timeout=5)

    def _capability_detail(self, node) -> None:
        """A capability root's own read-out: its requirement headings (3.2)."""
        from otaman_cli.console.capability import _specs_dirs

        specs_dir, _ = _specs_dirs(self.program)
        spec_file = (specs_dir / node.id / "spec.md") if specs_dir else None
        if spec_file is None or not spec_file.is_file():
            self.app.notify(f"No spec file for capability {node.id}.", timeout=5)
            return
        self.app.push_screen(CapabilityDetailScreen(self.program, node.id, spec_file))

    def _cursor_node(self):
        """The highlighted tree row's data, or None — a seam mirroring
        LifecycleScreen's `_highlighted`, so actions are testable without
        driving Textual's cursor."""
        tree = self.query_one("#artifact-tree", Tree)
        return getattr(tree.cursor_node, "data", None)

    def _is_authored(self, change_name: str) -> bool:
        try:
            from otaman_cli.console import artifacts

            return any(c.name == change_name for c in artifacts.list_authored_changes(self.program))
        except Exception:  # noqa: BLE001 - unresolvable specs repo → not reviewable
            return False

    def on_tree_node_selected(self, event) -> None:
        node = getattr(event.node, "data", None)
        if node is None:
            return
        # A reference NAVIGATES to its target (D4) rather than opening itself.
        if node.kind == "reference":
            self._navigate_to(node.ref_kind, node.ref_id)
            return
        if node.kind == "capability":
            self._capability_detail(node)
            return
        if node.kind == "disposition":
            # A ledger row minted nothing — there is no artifact to open, and
            # saying so is better than a dead keypress.
            self.app.notify(f"{node.id} — no change was minted from this.", timeout=6)
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
        Binding("a", "accept_cost", "Accept cost", priority=True),
        Binding("c", "choose", "Choose", priority=True),
        Binding("d", "discard", "Discard", priority=True),
        Binding("escape", "back", "Back", priority=True),
        Binding("q", "app.quit", "Quit", priority=True),
    ]

    def __init__(self, program: Program, kind: str, node_id: str) -> None:
        super().__init__()
        self.program = program
        self.kind = kind
        self.node_id = node_id
        # (outcome_id, solution_id) for the accept-cost verb, or None when not offerable
        self._accept_args: tuple[str, str] | None = None
        # team-mode 2.4b — solution-node decision affordances
        self._choose_args: tuple[str, str] | None = None  # (outcome_id, solution_id)
        self._discard_ok: bool = False

    def compose(self) -> ComposeResult:
        yield _header()
        yield _identity_badge_widget(self.program.root)
        yield _mode_banner(
            f"{self.kind.capitalize()} detail — {self.node_id}",
            "↑↓ scroll · a accept-cost · c choose · d discard · esc back · q quit",
        )
        with VerticalScroll(id="registry-detail-scroll"):
            yield Static("Loading…", id="registry-detail", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self._reload()

    def _reload(self) -> None:
        from otaman_cli.console.registry_detail import node_detail_text, role_emphasis

        # 4.2 — the acting hat EMPHASISES its field groups; nothing is hidden.
        # (Was: a `scope` that hid the others, which made a CTO see less than an
        # unresolved identity.)
        emphasis = role_emphasis(self.program)
        text = (
            node_detail_text(self.program, self.kind, self.node_id, emphasis=emphasis)
            or "(no detail)"
        )
        # team-mode 2.4a (+ follow-up) — surface the one-key accept-cost affordance.
        # Outcome node: auto-derive the single clear solution. Solution node: accept
        # THIS solution's cost (dissolves the multi-candidate gap, no choose step).
        self._accept_args = None
        if self.kind == "outcome":
            from otaman_cli.console.accept_cost import accept_cost_candidate

            sol, note = accept_cost_candidate(self.program, self.node_id)
            if sol:
                self._accept_args = (self.node_id, sol)
                text += f"\n\n▶ ACCEPT-COST available (press a) — {note}"
            elif "solution node" in note:  # multi-candidate → point at the solutions
                text += f"\n\n▷ {note}"
        elif self.kind == "solution":
            from otaman_cli.console.accept_cost import solution_accept_candidate
            from otaman_cli.console.decisions import choose_candidate, discard_candidate

            outcome_id, note = solution_accept_candidate(self.program, self.node_id)
            if outcome_id:
                self._accept_args = (outcome_id, self.node_id)
                text += f"\n\n▶ ACCEPT-COST available (press a) — {note}"
            # team-mode 2.4b — CHOOSE / DISCARD on the solution node (CTO hat)
            self._choose_args = None
            c_outcome, c_note = choose_candidate(self.program, self.node_id)
            if c_outcome:
                self._choose_args = (c_outcome, self.node_id)
                text += f"\n\n▶ CHOOSE available (press c) — {c_note}"
            self._discard_ok, d_note = discard_candidate(self.program, self.node_id)
            if self._discard_ok:
                text += f"\n\n▶ DISCARD available (press d) — {d_note}"
        self.query_one("#registry-detail", Static).update(text)

    def _hat_advisory_notify(self) -> None:
        from otaman_cli.console.decisions import acting_decision_hat

        holds, operator = acting_decision_hat(self.program)
        if not holds:  # advisory at Mode 1 — warn, then proceed
            self.app.notify(
                f"advisory: {operator or 'you'} does not hold the CTO/founder hat",
                severity="warning",
                timeout=5,
            )

    def _notify_result(self, verb: str, result) -> None:
        self.app.notify(
            (f"{verb}: " if result.ok else f"{verb} failed: ") + result.output,
            severity="information" if result.ok else "error",
            timeout=6,
        )
        self._reload()

    def action_choose(self) -> None:
        if self.kind != "solution" or not self._choose_args:
            self.app.notify("choose not available for this node", timeout=4)
            return
        from otaman_cli.console.decisions import run_choose

        self._hat_advisory_notify()
        outcome_id, solution_id = self._choose_args
        self._notify_result("chose solution", run_choose(self.program, outcome_id, solution_id))

    def action_discard(self) -> None:
        if self.kind != "solution" or not self._discard_ok:
            self.app.notify("discard not available for this node", timeout=4)
            return
        from otaman_cli.console.decisions import run_discard

        def _after(reason: str | None) -> None:
            if not reason:  # cancelled or empty → discard requires a reason
                return
            self._hat_advisory_notify()
            result = run_discard(self.program, self.node_id, reason)
            self._notify_result("discarded solution", result)

        self.app.push_screen(ReasonModal("discard"), _after)

    def action_accept_cost(self) -> None:
        if not self._accept_args:
            self.app.notify("accept-cost not available for this node", timeout=4)
            return
        from otaman_cli.console.accept_cost import acting_hat_holds, run_accept_cost

        holds, operator = acting_hat_holds(self.program)
        if not holds:  # advisory at Mode 1 — warn, then proceed
            who = operator or "you"
            self.app.notify(
                f"advisory: {who} does not hold the CEO/founder hat for accept-cost",
                severity="warning",
                timeout=5,
            )
        outcome_id, solution_id = self._accept_args
        result = run_accept_cost(self.program, outcome_id, solution_id)
        self.app.notify(
            ("accepted cost: " if result.ok else "accept-cost failed: ") + result.output,
            severity="information" if result.ok else "error",
            timeout=6,
        )
        self._reload()

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
            read_body(self.message) or "(empty message)",
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
        invalidate_read_caches()
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

    def _journal(self, outcome: str) -> None:
        """Record how this modal ended (gate 6.1 F2, requirement 2).

        Diagnosing the F2 loss cost three round-trips with Roman precisely
        because the journal could not distinguish "the human pressed Esc" from
        "the callback was dropped" — both looked like a modal that opened and
        produced nothing. Now a cancel says so, and a submit is always followed
        immediately by an `action-intent`; a `modal-submitted` with no intent
        after it means the chain broke, and says exactly where.
        """
        log = getattr(self.app, "session_log", None)
        if log is not None:
            log.event(outcome, verb=self._verb)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._journal("modal-submitted")
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self._journal("modal-cancelled")
        self.dismiss(None)


class ProposalScreen(_DecisionActions, Screen):
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
        body = read_body(self.proposal)
        # generated-artifact-quality 1.3 — completeness BEFORE the decision keys
        # act, and only for the artifacts the standard governs. 54 of 119 SCRs
        # carried TODO sections and the team-mode one was APPROVED with three of
        # four reading TODO; the human could not see that without scrolling the
        # body. This is a FACT line — sections filled, anchors present, the
        # author's declared evidence level — never a score. A number would
        # invite ranking SCRs by it, which is refused until an independent
        # critic exists to produce one (the RESERVED-slot precedent, and the
        # triage scorer that ranked rejected-cheap above recommended).
        if self.proposal.msg_type == "spec-change-request":
            from otaman_cli.scr_template import completeness, completeness_line

            facts = completeness(body)
            line = completeness_line(body)
            if not facts["template"]:
                marker = "·"  # legacy: a fact, not a fault
            elif facts["unfilled"]:
                marker = "⚠"
            else:
                marker = "✓"
            yield Static(f"{marker} {line}", id="proposal-completeness", markup=False)
        yield MarkdownViewer(body, show_table_of_contents=False, id="proposal-body")
        yield Footer()

    # The decide path lives in _DecisionActions, shared with the merged
    # Messages list so both run the same writes.
    def _decision_target(self):
        return self.proposal

    def _after_decision(self) -> None:
        self.app.pop_screen()  # the caller's on_screen_resume refreshes

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
        # One human-advance key serving both steps where the human is the
        # blocker: spec-approve an authored change, ratify a complete one (1.5).
        Binding("y", "ratify", "Approve/Ratify", priority=True),
        Binding("a", "archive", "Archive", priority=True),
        Binding("q", "app.quit", "Quit", priority=True),
    ]

    # The column set lives in console.lifecycle and is SHARED with the Artifacts
    # lifecycle lens (3.1), so "verbatim" is structural rather than a promise.
    from otaman_cli.console.lifecycle import LIFECYCLE_COLUMNS as _COLUMNS

    def __init__(self, program: Program) -> None:
        super().__init__()
        self.program = program
        self._rows: list = []  # ChangeRow in table order, indexed by cursor_row

    def compose(self) -> ComposeResult:
        yield _header()
        yield _identity_badge_widget(self.program.root)
        yield _mode_banner(
            f"Lifecycle — all changes by triage · {self.program.name}",
            "↑↓ rows · n nudge · y approve/ratify · a archive · r refresh · esc back · q quit",
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
        invalidate_read_caches()
        self._load()

    def action_back(self) -> None:
        self.app.pop_screen()

    def _load(self) -> None:
        self.run_worker(self._worker, thread=True, exclusive=True, group="lifecycle")

    def _worker(self) -> None:
        from otaman_cli.console.lifecycle import derive_lifecycle_rows

        self.app.call_from_thread(self._paint, derive_lifecycle_rows(self.program))

    def _paint(self, rows: list) -> None:
        self._rows = rows
        table = self.query_one("#lifecycle-table", DataTable)
        table.clear()
        from otaman_cli.console.lifecycle import lifecycle_row_cells

        for r in rows:
            table.add_row(*lifecycle_row_cells(r))
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

    def _advanceable(self, change_name: str) -> bool:
        """Whether *change_name* is one the spec-approved advance accepts.

        Asks ``artifacts.list_authored_changes`` — the SAME set the b-screen
        offers and ``advance_to_spec_approved`` accepts — so the console's offer
        condition can't drift from the action's acceptance condition.
        """
        try:
            from otaman_cli.console import artifacts

            return any(c.name == change_name for c in artifacts.list_authored_changes(self.program))
        except Exception:  # noqa: BLE001 - unresolvable specs repo → not advanceable
            return False

    def action_ratify(self) -> None:
        """The human's ADVANCE action (ratify-spec-approve-split 1.5).

        The human is the blocker at two different lifecycle steps, and this key
        serves whichever one the highlighted row is actually waiting on:
        an AUTHORED change waits on the dispatch gate (advance to spec-approved),
        a COMPLETE one waits ratify-blocked at the archive gate (ratify). It
        previously handled only the second and answered the first with "not
        ratify-blocked" — a dead end on a step the screen itself displays, which
        is what the canon now forbids. Where neither applies the refusal NAMES
        the command that does, so the key is never merely inert.
        """
        row = self._highlighted()
        if row is None:
            return

        # An authored change: the human's decision IS the dispatch-gate blocker.
        if self._advanceable(row.name):
            self._advance_to_spec_approved(row)
            return

        if "human" not in row.next_actor or "ratify" not in row.next_actor:
            self.app.notify(
                f"{row.name} is at stage {row.stage or 'unknown'} — nothing for the human to "
                f"advance here. To approve an authored change: `otaman spec approve {row.name}`; "
                f'to ratify a complete one: `otaman ratify {row.name} --reason "..."`.',
                timeout=8,
            )
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

    def _advance_to_spec_approved(self, row) -> None:
        """Advance an authored row to spec-approved (1.5), via the shared action.

        Runs ``artifacts.advance_to_spec_approved`` — the same code the b-screen
        and ``otaman spec approve`` use — so the transition validation, approver
        hat and audit record are identical from every entry point. The review
        note is optional here (unlike ratify's mandatory reason): this is the
        normal approval path, not a bypass of one.
        """

        def _after(reason: str | None) -> None:
            if reason is None:
                return
            from otaman_cli.console import artifacts
            from otaman_cli.console.journal import run_decision_action

            ok, _ = run_decision_action(
                self.app,
                action="spec-approve",
                target=row.name,
                fn=lambda: artifacts.advance_to_spec_approved(
                    self.program, row.name, reason=reason
                ),
            )
            if ok:
                self._load()

        self.app.push_screen(ReasonModal("spec-approve"), _after)

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


class CapabilityDetailScreen(Screen):
    """One capability's requirements, read in place (3.2).

    The capability lens answers "what does the system do"; this is the read-out
    of a single answer. Renders the spec's own markdown rather than a summary —
    the spec IS the artifact, and paraphrasing it here would create a second
    description to drift.
    """

    BINDINGS = [
        Binding("escape", "back", "Back", priority=True),
        Binding("q", "app.quit", "Quit", priority=True),
    ]

    def __init__(self, program: Program, capability: str, spec_file) -> None:
        super().__init__()
        self.program = program
        # NOT `self.name`: Textual's Widget already owns that as a read-only
        # property, so assigning it raises at construction.
        self.capability = capability
        self.spec_file = spec_file

    def compose(self) -> ComposeResult:
        from otaman_cli.console.capability import requirement_count

        yield _header()
        yield _identity_badge_widget(self.program.root)
        count = requirement_count(self.spec_file)
        yield _mode_banner(
            f"Capability — {self.capability}",
            f"{count} requirement{'s' if count != 1 else ''} · esc back · q quit",
        )
        try:
            body = self.spec_file.read_text(encoding="utf-8")
        except OSError as exc:
            body = f"(could not read {self.spec_file}: {exc})"
        yield MarkdownViewer(body, show_table_of_contents=False, id="capability-body")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()


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
        # 3.4 / D5 — the approved SCR appears HERE, as provenance on the change
        # it minted, rather than as a node of its own anywhere in the tree.
        provenance = d.get("provenance") or []
        if provenance:
            lines.append("provenance:")
            lines.extend(f"  {line}" for line in provenance)
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
        invalidate_read_caches()
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
    /* console-ia-consolidation 1.2 — a visible band boundary. `#mode-banner` is
       the plain-words header every screen yields (D9), and it had NO css rule at
       all, so header text and screen content ran together as one wall. */
    #mode-banner {
        border-bottom: solid $accent;
        padding: 0 1;
        margin-bottom: 1;
    }
    """

    def __init__(
        self,
        programs: list[Program],
        *,
        search_root=None,
        log_dir=None,
        initial_program: Program | None = None,
    ) -> None:
        super().__init__()
        self._programs = programs
        self._search_root = search_root
        self._log_dir = log_dir
        # console-ia-consolidation 1.1 — open straight into this program.
        self._initial_program = initial_program
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
        # The picker is always the BASE screen even when we skip past it: it is
        # Home's back-target, and popping the only screen in the stack raises.
        # Pushing Home on top means the operator never interacts with the
        # picker, while `escape` still lands somewhere sensible (1.1).
        self.push_screen(ProgramPickerScreen(self._programs))
        if self._initial_program is not None:
            self.push_screen(HomeScreen(self._initial_program))

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
