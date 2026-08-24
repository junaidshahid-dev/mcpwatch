"""Turn a monitor target into a graded, storable outcome.

The single seam between MCPWatch and the mcp-probe engine. Everything above it (service, API,
alerts) speaks `ProbeOutcome` and never imports mcp-probe directly.
"""
from __future__ import annotations

import shlex
import time
from dataclasses import dataclass, field

from . import ensure_engine_importable

ensure_engine_importable()

from mcp_probe.client import StdioMCPClient  # noqa: E402
from mcp_probe.model import Report, Severity  # noqa: E402
from mcp_probe.probe import (  # noqa: E402
    RemoteBackendDetected,
    _schema_hygiene,
    probe_server,
)

from . import schema_diff  # noqa: E402
from .http_client import HttpMCPClient  # noqa: E402

HEALTHY_SCORE = 75
MAX_STORED_FINDINGS = 40


@dataclass
class ProbeOutcome:
    """The result of one check — the unit MCPWatch stores, diffs, and alerts on."""
    reachable: bool
    depth: str                      # "liveness" | "audit"
    label: str
    score: int | None = None
    grade: str | None = None
    tool_count: int | None = None
    schema_hash: str | None = None
    protocol_version: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    latency_ms: int | None = None
    check_duration_ms: int | None = None
    counts: dict = field(default_factory=dict)
    findings: list[dict] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)   # normalised, for schema snapshots
    remote_blocked: bool = False
    error: str | None = None

    def status(self) -> str:
        if not self.reachable:
            return "down"
        if self.score is not None and self.score < HEALTHY_SCORE:
            return "degraded"
        return "up"

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["status"] = self.status()
        return d


def _report_to_outcome(report: Report, depth: str, latency_ms: int, client) -> ProbeOutcome:
    findings = [
        {"tool": f.tool, "check": f.check, "severity": f.severity.value, "message": f.message}
        for f in report.findings if f.severity in (Severity.WARN, Severity.FAIL)
    ][:MAX_STORED_FINDINGS]
    tools = schema_diff.normalize_tools(report.tools)
    return ProbeOutcome(
        reachable=True,
        depth=depth,
        label=report.server,
        score=report.score(),
        grade=report.grade(),
        tool_count=len(report.tools),
        schema_hash=schema_diff.schema_hash(tools),
        protocol_version=getattr(client, "protocol_version", None),
        server_name=getattr(client, "server_name", None),
        server_version=getattr(client, "server_version", None),
        latency_ms=latency_ms,
        counts=report.counts(),
        findings=findings,
        tools=tools,
    )


def _liveness(client, label: str, latency_ms: int) -> ProbeOutcome:
    """Connect + list tools + static schema hygiene. No tool calls."""
    tools = client.list_tools()
    report = Report(server=label, tools=tools)
    for spec in tools:
        _schema_hygiene(report, spec)
    return _report_to_outcome(report, "liveness", latency_ms, client)


def probe_target(
    kind: str,
    endpoint: str,
    *,
    depth: str = "liveness",
    label: str | None = None,
    timeout: float = 25.0,
    allow_remote: bool = False,
    headers: dict | None = None,
    max_response_bytes: int = 5_000_000,
) -> ProbeOutcome:
    """Run one check against a target.

    kind:  "stdio" (endpoint is a launch command) or "http" (endpoint is a URL).
    depth: "liveness" (safe, scheduled) or "audit" (adversarial, on-demand, own servers).
    """
    label = label or endpoint

    def _factory():
        if kind == "http":
            return HttpMCPClient(endpoint, timeout=timeout, headers=headers,
                                 max_response_bytes=max_response_bytes)
        if kind == "stdio":
            return StdioMCPClient(shlex.split(endpoint), timeout=timeout)
        raise ValueError(f"unknown monitor kind: {kind!r}")

    started = time.monotonic()
    try:
        with _factory() as client:
            t0 = time.monotonic()
            if depth == "audit":
                try:
                    report = probe_server(client, label, allow_remote=allow_remote)
                except RemoteBackendDetected as e:
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    out = _liveness(client, label, latency_ms)
                    out.remote_blocked = True
                    out.error = f"audit skipped: {e}"
                    out.check_duration_ms = int((time.monotonic() - started) * 1000)
                    return out
                latency_ms = int((time.monotonic() - t0) * 1000)
                out = _report_to_outcome(report, "audit", latency_ms, client)
                out.check_duration_ms = int((time.monotonic() - started) * 1000)
                return out
            out = _liveness(client, label, 0)
            out.latency_ms = int((time.monotonic() - t0) * 1000)
            out.check_duration_ms = int((time.monotonic() - started) * 1000)
            return out
    except Exception as e:  # transport/connection failure (incl. ToolError) => unreachable
        return ProbeOutcome(
            reachable=False, depth=depth, label=label,
            latency_ms=int((time.monotonic() - started) * 1000),
            check_duration_ms=int((time.monotonic() - started) * 1000),
            error=_short(e),
        )


def _short(e: Exception, limit: int = 300) -> str:
    from ..security import scrub_text
    msg = str(e).strip() or repr(e)
    return scrub_text(msg)[:limit]
