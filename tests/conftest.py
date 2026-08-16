"""Suite-wide isolation (bus-test-isolation tasks 2.1/2.3).

``isolate_bus`` is the shared otaman-core primitive (reference adoption
for the fleet): autouse env-strip of OTAMAN_ROOT/MAESTRO_ROOT/OTAMAN_AGENT,
a tmp program-root sandbox pinned via OTAMAN_ROOT, and the
OTAMAN_TEST_MODE sentinel that makes resolvers refuse non-tmp roots.

Subprocess-spawning tests inherit the sandbox OTAMAN_ROOT via os.environ —
their env helpers must pop OTAMAN_ROOT/MAESTRO_ROOT (or set their own) so
the spawned CLI resolves the test's OWN fixture tree, not the sandbox.

``_isolated_ledger`` keeps the confirmation ledger (which deliberately
lives OUTSIDE tmp isolation, at ~/.otaman/confirmations.log) from being
written by in-process tests of the gated commands.
"""

from __future__ import annotations

import pytest
from otaman_core.testing import isolate_bus  # noqa: F401


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch):
    import otaman_core.confirmations as _conf

    ledger = tmp_path / "test-confirmations.log"
    monkeypatch.setattr(_conf, "default_ledger_path", lambda: ledger)
    return ledger
