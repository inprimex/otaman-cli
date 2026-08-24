"""hitl-confirmation-adapters 1.2 — RFC 6238 TOTP verify core.

Correctness is pinned to the RFC 6238 Appendix B published test vectors
(SHA1 seed "12345678901234567890"), which is the standard's own proof of
a conforming implementation. Everything else (drift window, otpauth URI,
input hardening) rides on top of that.
"""

from __future__ import annotations

import base64

import pytest

from otaman_cli.hitl.totp import (
    generate_secret,
    otpauth_uri,
    totp_now,
    verify_totp,
)

# RFC 6238 Appendix B: ASCII seed "12345678901234567890" (20 bytes) as base32.
_RFC_SECRET = base64.b32encode(b"12345678901234567890").decode("ascii")

# (unix_time, 8-digit TOTP) — the exact SHA1 vectors from the RFC table.
_RFC_VECTORS_8 = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]


@pytest.mark.parametrize("ts,expected8", _RFC_VECTORS_8)
def test_rfc6238_vectors_8_digit(ts, expected8):
    assert totp_now(_RFC_SECRET, timestamp=ts, digits=8) == expected8


@pytest.mark.parametrize("ts,expected8", _RFC_VECTORS_8)
def test_rfc6238_vectors_6_digit_is_low_six(ts, expected8):
    # A 6-digit code is the low six digits of the 8-digit code.
    assert totp_now(_RFC_SECRET, timestamp=ts, digits=6) == expected8[-6:]


@pytest.mark.parametrize("ts,expected8", _RFC_VECTORS_8)
def test_verify_accepts_current_code(ts, expected8):
    assert verify_totp(_RFC_SECRET, expected8, timestamp=ts, digits=8, window=0) is True


# ---------------------------------------------------------------------------
# Drift window ±1 step (30s)


def test_verify_accepts_previous_step_within_window():
    ts = 1234567890
    prev = totp_now(_RFC_SECRET, timestamp=ts - 30, digits=6)
    assert verify_totp(_RFC_SECRET, prev, timestamp=ts, digits=6, window=1) is True


def test_verify_accepts_next_step_within_window():
    ts = 1234567890
    nxt = totp_now(_RFC_SECRET, timestamp=ts + 30, digits=6)
    assert verify_totp(_RFC_SECRET, nxt, timestamp=ts, digits=6, window=1) is True


def test_verify_rejects_two_steps_away():
    ts = 1234567890
    far = totp_now(_RFC_SECRET, timestamp=ts + 60, digits=6)
    assert verify_totp(_RFC_SECRET, far, timestamp=ts, digits=6, window=1) is False


def test_verify_window_zero_rejects_adjacent_step():
    ts = 1234567890
    prev = totp_now(_RFC_SECRET, timestamp=ts - 30, digits=6)
    assert verify_totp(_RFC_SECRET, prev, timestamp=ts, digits=6, window=0) is False


# ---------------------------------------------------------------------------
# Input hardening — no crypto compare on malformed input


@pytest.mark.parametrize("bad", ["", "   ", "12ab56", "abcdef", "1234567", "12345", "12345\n6"])
def test_verify_rejects_malformed_code(bad):
    assert verify_totp(_RFC_SECRET, bad, timestamp=59, digits=6) is False


def test_verify_rejects_non_string():
    assert verify_totp(_RFC_SECRET, 287082, timestamp=59, digits=6) is False  # type: ignore[arg-type]


def test_verify_rejects_wrong_code():
    assert verify_totp(_RFC_SECRET, "000000", timestamp=59, digits=6) is False


def test_verify_tolerates_spaces_and_lowercase_secret():
    # Authenticator apps present secrets spaced/lowercased; decoding must cope.
    spaced = " ".join(_RFC_SECRET[i : i + 4] for i in range(0, len(_RFC_SECRET), 4)).lower()
    code = totp_now(spaced, timestamp=59, digits=6)
    assert verify_totp(spaced, code, timestamp=59, digits=6) is True


# ---------------------------------------------------------------------------
# Secret generation + otpauth URI


def test_generate_secret_is_decodable_base32_and_unique():
    a, b = generate_secret(), generate_secret()
    assert a != b
    # Round-trips through the verifier (decodable, usable).
    code = totp_now(a, timestamp=59)
    assert verify_totp(a, code, timestamp=59) is True


def test_generate_secret_length_reflects_bytes():
    # 20 bytes → 32 base32 chars (unpadded), 160-bit.
    assert len(generate_secret(20)) == 32


def test_otpauth_uri_shape():
    uri = otpauth_uri(_RFC_SECRET, account="roman@otaman.ai", issuer="Otaman")
    assert uri.startswith("otpauth://totp/Otaman%3Aroman%40otaman.ai?")
    assert f"secret={_RFC_SECRET}" in uri
    assert "issuer=Otaman" in uri
    assert "algorithm=SHA1" in uri
    assert "digits=6" in uri
    assert "period=30" in uri
