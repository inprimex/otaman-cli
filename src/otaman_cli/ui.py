"""The CLI's presentation layer — colors and output templates.

Extracted from `main.py` (CTO review 2026-09-26, "inverted dependency"). Every
command module needs `UI`; `UI` lived in the entry-point module; so 39 of 40 modules
under `commands/` imported the dispatcher, its 153-line help text and its version
resolution in order to print a line. Measured before the move: importing ONE command
module loaded 47 `otaman_cli` modules, `main` and the whole `console` package among
them, in 118ms.

This module is a LEAF on purpose: it imports nothing from `otaman_cli`. That is the
property that makes the inversion gone rather than relocated, and a guard test asserts
it — the next person to reach for a helper from here must not drag the package back
into a cycle.

`main` re-exports `UI` and `C`, so `otaman_cli.main.UI` keeps resolving for anything
outside this repo that learned to import it there.
"""

from __future__ import annotations

import os
import re
import sys

# Fix Windows console encoding for Unicode output
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# Colors for terminal output
# Colors for terminal output
class C:
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    RESET = "\033[0m"

    @classmethod
    def disable(cls) -> None:
        for attr in ("BOLD", "DIM", "RED", "GREEN", "YELLOW", "BLUE", "CYAN", "RESET"):
            setattr(cls, attr, "")


# Disable colors if not a terminal or on Windows without ANSI support. Module level
# and it has to stay module level: every `UI` method reads `C` at call time, so the
# decision must be made before the first line is printed, and it was made here when
# this lived in `main`.
if not sys.stdout.isatty() or (
    sys.platform == "win32" and "WT_SESSION" not in os.environ and "TERM" not in os.environ
):
    C.disable()


# ---------------------------------------------------------------------------
# UI — standardized output templates
# ---------------------------------------------------------------------------


class UI:
    """Consistent output formatting for all otaman CLI commands.

    Semantic methods:
        error/warn/ok/info/muted — status messages
        blocked/action — workflow state
        header/subheader — section dividers
        table/kv/bullet — structured data
        agent/repo/path/priority — inline formatters
        badge — inline status tag
        confirm — yes/no prompt
    """

    @staticmethod
    def error(msg: str) -> None:
        print(f"  {C.RED}[!]{C.RESET} {msg}")

    @staticmethod
    def warn(msg: str) -> None:
        print(f"  {C.YELLOW}[!]{C.RESET} {msg}")

    @staticmethod
    def ok(msg: str) -> None:
        print(f"  {C.GREEN}[+]{C.RESET} {msg}")

    @staticmethod
    def info(msg: str) -> None:
        print(f"  {C.CYAN}[i]{C.RESET} {msg}")

    @staticmethod
    def muted(msg: str) -> None:
        print(f"      {C.DIM}{msg}{C.RESET}")

    @staticmethod
    def action(msg: str) -> None:
        print(f"  {C.YELLOW}->{C.RESET} {msg}")

    @staticmethod
    def blocked(msg: str) -> None:
        print(f"  {C.RED}[X]{C.RESET} {C.BOLD}{msg}{C.RESET}")

    @staticmethod
    def header(title: str) -> None:
        w = max(len(title) + 4, 50)
        print(f"\n  {C.BOLD}{C.CYAN}{'─' * w}{C.RESET}")
        print(f"  {C.BOLD}{C.CYAN}  {title}{C.RESET}")
        print(f"  {C.BOLD}{C.CYAN}{'─' * w}{C.RESET}\n")

    @staticmethod
    def subheader(title: str) -> None:
        print(f"\n  {C.BOLD}{title}{C.RESET}")

    @staticmethod
    def kv(key: str, value: str, value_color: str = "") -> None:
        vc = value_color or ""
        rst = C.RESET if vc else ""
        print(f"  {C.DIM}{key}:{C.RESET} {vc}{value}{rst}")

    @staticmethod
    def bullet(text: str, icon: str = "*", color: str = "") -> None:
        c = color or C.YELLOW
        print(f"    {c}{icon}{C.RESET} {text}")

    @staticmethod
    def table(
        headers: list[str], rows: list[list[str]], col_widths: list[int] | None = None
    ) -> None:
        if not col_widths:
            col_widths = []
            for i, h in enumerate(headers):
                max_w = len(h)
                for row in rows:
                    if i < len(row):
                        clean = re.sub(r"\033\[[0-9;]*m", "", str(row[i]))
                        max_w = max(max_w, len(clean))
                col_widths.append(min(max_w + 2, 40))

        hdr = "  "
        sep = "  "
        for i, h in enumerate(headers):
            w = col_widths[i] if i < len(col_widths) else 15
            hdr += f"{C.BOLD}{h:<{w}}{C.RESET}"
            sep += f"{'─' * (w - 1)} "
        print(hdr)
        print(f"  {C.DIM}{sep.strip()}{C.RESET}")

        for row in rows:
            line = "  "
            for i, cell in enumerate(row):
                w = col_widths[i] if i < len(col_widths) else 15
                clean = re.sub(r"\033\[[0-9;]*m", "", str(cell))
                padding = w - len(clean)
                line += f"{cell}{' ' * max(padding, 1)}"
            print(line)

    # --- Inline formatters (return strings, don't print) ---

    @staticmethod
    def agent(name: str) -> str:
        return f"{C.GREEN}{name}{C.RESET}"

    @staticmethod
    def repo(name: str) -> str:
        return f"{C.BOLD}{C.CYAN}{name}{C.RESET}"

    @staticmethod
    def path(p: str) -> str:
        return f"{C.DIM}{p}{C.RESET}"

    @staticmethod
    def priority(p: str) -> str:
        colors = {"urgent": C.RED + C.BOLD, "high": C.RED, "normal": "", "low": C.DIM}
        c = colors.get(p, "")
        return f"{c}{p}{C.RESET}" if c else p

    @staticmethod
    def badge(text: str, color: str = "") -> str:
        c = color or C.GREEN
        return f"{c}[{text}]{C.RESET}"

    @staticmethod
    def confirm(question: str) -> bool:
        resp = input(f"  {C.BOLD}? {question}{C.RESET} [Y/n] ").strip().lower()
        return resp != "n"


__all__ = ["C", "UI"]
