"""Connection management for `otaman connection` (agent-credential-access 3.1).

The READ/resolve/cascade/check side lives in otaman-core
(`otaman_core.connections`, `otaman_core.connection_check`). This package
owns the cli surface: the per-scope `connections.yaml` WRITER (core has no
write helper) and the command dispatch/rendering. Every surface here is
values-free by construction — a connection stores a `secret_ref` (a backend
key NAME), never a secret value (spec: values SHALL NEVER be exposed).
"""
