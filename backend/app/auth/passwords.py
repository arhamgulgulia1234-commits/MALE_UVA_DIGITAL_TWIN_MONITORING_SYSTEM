"""Password hashing — PBKDF2-HMAC-SHA256, stdlib only (no bcrypt/passlib dependency).

OWASP's current minimum for PBKDF2-SHA256 is ~600k iterations; 260k is the Django
reference value and plenty for a demo-scale user table, so it stays as documented
in docs/deployment-roadmap.md rather than raised silently later.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

_ITERATIONS = 260_000
_SCHEME = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"{_SCHEME}${_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations_s, salt_b64, hash_b64 = encoded.split("$")
        if scheme != _SCHEME:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations_s))
    return hmac.compare_digest(dk, expected)
