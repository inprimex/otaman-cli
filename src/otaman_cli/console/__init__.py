"""Interactive human console (`otaman -i`) — interactive-human-console.

A Textual TUI where a HUMAN, in their own SSH-identified session with NO LLM
in the loop, picks a program, reads a rendered proposal, and approves/rejects
through the same privileged ledger-gated writer as `otaman approve`. This
dissolves the HITL context-leak problem (no agent context exists to leak
into) and supersedes the chat-approval fallback for the attended case.

Layering keeps Textual OPTIONAL and the logic testable without a terminal:
- `bus.py` — pure, Textual-free bus reads (program discovery, pending
  proposals). Importable and testable with no TUI dependency.
- `app.py` — the Textual `App`/`Screen` UI. Imported only when the `console`
  extra (Textual, exact-pinned) is installed.
- `launch.py` — the `otaman -i` entry: checks the extra, resolves context,
  runs the app (or prints an install hint when Textual is absent).
"""
