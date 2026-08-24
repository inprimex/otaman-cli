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
    """Keep every tenant-scope ``~/.otaman`` write out of the real home.

    Several features persist under ``~/.otaman`` (outside tmp by default, like
    the confirmation ledger): TOTP enrollment (``hitl.yaml`` +
    ``secrets.env``, hitl 1.2) and connections (``connections.yaml`` +
    ``connection-checks.json``, agent-credential-access 3.1). Redirect all
    their path resolvers to tmp for every in-process test. ``_secrets.resolve``
    reads the tenant dotenv via ``tenant_secrets_path`` too, so patching it
    isolates enroll AND the adapter/check verify paths.
    """
    import otaman_core._secrets as _secrets
    import otaman_core.connection_check as _conn_check

    import otaman_cli.connections.store as _conn_store
    import otaman_cli.hitl.config as _config

    home = tmp_path / "tenant-home"

    # Several of these are called with an explicit None (e.g.
    # `tenant_secrets_path(context.get("home"))`), so coerce falsy → tmp base
    # rather than relying on a default arg.
    def _hitl_path(arg=None):
        return (arg or home) / ".otaman" / "hitl.yaml"

    def _secrets_path(arg=None):
        return (arg or home) / ".otaman" / "secrets.env"

    def _report_path(arg=None):
        return (arg or home) / ".otaman" / "connection-checks.json"

    def _tenant_conns_path(arg=None):
        return (arg or home) / ".otaman" / "connections.yaml"

    monkeypatch.setattr(_config, "hitl_config_path", _hitl_path)
    monkeypatch.setattr(_secrets, "tenant_secrets_path", _secrets_path)
    monkeypatch.setattr(_conn_check, "report_store_path", _report_path)
    monkeypatch.setattr(_conn_store, "tenant_connections_path", _tenant_conns_path)
    return home
