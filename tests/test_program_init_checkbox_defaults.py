"""Regression: questionary 2.x checkbox API change.

In questionary 1.x, `checkbox(default=[...])` accepted a list of
pre-selected items. In 2.x, `default` is a single value (initial focus
only); pre-selection is done via Choice(name, checked=True). Passing
`default=[]` raises `ValueError: Invalid 'default' value passed`.

This test verifies that `_ask_checkbox` calls questionary with the new
API shape (Choice list + scalar `default`), regardless of how `default`
is declared in the question YAML (empty list, populated list, or None).
"""

from __future__ import annotations

from unittest import mock

import pytest

from otaman_cli.onboard.program_init.questions import _ask_checkbox


@pytest.fixture
def mock_questionary(monkeypatch):
    """Replace `questionary.checkbox` with a recording mock."""
    import otaman_cli.onboard.program_init.questions as qmod

    fake = mock.MagicMock()
    fake.checkbox = mock.MagicMock()
    # checkbox(...).ask() chain returns the test-provided value
    fake.checkbox.return_value.ask.return_value = []
    # Use a real Choice constructor so the code under test can build them
    import questionary as real_q
    fake.Choice = real_q.Choice

    monkeypatch.setattr(qmod, "questionary", fake)
    monkeypatch.setattr(qmod, "_Q_AVAILABLE", True)
    return fake


def test_checkbox_with_empty_default_passes_scalar_none(mock_questionary):
    """default: [] in YAML → questionary.checkbox(default=None), no exception."""
    q = {
        "id": "domains",
        "type": "checkbox",
        "label": "Pick domains",
        "options": ["a", "b", "c"],
        "default": [],
    }
    _ask_checkbox(q, {})

    mock_questionary.checkbox.assert_called_once()
    kwargs = mock_questionary.checkbox.call_args.kwargs
    assert kwargs["default"] is None  # not [] — questionary 2.x rejects lists
    # choices is now a list of Choice objects, not raw strings
    assert all(hasattr(c, "checked") for c in kwargs["choices"])
    # No items are pre-checked (defaults was empty)
    assert all(c.checked is False for c in kwargs["choices"])


def test_checkbox_with_populated_default_pre_checks_items(mock_questionary):
    """default: ['a', 'c'] → those Choice items have checked=True."""
    q = {
        "id": "domains",
        "type": "checkbox",
        "label": "Pick domains",
        "options": ["a", "b", "c"],
        "default": ["a", "c"],
    }
    _ask_checkbox(q, {})

    kwargs = mock_questionary.checkbox.call_args.kwargs
    choices = kwargs["choices"]
    checked_titles = [c.title for c in choices if c.checked]
    assert sorted(checked_titles) == ["a", "c"]
    # `default` (focus) is the first defaulted option that exists in choices
    assert kwargs["default"] == "a"


def test_checkbox_with_no_default_key(mock_questionary):
    """No default key in YAML → no items checked, default=None."""
    q = {
        "id": "x",
        "type": "checkbox",
        "label": "X",
        "options": ["a", "b"],
    }
    _ask_checkbox(q, {})

    kwargs = mock_questionary.checkbox.call_args.kwargs
    assert kwargs["default"] is None
    assert all(c.checked is False for c in kwargs["choices"])


def test_checkbox_default_with_unknown_value_does_not_focus(mock_questionary):
    """default=['zzz'] (not in options) → no focus, no crash, no pre-check."""
    q = {
        "id": "x",
        "type": "checkbox",
        "label": "X",
        "options": ["a", "b"],
        "default": ["zzz"],
    }
    _ask_checkbox(q, {})

    kwargs = mock_questionary.checkbox.call_args.kwargs
    assert kwargs["default"] is None  # zzz isn't in options
    assert all(c.checked is False for c in kwargs["choices"])
