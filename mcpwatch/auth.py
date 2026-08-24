"""Authentication & account bootstrap.

Signup creates the whole tenant spine in one shot: a user, their default organization (with an
owner membership), and a default project. Passwords are scrypt-hashed; sessions are random
tokens stored only as hashes. Auth endpoints are rate-limited by a sliding window.
"""
from __future__ import annotations

import threading
import time

from . import crypto, db
from .config import SETTINGS


class AuthError(Exception):
    """Base auth failure. `status` maps to an HTTP code in the API layer."""
    status = 400


class InvalidCredentials(AuthError):
    status = 401


class EmailExists(AuthError):
    status = 409


class RateLimited(AuthError):
    status = 429


# --------------------------------------------------------------------------- rate limiting
class RateLimiter:
    """In-process sliding-window limiter. For multi-instance deploys, back this with Redis."""

    def __init__(self, max_events: int, window_seconds: int):
        self.max = max_events
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.time()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < self.window]
            if len(hits) >= self.max:
                raise RateLimited("too many attempts; please wait and try again")
            hits.append(now)
            self._hits[key] = hits


auth_limiter = RateLimiter(SETTINGS.auth_max_attempts, SETTINGS.auth_window_seconds)


# --------------------------------------------------------------------------- signup / login
def signup(email: str, password: str, ip: str | None = None) -> dict:
    """Create user + default org + default project. Returns dict with user, org, verify_token."""
    email = (email or "").lower().strip()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise AuthError("enter a valid email address")
    if db.get_user_by_email(email):
        raise EmailExists("an account with that email already exists")
    pw_hash = crypto.hash_password(password)  # raises ValueError if too short

    verify_token = crypto.new_token()
    user = db.create_user(email, pw_hash, crypto.hash_token(verify_token))
    org = db.create_org(email.split("@")[0] + "'s org", user["id"])
    db.create_project(org["id"], "Default")
    # A default email alert rule so paid plans get notified without any setup (no-op on Free,
    # where the plan has alerts disabled).
    db.create_alert_rule(org["id"], "email", email,
                         on_down=True, on_recover=True, on_breaking_change=True)
    db.audit("auth.signup", org_id=org["id"], user_id=user["id"], ip=ip)
    return {"user": user, "org": org, "verify_token": verify_token}


def login(email: str, password: str, ip: str | None = None) -> dict:
    """Verify credentials and open a session. Returns {user, session_token}."""
    key = f"login:{ip}:{(email or '').lower().strip()}"
    auth_limiter.check(key)
    user = db.get_user_by_email(email)
    if not user or not crypto.verify_password(password, user["password_hash"]):
        raise InvalidCredentials("email or password is incorrect")
    token = crypto.new_token()
    db.create_session(user["id"], crypto.hash_token(token), SETTINGS.session_ttl_seconds)
    db.audit("auth.login", user_id=user["id"], ip=ip)
    return {"user": user, "session_token": token}


def logout(session_token: str) -> None:
    if session_token:
        db.delete_session(crypto.hash_token(session_token))


def user_from_session(session_token: str) -> dict | None:
    if not session_token:
        return None
    return db.get_session_user(crypto.hash_token(session_token))


# --------------------------------------------------------------------------- email verify / reset
def verify_email(token: str) -> bool:
    user = db.get_user_by_verify_token(crypto.hash_token(token))
    if not user:
        return False
    db.mark_email_verified(user["id"])
    return True


def request_password_reset(email: str) -> str | None:
    """Returns a reset token to email the user, or None if no such account (don't reveal which)."""
    user = db.get_user_by_email(email)
    if not user:
        return None
    token = crypto.new_token()
    db.set_reset_token(user["id"], crypto.hash_token(token), time.time() + 3600)
    return token


def reset_password(token: str, new_password: str) -> bool:
    user = db.get_user_by_reset_token(crypto.hash_token(token))
    if not user:
        return False
    db.update_password(user["id"], crypto.hash_password(new_password))
    db.audit("auth.password_reset", user_id=user["id"])
    return True


# --------------------------------------------------------------------------- context helpers
def primary_org(user: dict) -> dict:
    orgs = db.orgs_for_user(user["id"])
    return orgs[0]


def default_project(org_id: str) -> dict:
    projects = db.list_projects(org_id)
    return projects[0] if projects else db.create_project(org_id, "Default")


def api_key_context(plaintext: str) -> dict | None:
    """Resolve an API key to its {user, org, key}. Updates last-used. None if invalid."""
    if not plaintext or not plaintext.startswith("mcpw_"):
        return None
    row = db.get_api_key_by_hash(crypto.hash_token(plaintext))
    if not row:
        return None
    user = db.get_user(row["user_id"])
    org = db.get_org(row["org_id"])
    if not user or not org:
        return None
    return {"user": user, "org": org, "key": row}
