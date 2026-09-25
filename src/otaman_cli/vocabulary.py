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


def known_terms(root: Path) -> set[str] | None:
    """The declared vocabulary terms, or None when there is no registry to read.

    None means "could not check" and is deliberately distinct from `set()`,
    which means "checked, and the registry declares nothing".
    """
    path = registry_path(root)
    if path is None or not path.is_file():
        return None
    from otaman_cli.console.extra_registries import entry_rows

    rows = entry_rows(path)
    if not rows:
        return None
    return {ident.strip().lower() for ident, _ in rows if ident.strip()}


def check_domain(root: Path, domain: str) -> tuple[bool, str]:
    """``(accepted, note)`` for *domain*.

    A non-empty note is always worth printing: on acceptance it says the term
    went unvalidated and why; on refusal it names the registry path, so the
    operator knows where to declare the term rather than guessing.
    """
    term = (domain or "").strip()
    if not term:
        return True, ""

    terms = known_terms(root)
    if terms is None:
        path = registry_path(root)
        where = str(path) if path else f"<registry home unset>/{REGISTRY_FILENAME}"
        return True, (
            f"domain {term!r} was NOT validated: no vocabulary registry at {where}. "
            "The term is recorded as written."
        )

    if term.lower() in terms:
        return True, ""

    path = registry_path(root)
    listed = ", ".join(sorted(terms)[:8]) or "(none declared)"
    return False, (
        f"domain {term!r} is not in the program vocabulary.\n"
        f"  Registry: {path}\n"
        f"  Declared: {listed}\n"
        "  Add the term to the registry, or omit --domain."
    )
