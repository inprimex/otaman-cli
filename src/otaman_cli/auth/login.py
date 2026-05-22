"""``otaman login`` — OAuth 2.0 Device Authorization Grant (RFC 8628).

Per ADR-010, otaman uses Zitadel-issued OIDC tokens for multi-user
authentication. This CLI provides the device-flow client that an
operator runs on their laptop to fetch a token.

Flow:
    1. POST <issuer>/oauth/v2/device_authorization with client_id + scope
    2. Receive {device_code, user_code, verification_uri_complete, ...}
    3. Print verification_uri_complete + user_code, open browser
    4. Poll <issuer>/oauth/v2/token with grant_type=device_code until:
       - 'authorization_pending' -> wait interval, retry
       - 'slow_down' -> back off, retry
       - {access_token: ...} -> store and exit
       - other error -> abort

Token cache: ``~/.otaman/token.cache`` (POSIX 0600). Format is a small
JSON blob with the access_token, refresh_token (if any), expiry epoch,
and the issuer/audience for cross-checking.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_SCOPE = "openid profile urn:zitadel:iam:org:projects:roles"


@dataclass
class DeviceFlowConfig:
    """Where to authenticate and how to identify ourselves."""

    issuer: str                                 # e.g. http://100.65.57.73:8080
    client_id: str                              # Native OIDC client_id from Zitadel
    project_id: str = ""                        # if set, added to scope for project-aud
    scopes: list[str] = field(default_factory=list)
    external_host: str | None = None            # Host header override for ExternalDomain enforcement
    token_path: Path = Path.home() / ".otaman" / "token.cache"


@dataclass
class CachedToken:
    access_token: str
    refresh_token: str | None
    expires_at: int                             # epoch seconds; 0 = no expiry given
    issuer: str
    client_id: str

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "issuer": self.issuer,
            "client_id": self.client_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CachedToken":
        return cls(
            access_token=d["access_token"],
            refresh_token=d.get("refresh_token"),
            expires_at=int(d.get("expires_at") or 0),
            issuer=d.get("issuer", ""),
            client_id=d.get("client_id", ""),
        )

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        # 30s leeway so we don't hand callers a token about to expire
        return time.time() >= (self.expires_at - 30)


class LoginError(RuntimeError):
    """Raised on unrecoverable device-flow failure."""


def _post_form(url: str, data: dict, *, extra_headers: dict | None = None) -> dict:
    """POST application/x-www-form-urlencoded. Returns JSON dict."""
    body = urllib.parse.urlencode(data).encode("utf-8")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise LoginError(f"HTTP {e.code} from {url}: {text}")
    except urllib.error.URLError as e:
        raise LoginError(f"network error contacting {url}: {e}")


def build_scope(cfg: DeviceFlowConfig) -> str:
    parts = list(cfg.scopes) if cfg.scopes else DEFAULT_SCOPE.split()
    if cfg.project_id:
        aud = f"urn:zitadel:iam:org:project:id:{cfg.project_id}:aud"
        if aud not in parts:
            parts.append(aud)
    return " ".join(parts)


def initiate_device_flow(cfg: DeviceFlowConfig) -> dict:
    """POST /oauth/v2/device_authorization. Returns the device-auth response."""
    extras = {"Host": cfg.external_host} if cfg.external_host else None
    resp = _post_form(
        f"{cfg.issuer.rstrip('/')}/oauth/v2/device_authorization",
        {"client_id": cfg.client_id, "scope": build_scope(cfg)},
        extra_headers=extras,
    )
    if "error" in resp:
        raise LoginError(f"device_authorization failed: {resp.get('error_description', resp['error'])}")
    for key in ("device_code", "user_code", "verification_uri_complete"):
        if key not in resp:
            raise LoginError(f"device_authorization response missing {key!r}: {resp}")
    return resp


def poll_for_token(cfg: DeviceFlowConfig, *, device_code: str, interval: float, expires_in: int,
                   on_pending=None) -> dict:
    """Poll /oauth/v2/token until success, denial, or expiry.

    Returns the token-endpoint JSON response on success.
    """
    deadline = time.time() + max(expires_in - 10, 10)
    extras = {"Host": cfg.external_host} if cfg.external_host else None
    while time.time() < deadline:
        resp = _post_form(
            f"{cfg.issuer.rstrip('/')}/oauth/v2/token",
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": cfg.client_id,
            },
            extra_headers=extras,
        )
        if "access_token" in resp:
            return resp
        err = resp.get("error", "unknown_error")
        if err == "authorization_pending":
            if on_pending is not None:
                on_pending()
            time.sleep(interval)
            continue
        if err == "slow_down":
            interval += 5
            time.sleep(interval)
            continue
        # Fatal errors: access_denied, expired_token, invalid_grant, ...
        raise LoginError(f"device-flow failed: {err}: {resp.get('error_description', '')}")
    raise LoginError("device-flow timed out before user approved")


def save_token(token: CachedToken, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(token.to_dict(), indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows or other non-POSIX
    return path


def load_token(path: Path) -> CachedToken | None:
    if not path.is_file():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return CachedToken.from_dict(d)
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def config_from_env() -> DeviceFlowConfig:
    """Build DeviceFlowConfig from environment variables.

    Required: OIDC_ISSUER, OIDC_CLI_CLIENT_ID
    Optional: OIDC_PROJECT_ID, OIDC_EXTERNAL_HOST, OTAMAN_TOKEN_CACHE
    """
    issuer = os.environ.get("OIDC_ISSUER", "").strip()
    client_id = os.environ.get("OIDC_CLI_CLIENT_ID", "").strip()
    if not issuer or not client_id:
        raise LoginError(
            "OIDC config missing: set OIDC_ISSUER and OIDC_CLI_CLIENT_ID "
            "(values come from zitadel-bootstrap.py output)"
        )
    path = Path(os.environ.get("OTAMAN_TOKEN_CACHE", "")) if os.environ.get("OTAMAN_TOKEN_CACHE") else Path.home() / ".otaman" / "token.cache"
    return DeviceFlowConfig(
        issuer=issuer,
        client_id=client_id,
        project_id=os.environ.get("OIDC_PROJECT_ID", "").strip(),
        external_host=os.environ.get("OIDC_EXTERNAL_HOST") or None,
        token_path=path,
    )


def cmd_login(args: argparse.Namespace) -> int:
    try:
        cfg = config_from_env()
    except LoginError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # Reuse cached token if still valid and the caller didn't pass --force.
    if not args.force:
        cached = load_token(cfg.token_path)
        if cached and not cached.is_expired and cached.issuer == cfg.issuer:
            print(f"already logged in (token in {cfg.token_path})")
            return 0

    print(f"requesting device code from {cfg.issuer}...")
    try:
        device = initiate_device_flow(cfg)
    except LoginError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print()
    print("=" * 60)
    print(f"  Open this URL in your browser:")
    print(f"    {device['verification_uri_complete']}")
    print()
    print(f"  Or visit {device.get('verification_uri', '<see above>')}")
    print(f"  and enter code: {device['user_code']}")
    print("=" * 60)
    print()

    interval = float(device.get("interval", 5))
    expires_in = int(device.get("expires_in", 600))

    spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    spin_state = [0]

    def on_pending():
        sys.stderr.write(f"\rwaiting for browser confirmation {spinner[spin_state[0] % len(spinner)]}")
        sys.stderr.flush()
        spin_state[0] += 1

    try:
        token_resp = poll_for_token(
            cfg, device_code=device["device_code"],
            interval=interval, expires_in=expires_in,
            on_pending=on_pending,
        )
    except LoginError as e:
        sys.stderr.write("\n")
        print(f"error: {e}", file=sys.stderr)
        return 1

    sys.stderr.write("\r" + " " * 50 + "\r")  # clear spinner line

    expires_at = int(time.time() + int(token_resp.get("expires_in", 0))) if token_resp.get("expires_in") else 0
    cached = CachedToken(
        access_token=token_resp["access_token"],
        refresh_token=token_resp.get("refresh_token"),
        expires_at=expires_at,
        issuer=cfg.issuer,
        client_id=cfg.client_id,
    )
    path = save_token(cached, cfg.token_path)
    print(f"logged in. Token cached at {path}")
    return 0


def cmd_whoami_token(args: argparse.Namespace) -> int:
    """Show cached-token metadata (NOT the token itself)."""
    path = Path(args.token_path) if args.token_path else Path.home() / ".otaman" / "token.cache"
    cached = load_token(path)
    if cached is None:
        print(f"no token cached at {path}", file=sys.stderr)
        return 2
    expires_at_human = (
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cached.expires_at))
        if cached.expires_at else "(no expiry)"
    )
    print(f"token cache: {path}")
    print(f"  issuer:     {cached.issuer}")
    print(f"  client_id:  {cached.client_id}")
    print(f"  expires_at: {expires_at_human}")
    print(f"  expired:    {cached.is_expired}")
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    path = Path(args.token_path) if args.token_path else Path.home() / ".otaman" / "token.cache"
    if path.is_file():
        path.unlink()
        print(f"removed {path}")
    else:
        print(f"no token cached at {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for the standalone `otaman login` shim. Dispatched from main.py."""
    p = argparse.ArgumentParser(prog="otaman login")
    sub = p.add_subparsers(dest="auth_cmd", required=False)

    p_login = sub.add_parser("login", help="initiate device-auth flow")
    p_login.add_argument("--force", action="store_true", help="reauthenticate even if token cached")
    p_login.set_defaults(func=cmd_login)

    p_logout = sub.add_parser("logout", help="remove cached token")
    p_logout.add_argument("--token-path", help="override token cache path")
    p_logout.set_defaults(func=cmd_logout)

    p_show = sub.add_parser("show", help="print cached token metadata")
    p_show.add_argument("--token-path", help="override token cache path")
    p_show.set_defaults(func=cmd_whoami_token)

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        # Default = login
        args.force = False
        return cmd_login(args)
    return args.func(args)
