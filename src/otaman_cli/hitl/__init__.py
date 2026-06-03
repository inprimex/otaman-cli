"""Human-in-the-loop (HITL) stack + task-mode annotations.

Implements `auto-session-spawn-implementation` task §3 (cli-agent):
- `[headless]` / `[interactive]` mode annotation parser for `otaman assign`
- `otaman hitl list / next / take` subcommands for reading
  `request-human-review` messages and emitting `human-decision` replies

Schemas: `auto-session-spawn-on-bus-events/design.md` Q2 (annotations) and
Q4 / Resolved 2026-05-21 (HITL message frontmatter + body).
"""
