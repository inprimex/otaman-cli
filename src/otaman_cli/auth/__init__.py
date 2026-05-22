"""otaman login — OAuth 2.0 Device Authorization Grant against the
configured OIDC provider (Zitadel for v0).

Stores the access token at ``~/.otaman/token.cache`` (mode 0600) so
later commands (and the launch-agents.sh runner-client mode) can find
it without reauthenticating.
"""
