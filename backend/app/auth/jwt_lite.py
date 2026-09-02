"""Minimal HS256 JWT encode/decode — stdlib only (no PyJWT dependency).

Standard three-segment `base64url(header).base64url(payload).base64url(hmac_sha256)`
construction (RFC 7519 / RFC 7515 HS256). Deliberately small: this project needs to
issue and verify one token shape with one algorithm, not a general JOSE library.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

_HEADER = {"alg": "HS256", "typ": "JWT"}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def encode(claims: dict, secret: str, expires_in_s: float) -> str:
    now = int(time.time())
    payload = {**claims, "iat": now, "exp": now + int(expires_in_s)}
    header_seg = _b64url(json.dumps(_HEADER, separators=(",", ":")).encode())
    payload_seg = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_seg}.{payload_seg}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header_seg}.{payload_seg}.{_b64url(sig)}"


def decode(token: str, secret: str) -> dict | None:
    """Returns the claims, or None if the signature is invalid, malformed, or expired."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_seg, payload_seg, sig_seg = parts
    expected_sig = hmac.new(secret.encode(), f"{header_seg}.{payload_seg}".encode(), hashlib.sha256).digest()
    try:
        actual_sig = _b64url_decode(sig_seg)
    except Exception:
        return None
    if not hmac.compare_digest(expected_sig, actual_sig):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_seg))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload
