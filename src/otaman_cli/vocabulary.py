"""knowledge-v2 2.3 — `domain:` validated against the program's vocabulary.

`function:` and `domain:` look alike and behave oppositely. The function enum is
platform-owned and fixed at eight values (core validates it); the domain is the
PROGRAM's industry vocabulary and is therefore the program's to declare — "industry
domain = vocabulary, function = fixed enum" (Roman's taxonomy ruling 2026-09-23).
Core says so explicitly and refuses to validate `domain` itself, which is why the
check lives here.

The vocabulary registry is one of the four dispatched-but-unbuilt sibling
registries, so its SCHEMA is not settled. Rather than guess it, this reads through
the same schema-agnostic reader the console uses for those registries: any
top-level list, or the first list-of-mappings in a mapping, with the term named by
whichever of id/key/slug/name/term is present.

An absent registry does NOT silently accept every domain, and does not refuse every
domain either. It accepts and SAYS the term went unvalidated, naming the path a
registry would live at. Refusing would make `domain:` unusable for every program
that has not built a vocabulary yet — which today is all of them. Accepting in
silence would report a validated field that nothing validated.
"""

from __future__ import annotations

from pathlib import Path

#: Filename under the registry home, matching the sibling-registry convention
#: (`<key>.yaml`) that `extra_registries` already resolves.
REGISTRY_FILENAME = "vocabulary.yaml"


def registry_path(root: Path) -> Path | None:
    """Where this program's vocabulary registry lives, or None when unresolvable."""
    try:
        from otaman_cli.registries.loader import strategy_repo

        home = strategy_repo(root)
    except Exception:  # noqa: BLE001 - unresolvable home → unresolvable registry
        return None
    return (home / REGISTRY_FILENAME) if home is not None else None


#: Why there is nothing to check a term against. Three different facts, and the
#: first version of this module collapsed all three into `None` — contradicting
#: its own docstring, which said None and `set()` were deliberately distinct.
#:
#: The cost: a program whose `vocabulary.yaml` exists and declares nothing was
#: told "no vocabulary registry at <path>" — false, the file is right there — and
#: every `--domain` was accepted as unvalidated instead of being refused for not
#: being declared. A registry that could not be PARSED got the same sentence, so
#: a broken registry was indistinguishable from no registry at all. That is the
#: fail-open family spec-agent found in plugin's dispatch gate and I then found
#: twice in mine (cli #223).
ABSENT = "absent"
UNREADABLE = "unreadable"


def read_terms(root: Path) -> tuple[set[str] | None, str]:
    """``(terms, reason)`` — the declared vocabulary, or why it could not be read.

    `terms` is a set (possibly EMPTY, which means "checked, and the registry
    declares nothing" — a term is then genuinely undeclared and refusable) or None
    with a `reason` of :data:`ABSENT` or :data:`UNREADABLE`.

    The read goes through `extra_registries.entry_rows`, which returns `[]` for
    both an unparseable file and an empty one — correct for its own job (a TUI
    preview must not raise), and not enough to validate against. So this
    distinguishes the two itself rather than inferring from a row count.
    """
    path = registry_path(root)
    if path is None or not path.is_file():
        return None, ABSENT

    from otaman_cli.console.extra_registries import entry_rows

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None, UNREADABLE
    if not raw.strip():
        # An empty FILE is a declared-nothing registry, not a broken one — the
        # same ruling as cli #200, where an empty FILTER was not an empty SURFACE.
        return set(), ""

    # The STRICT loader, deliberately, not `yaml_fast.load_file`: that one returns
    # None for malformed YAML instead of raising, so `vocabulary: [: : :` and a
    # comments-only file are the same value to it. Measured — all four malformed
    # samples I tried came back as None. Fine for a TUI preview, useless for
    # deciding whether a registry could be read, which is the whole question here.
    try:
        import yaml

        data = yaml.safe_load(raw)
    except Exception:  # noqa: BLE001 - a parse failure is UNREADABLE, never absent
        return None, UNREADABLE
    if data is None:
        # Parsed clean and holds nothing (comments only) — declared-nothing.
        return set(), ""
    if not isinstance(data, (dict, list)):
        return None, UNREADABLE

    return {ident.strip().lower() for ident, _ in entry_rows(path) if ident.strip()}, ""


def known_terms(root: Path) -> set[str] | None:
    """The declared vocabulary terms, or None when there is nothing to read.

    Thin wrapper over :func:`read_terms` for callers that do not need the reason.
    An EMPTY set now reaches them, where this used to return None for it.
    """
    terms, _ = read_terms(root)
    return terms


def check_domain(root: Path, domain: str) -> tuple[bool, str]:
    """``(accepted, note)`` for *domain*.

    A non-empty note is always worth printing: on acceptance it says the term
    went unvalidated and why; on refusal it names the registry path, so the
    operator knows where to declare the term rather than guessing.
    """
    term = (domain or "").strip()
    if not term:
        return True, ""

    terms, reason = read_terms(root)
    path = registry_path(root)
    where = str(path) if path else f"<registry home unset>/{REGISTRY_FILENAME}"
    if terms is None:
        # Accept-and-state, with the reason named. "No registry" and "the registry
        # is there and would not parse" call for different actions by the reader —
        # declare the term vs. fix the file — so they cannot share a sentence.
        if reason == UNREADABLE:
            return True, (
                f"domain {term!r} was NOT validated: the vocabulary registry at "
                f"{where} exists and could not be read. The term is recorded as "
                "written — fix the registry, this is not a missing-registry case."
            )
        return True, (
            f"domain {term!r} was NOT validated: no vocabulary registry at {where}. "
            "The term is recorded as written."
        )

    if term.lower() in terms:
        return True, ""

    listed = ", ".join(sorted(terms)[:8]) or "(none declared)"
    return False, (
        f"domain {term!r} is not in the program vocabulary.\n"
        f"  Registry: {path}\n"
        f"  Declared: {listed}\n"
        "  Add the term to the registry, or omit --domain."
    )
