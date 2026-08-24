"""Named rate limiters and the per-tenant probe concurrency guard.

In-process (single-instance) implementations. For a multi-instance deployment, back the rate
limiters with Redis and move the probe guard into the worker tier; the call sites stay the same.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

from .auth import RateLimited, RateLimiter
from .config import SETTINGS

signup_limiter = RateLimiter(*SETTINGS.rl_signup)
monitor_limiter = RateLimiter(*SETTINGS.rl_monitor)
probe_limiter = RateLimiter(*SETTINGS.rl_probe)
api_limiter = RateLimiter(*SETTINGS.rl_api)
webhook_limiter = RateLimiter(*SETTINGS.rl_webhook)


class TenantProbeGuard:
    """Cap concurrent probes per tenant so one org can't starve the workers (or be used to
    amplify traffic at a third party). Raises TooManyProbes when a tenant is at its ceiling."""

    def __init__(self, per_tenant: int):
        self.per_tenant = per_tenant
        self._active: dict[str, int] = {}
        self._lock = threading.Lock()

    @contextmanager
    def slot(self, org_id: str):
        with self._lock:
            n = self._active.get(org_id, 0)
            if n >= self.per_tenant:
                raise TooManyProbes(f"tenant is already running {n} concurrent probes")
            self._active[org_id] = n + 1
        try:
            yield
        finally:
            with self._lock:
                self._active[org_id] = max(0, self._active.get(org_id, 1) - 1)


class TooManyProbes(Exception):
    status = 429


probe_guard = TenantProbeGuard(SETTINGS.max_probes_per_tenant)


def enforce(limiter: RateLimiter, key: str) -> None:
    """Raise RateLimited if the key is over its limit (mapped to HTTP 429 in the API)."""
    limiter.check(key)


__all__ = ["signup_limiter", "monitor_limiter", "probe_limiter", "api_limiter",
           "webhook_limiter", "probe_guard", "TooManyProbes", "RateLimited", "enforce"]
