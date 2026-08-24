"""Observability for MCPWatch itself — you need to know when your monitor is the thing that's down.

Provides: structured logging with a per-request id, a request-id middleware that also counts
traffic, an in-process metrics registry rendered in Prometheus text format, and a readiness
probe that actually touches the database and the grading engine.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"))
    handler.addFilter(_RequestIdFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


# --------------------------------------------------------------------------- metrics registry
class Metrics:
    def __init__(self):
        self._c: dict[str, float] = {}
        self._lock = threading.Lock()
        self.started_at = time.time()

    def inc(self, name: str, by: float = 1.0, **labels) -> None:
        key = name + _labels(labels)
        with self._lock:
            self._c[key] = self._c.get(key, 0) + by

    def render(self) -> str:
        lines = [f'mcpwatch_uptime_seconds {time.time() - self.started_at:.0f}']
        with self._lock:
            for key, val in sorted(self._c.items()):
                lines.append(f"{key} {val:g}")
        return "\n".join(lines) + "\n"


def _labels(labels: dict) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return "{" + inner + "}"


METRICS = Metrics()


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        start = time.time()
        try:
            response = await call_next(request)
        except Exception:
            METRICS.inc("mcpwatch_requests_total", route=_route(request), status="500")
            raise
        finally:
            request_id_var.reset(token)
        METRICS.inc("mcpwatch_requests_total", route=_route(request), status=str(response.status_code))
        METRICS.inc("mcpwatch_request_seconds_sum", time.time() - start)
        response.headers["x-request-id"] = rid
        return response


def _route(request) -> str:
    # Collapse ids so the metric doesn't explode into unbounded label cardinality.
    parts = []
    for seg in request.url.path.split("/"):
        parts.append(":id" if ("_" in seg or len(seg) > 24) else seg)
    return "/".join(parts) or "/"


# --------------------------------------------------------------------------- readiness
def readiness() -> tuple[bool, dict]:
    checks = {}
    try:
        from . import db
        with db.connect() as conn:
            conn.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
    try:
        from .engine import ensure_engine_importable
        ensure_engine_importable()
        import mcp_probe  # noqa: F401
        checks["engine"] = "ok"
    except Exception as e:
        checks["engine"] = f"error: {e}"
    ok = all(v == "ok" for v in checks.values())
    return ok, checks
