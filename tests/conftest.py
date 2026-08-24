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


@pytest.fixture(autouse=True)
def _isolated_tenant_home(tmp_path, monkeypatch):
    """Keep TOTP enrollment (hitl-confirmation-adapters 1.2) off the real
    ``~/.otaman``. Both the tenant ``hitl.yaml`` and the tenant
    ``secrets.env`` (where the TOTP seed lands) live outside tmp by default,
    like the confirmation ledger — so redirect their path resolvers to tmp
    for every in-process test. ``_secrets.resolve`` reads the tenant dotenv
    via ``tenant_secrets_path`` too, so this one patch isolates enroll AND
    the adapter's verify path.
    """
    import otaman_core._secrets as _secrets

    import otaman_cli.hitl.config as _config

    home = tmp_path / "tenant-home"

    # `_secrets.resolve` calls tenant_secrets_path(context.get("home")) — i.e.
    # with an explicit None — so coerce a falsy home to the tmp base rather
    # than relying on a default arg.
    def _hitl_path(arg=None):
        return (arg or home) / ".otaman" / "hitl.yaml"

    def _secrets_path(arg=None):
        return (arg or home) / ".otaman" / "secrets.env"

    monkeypatch.setattr(_config, "hitl_config_path", _hitl_path)
    monkeypatch.setattr(_secrets, "tenant_secrets_path", _secrets_path)
    return home
