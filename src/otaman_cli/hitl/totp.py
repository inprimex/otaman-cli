"""RFC 6238 TOTP primitives for the HITL TOTP confirmation adapter.

hitl-confirmation-adapters 1.2 (verify core). Pure, dependency-free
implementation of secret generation, code computation, verification with
a configurable drift window, and otpauth:// URI construction —
compatible with any standard authenticator (Google/Microsoft/etc.).

Correctness is pinned to the RFC 6238 Appendix B published test vectors
(see tests). The verifier uses a constant-time compare and defaults to a
±1-step drift window, per the task. Deliberately no third-party dep
(hmac/hashlib/base64/struct only) — TOTP enrollment is CLI-local and must
work in Mode 1 with no server and no optional packages.

Storage of the enrolled secret and the QR rendering are intentionally NOT
here — they depend on the enrollment-config model (see the bus question
to core-agent re: totp_secret_ref vs the design's `totp: {enrolled}`).
This module is the correctness core both will consume.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets as _sysrandom
import struct
import time
import urllib.parse

DEFAULT_DIGITS = 6
DEFAULT_PERIOD = 30  # seconds per step
DEFAULT_ALGORITHM = "SHA1"  # what stock authenticator apps assume
DEFAULT_SECRET_BYTES = 20  # 160-bit — RFC 6238 recommendation for SHA1
DEFAULT_WINDOW = 1  # ±1 step of clock drift, per task 1.2

_ALGORITHMS = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}


def generate_secret(num_bytes: int = DEFAULT_SECRET_BYTES) -> str:
    """Return a fresh base32 secret (RFC 4648, unpadded) from a CSPRNG."""
    return base64.b32encode(_sysrandom.token_bytes(num_bytes)).decode("ascii").rstrip("=")


def _b32_key(secret_b32: str) -> bytes:
    """Decode a (possibly unpadded, spaced, lowercased) base32 secret."""
    s = secret_b32.replace(" ", "").upper()
    s += "=" * ((8 - len(s) % 8) % 8)
    return base64.b32decode(s, casefold=True)


def _hotp(secret_b32: str, counter: int, *, digits: int, algorithm: str) -> str:
    """HOTP (RFC 4226) — the per-counter building block of TOTP."""
    key = _b32_key(secret_b32)
    digestmod = _ALGORITHMS.get(algorithm.upper())
    if digestmod is None:
        raise ValueError(f"unsupported TOTP algorithm: {algorithm!r}")
    mac = hmac.new(key, struct.pack(">Q", counter), digestmod).digest()
    offset = mac[-1] & 0x0F
    truncated = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**digits)).zfill(digits)


def totp_now(
    secret_b32: str,
    *,
    timestamp: float | None = None,
    period: int = DEFAULT_PERIOD,
    digits: int = DEFAULT_DIGITS,
    algorithm: str = DEFAULT_ALGORITHM,
) -> str:
    """Compute the current TOTP code for *secret_b32*."""
    ts = int(timestamp if timestamp is not None else time.time())
    return _hotp(secret_b32, ts // period, digits=digits, algorithm=algorithm)


def verify_totp(
    secret_b32: str,
    code: str,
    *,
    timestamp: float | None = None,
    period: int = DEFAULT_PERIOD,
    digits: int = DEFAULT_DIGITS,
    algorithm: str = DEFAULT_ALGORITHM,
    window: int = DEFAULT_WINDOW,
) -> bool:
    """True if *code* is valid for *secret_b32* within ±*window* steps.

    Non-digit / wrong-length input refuses without a crypto compare.
    Uses a constant-time compare across the accepted step window.
    """
    if not isinstance(code, str):
        return False
    code = code.strip()
    if not code.isdigit() or len(code) != digits:
        return False
    ts = int(timestamp if timestamp is not None else time.time())
    counter = ts // period
    ok = False
    # Compare against every step in the window WITHOUT early-return, so the
    # timing does not leak which step (if any) matched.
    for step in range(-window, window + 1):
        candidate = _hotp(secret_b32, counter + step, digits=digits, algorithm=algorithm)
        if hmac.compare_digest(candidate, code):
            ok = True
    return ok


def otpauth_uri(
    secret_b32: str,
    *,
    account: str,
    issuer: str = "Otaman",
    digits: int = DEFAULT_DIGITS,
    period: int = DEFAULT_PERIOD,
    algorithm: str = DEFAULT_ALGORITHM,
) -> str:
    """Build a standard ``otpauth://totp/...`` provisioning URI.

    Scannable/pasteable into any authenticator app. The label is
    ``issuer:account`` and issuer is repeated as a param (Google Authenticator
    convention) so the account shows a clear provider name.
    """
    label = urllib.parse.quote(f"{issuer}:{account}", safe="")
    params = urllib.parse.urlencode(
        {
            "secret": secret_b32,
            "issuer": issuer,
            "algorithm": algorithm,
            "digits": digits,
            "period": period,
        }
    )
    return f"otpauth://totp/{label}?{params}"


__all__ = [
    "DEFAULT_DIGITS",
    "DEFAULT_PERIOD",
    "DEFAULT_WINDOW",
    "generate_secret",
    "otpauth_uri",
    "totp_now",
    "verify_totp",
]
