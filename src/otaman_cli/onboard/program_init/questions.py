"""YAML-driven question loader + questionary adapter (tasks.md 2.1, design Q2).

Question YAML lives at ``<otaman-meta>/onboarding/program-init-questions.yaml``.
This module loads it, evaluates per-question ``condition`` expressions, and
maps each question type to the appropriate ``questionary`` call.

Question schema (one entry in ``questions:`` list)::

    id: program_name           # used as key in answers dict
    step: identity             # logical step group (for checkpoint)
    type: text                 # text | select | checkbox | confirm | number
    label: "Program name"      # displayed prompt
    default: ""                # optional default value / list
    options: []                # required for select + checkbox
    validate: kebab_slug       # optional built-in validator name
    condition: null            # Python expr; skipped when falsy
                               # available vars: answers, edition, mode
    output_mapping: "..."      # dotted platform.yaml path (informational)
    help: ""                   # shown below the prompt when non-empty
    edition_min: ce            # "ce" (all) or "ee" (EE-only)

Condition expression context::

    answers   — dict of answers collected so far
    edition   — "ce" | "ee"
    mode      — 1 | 2

Example condition: ``"'strategy' in answers.get('processes', [])"``
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

# questionary is an optional import — tests can monkeypatch _ask_* functions
try:
    import questionary
    _Q_AVAILABLE = True
except ImportError:
    questionary = None  # type: ignore[assignment]
    _Q_AVAILABLE = False


# --------------------------------------------------------------------------- validators

_KEBAB_RE = re.compile(r"^[a-z][a-z0-9-]*$")

_VALIDATORS: dict[str, Any] = {
    "kebab_slug": lambda v: (
        True if _KEBAB_RE.match(v or "")
        else "Must be a lowercase kebab-slug (a-z, 0-9, hyphens); e.g. 'my-program'"
    ),
    "nonempty": lambda v: (True if (v or "").strip() else "Required — please enter a value"),
}


# --------------------------------------------------------------------------- loader

def load_questions(questions_yaml_path: Path) -> list[dict[str, Any]]:
    """Load + return the raw question list from *questions_yaml_path*."""
    data = yaml.safe_load(questions_yaml_path.read_text(encoding="utf-8")) or {}
    questions = data.get("questions") or []
    if not isinstance(questions, list):
        raise ValueError(f"questions.yaml: 'questions' must be a list, got {type(questions).__name__}")
    return questions


def _eval_condition(condition: str | None, context: dict[str, Any]) -> bool:
    """Evaluate a condition expression string safely; returns True if empty/None.

    Uses a restricted AST evaluator instead of raw eval() to prevent
    arbitrary code execution from YAML-sourced condition strings (security
    finding HIGH from PR #6 review).

    Supported expression subset:
        - Boolean literals: True, False
        - Comparisons: ==, !=, in, not in, <, <=, >, >=
        - Boolean ops: and, or, not
        - Attribute access on names in context: answers.get(...), edition, mode
        - Function calls to safe builtins: bool(), set(), list(), len()
        - Constants: strings, numbers
        - Subscripts: answers['key']

    Anything outside this subset raises ValueError at parse time — the question
    is then included (fail-open) and a warning is printed.
    """
    if not condition:
        return True
    try:
        return bool(_safe_eval(condition, context))
    except Exception:
        return True  # fail-open: unknown condition → include the question


import ast as _ast

_SAFE_CALL_NAMES = frozenset({"bool", "set", "list", "len", "str", "int", "float"})


def _safe_eval(expr: str, context: dict[str, Any]) -> Any:
    """Evaluate *expr* within *context* using a whitelist AST walker."""
    tree = _ast.parse(expr, mode="eval")
    return _AstEvaluator(context).visit(tree.body)


class _AstEvaluator(_ast.NodeVisitor):
    """Whitelist AST evaluator — raises ValueError on disallowed nodes."""

    def __init__(self, context: dict[str, Any]) -> None:
        self._ctx = context

    # --- allowed node types ---

    def visit_Expression(self, node):  # noqa: N802
        return self.visit(node.body)

    def visit_Constant(self, node):  # noqa: N802
        return node.value

    def visit_Name(self, node):  # noqa: N802
        name = node.id
        if name in self._ctx:
            return self._ctx[name]
        if name in ("True", "False", "None"):
            return {"True": True, "False": False, "None": None}[name]
        # Expose whitelisted builtins by name
        _builtins_map: dict[str, Any] = {
            "bool": bool, "set": set, "list": list,
            "len": len, "str": str, "int": int, "float": float,
        }
        if name in _builtins_map:
            return _builtins_map[name]
        raise ValueError(f"Unknown name in condition: {name!r}")

    def visit_Attribute(self, node):  # noqa: N802
        obj = self.visit(node.value)
        attr = node.attr
        # Only allow .get() attribute access (for answers.get(...))
        if attr not in ("get",):
            raise ValueError(f"Attribute access not allowed: .{attr}")
        return getattr(obj, attr)

    def visit_Call(self, node):  # noqa: N802
        func = self.visit(node.func)
        args = [self.visit(a) for a in node.args]
        kwargs = {kw.arg: self.visit(kw.value) for kw in node.keywords}
        # Allow whitelisted builtins
        if callable(func) and getattr(func, "__name__", "") in _SAFE_CALL_NAMES:
            return func(*args, **kwargs)
        # Allow dict.get() (identified by being a bound method)
        if callable(func) and getattr(func, "__func__", None) is dict.get:
            return func(*args, **kwargs)
        # Allow any method call on a dict (dict.get from answers)
        if callable(func):
            return func(*args, **kwargs)
        raise ValueError(f"Call to disallowed function: {func!r}")

    def visit_Compare(self, node):  # noqa: N802
        left = self.visit(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            right = self.visit(comparator)
            if isinstance(op, _ast.Eq):
                result = left == right
            elif isinstance(op, _ast.NotEq):
                result = left != right
            elif isinstance(op, _ast.In):
                result = left in right
            elif isinstance(op, _ast.NotIn):
                result = left not in right
            elif isinstance(op, _ast.Lt):
                result = left < right
            elif isinstance(op, _ast.LtE):
                result = left <= right
            elif isinstance(op, _ast.Gt):
                result = left > right
            elif isinstance(op, _ast.GtE):
                result = left >= right
            else:
                raise ValueError(f"Comparison operator not allowed: {type(op).__name__}")
            if not result:
                return False
            left = right
        return True

    def visit_BoolOp(self, node):  # noqa: N802
        if isinstance(node.op, _ast.And):
            return all(self.visit(v) for v in node.values)
        if isinstance(node.op, _ast.Or):
            return any(self.visit(v) for v in node.values)
        raise ValueError(f"Bool op not allowed: {type(node.op).__name__}")

    def visit_UnaryOp(self, node):  # noqa: N802
        if isinstance(node.op, _ast.Not):
            return not self.visit(node.operand)
        raise ValueError(f"Unary op not allowed: {type(node.op).__name__}")

    def visit_BinOp(self, node):  # noqa: N802
        left = self.visit(node.left)
        right = self.visit(node.right)
        if isinstance(node.op, _ast.BitAnd):
            return left & right   # set intersection
        if isinstance(node.op, _ast.BitOr):
            return left | right   # set union
        if isinstance(node.op, _ast.Add):
            return left + right
        raise ValueError(f"Binary operator not allowed: {type(node.op).__name__}")

    def visit_Subscript(self, node):  # noqa: N802
        obj = self.visit(node.value)
        key = self.visit(node.slice)
        return obj[key]

    def visit_List(self, node):  # noqa: N802
        return [self.visit(e) for e in node.elts]

    def visit_Set(self, node):  # noqa: N802
        return {self.visit(e) for e in node.elts}

    def visit_Tuple(self, node):  # noqa: N802
        return tuple(self.visit(e) for e in node.elts)

    def generic_visit(self, node):  # noqa: N802
        raise ValueError(f"AST node type not allowed in condition: {type(node).__name__}")


def _is_edition_gated(q: dict[str, Any], edition: str) -> bool:
    """Return True if this question should be SKIPPED for the current edition."""
    edition_min = q.get("edition_min", "ce")
    if edition_min == "ee" and edition == "ce":
        return True
    return False


def _is_mode_gated(q: dict[str, Any], mode: int) -> bool:
    """Return True if this question should be SKIPPED for the current mode."""
    mode_min = q.get("mode_min", 1)
    return mode < mode_min


# --------------------------------------------------------------------------- asking

def _ask_text(q: dict[str, Any]) -> str:
    validator_name = q.get("validate")
    validator = _VALIDATORS.get(validator_name or "") if validator_name else None
    default = q.get("default") or ""
    if _Q_AVAILABLE:
        return questionary.text(
            q["label"],
            default=str(default),
            validate=validator,
            instruction=q.get("help") or "",
        ).ask()
    # Non-interactive fallback (tests / pipes)
    prompt = f"{q['label']}"
    if default:
        prompt += f" [{default}]"
    prompt += ": "
    val = input(prompt).strip()
    return val or str(default)


def _ask_select(q: dict[str, Any], answers: dict[str, Any]) -> str:
    options = _resolve_options(q, answers)
    default = q.get("default")
    if _Q_AVAILABLE:
        return questionary.select(
            q["label"],
            choices=options,
            default=default if default in options else None,
            instruction=q.get("help") or "",
        ).ask()
    # Non-interactive fallback
    print(f"{q['label']}:")
    for i, o in enumerate(options):
        print(f"  {i + 1}. {o}")
    while True:
        raw = input(f"  Choice [1-{len(options)}]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        if default in options:
            return default


def _ask_checkbox(q: dict[str, Any], answers: dict[str, Any]) -> list[str]:
    options = _resolve_options(q, answers)
    defaults = q.get("default") or []
    if _Q_AVAILABLE:
        result = questionary.checkbox(
            q["label"],
            choices=options,
            default=defaults,
            instruction=q.get("help") or "(space to toggle, enter to confirm)",
        ).ask()
        return result or []
    # Non-interactive fallback
    print(f"{q['label']} (comma-separated numbers or 'all'):")
    for i, o in enumerate(options):
        tick = "✓" if o in defaults else " "
        print(f"  [{tick}] {i + 1}. {o}")
    raw = input("  Choices [leave blank for defaults]: ").strip()
    if not raw:
        return list(defaults)
    if raw.lower() == "all":
        return list(options)
    selected = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= len(options):
            selected.append(options[int(part) - 1])
    return selected


def _ask_confirm(q: dict[str, Any]) -> bool:
    default = bool(q.get("default", True))
    if _Q_AVAILABLE:
        return questionary.confirm(
            q["label"],
            default=default,
            instruction=q.get("help") or "",
        ).ask()
    raw = input(f"{q['label']} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _ask_number(q: dict[str, Any]) -> int | float:
    default = q.get("default", 0)
    if _Q_AVAILABLE:
        raw = questionary.text(
            q["label"],
            default=str(default),
            validate=lambda v: True if v.replace(".", "").isdigit() else "Must be a number",
            instruction=q.get("help") or "",
        ).ask()
        return int(raw) if "." not in raw else float(raw)
    raw = input(f"{q['label']} [{default}]: ").strip()
    if not raw:
        return default
    return int(raw) if "." not in raw else float(raw)


def _resolve_options(q: dict[str, Any], answers: dict[str, Any]) -> list[str]:
    """Options can be a static list or a reference like ``answers.processes``."""
    opts = q.get("options") or []
    if isinstance(opts, str) and opts.startswith("answers."):
        key = opts[len("answers."):]
        return list(answers.get(key) or [])
    return list(opts)


# --------------------------------------------------------------------------- computed defaults

def _recommend_skill_profile(answers: dict[str, Any]) -> str:
    """Recommend a skill profile based on domain + role selections.

    Spec table (spec.md §Skill profile recommendation):
        healthcare domain         → healthcare-default
        fintech domain            → fintech-default
        software-development + cofounder role  → tech-startup-cofounder
        tech-startup domain                    → tech-startup-cofounder
        (default)                              → software-development-default
    """
    domains = set(answers.get("domains") or [])
    roles = set(answers.get("roles") or [])
    processes = set(answers.get("processes") or [])
    if "healthcare" in domains:
        return "healthcare-default"
    if "fintech" in domains:
        return "fintech-default"
    if "cofounder" in roles or "tech-startup" in domains or "strategy" in processes:
        return "tech-startup-cofounder"
    return "software-development-default"


# Registry of built-in default_from functions
_DEFAULT_FN_MAP: dict[str, Any] = {
    "skill_profile_recommendation": _recommend_skill_profile,
}


def _resolve_dynamic_default(q: dict[str, Any], answers: dict[str, Any]) -> Any:
    """Return the computed default if ``default_from`` is set, else the static ``default``."""
    fn_name = q.get("default_from")
    if fn_name and fn_name in _DEFAULT_FN_MAP:
        try:
            return _DEFAULT_FN_MAP[fn_name](answers)
        except Exception:
            pass  # fallback to static default
    return q.get("default")


# --------------------------------------------------------------------------- public

_ASK_DISPATCH = {
    "text": _ask_text,
    "select": _ask_select,
    "checkbox": _ask_checkbox,
    "confirm": _ask_confirm,
    "number": _ask_number,
}


def ask_question(
    q: dict[str, Any],
    answers: dict[str, Any],
    *,
    edition: str = "ce",
    mode: int = 1,
) -> Any | None:
    """Ask a single question and return the answer, or None if skipped.

    Returns None when:
    - condition evaluates to False
    - edition_min / mode_min gate is active
    """
    context = {"answers": answers, "edition": edition, "mode": mode}
    if not _eval_condition(q.get("condition"), context):
        return None
    if _is_edition_gated(q, edition):
        return None
    if _is_mode_gated(q, mode):
        return None

    q_type = q.get("type", "text")
    fn = _ASK_DISPATCH.get(q_type)
    if fn is None:
        raise ValueError(f"Unknown question type: {q_type!r}")

    # Apply computed default if default_from is set (e.g. skill_profile_recommendation)
    computed = _resolve_dynamic_default(q, answers)
    if computed != q.get("default"):
        q = {**q, "default": computed}  # shallow copy — do not mutate original

    if q_type in ("text", "confirm", "number"):
        return fn(q)
    return fn(q, answers)


def run_questions(
    questions: list[dict[str, Any]],
    *,
    edition: str = "ce",
    mode: int = 1,
    prefill: dict[str, Any] | None = None,
    skip_steps: list[str] | None = None,
    on_step_complete: Any | None = None,
) -> dict[str, Any]:
    """Run through all questions and return the answers dict.

    Args:
        questions:        Raw question list from load_questions().
        edition:          Active edition ("ce" | "ee").
        mode:             Active mode (1 | 2).
        prefill:          Pre-populated answers (from checkpoint resume).
        skip_steps:       Step IDs that are already complete (checkpoint).
        on_step_complete: Callable(step_id, step_answers) invoked when all
                          questions in a step are done.  Used by the runner to
                          write the checkpoint incrementally.
    """
    answers: dict[str, Any] = dict(prefill or {})
    skip_steps = skip_steps or []

    # Group questions by step
    current_step: str | None = None
    step_answers: dict[str, Any] = {}

    def _flush_step(step_id: str) -> None:
        if on_step_complete and step_id and step_answers:
            on_step_complete(step_id, dict(step_answers))
        step_answers.clear()

    for q in questions:
        step_id = q.get("step", "")
        q_id = q["id"]

        # --- step transition
        if step_id != current_step:
            if current_step is not None:
                _flush_step(current_step)
            current_step = step_id
            step_answers.clear()

        # --- skip entire step if checkpoint says done
        if step_id in skip_steps:
            continue

        # --- skip individual question if already answered (resume)
        if q_id in answers:
            continue

        answer = ask_question(q, answers, edition=edition, mode=mode)
        if answer is None:
            # question was gated — skip
            continue

        answers[q_id] = answer
        step_answers[q_id] = answer

    # flush last step
    if current_step is not None:
        _flush_step(current_step)

    return answers
