"""Cryptographic helpers — password hashing, token hashing, API-key generation.

No third-party crypto dependency: password hashing uses the stdlib `hashlib.scrypt` (a
memory-hard KDF), with a per-password random salt and a versioned, self-describing string so
parameters can change later without breaking existing hashes.

Secrets that are looked up (session tokens, API keys) are stored only as SHA-256 hashes; the
plaintext is shown to the user once and never persisted.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

# scrypt parameters (OWASP-ish for interactive logins). Encoded into the hash so they can be
# tuned per-hash later.
_N, _R, _P, _DKLEN = 2 ** 14, 8, 1, 32


def hash_password(password: str) -> str:
    if not password or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, dk_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                            n=int(n), r=int(r), p=int(p), dklen=len(dk_hex) // 2)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, AttributeError):
        return False


def new_token(nbytes: int = 32) -> str:
    """A random opaque token (for sessions / verification / reset)."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Deterministic hash for looking a token up in the DB."""
    return hashlib.sha256(token.encode()).hexdigest()


def new_api_key(live: bool = True) -> tuple[str, str, str]:
    """Return (plaintext, prefix, hash). Plaintext is shown once; only the hash is stored.

    Format: mcpw_live_<random>. The prefix (first 12 chars) is stored in the clear so a key can
    be identified in listings without revealing it.
    """
    env = "live" if live else "test"
    secret = secrets.token_urlsafe(24)
    plaintext = f"mcpw_{env}_{secret}"
    return plaintext, plaintext[:16], hash_token(plaintext)
