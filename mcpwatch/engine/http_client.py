"""A minimal MCP client over Streamable HTTP, hardened for monitoring untrusted targets.

mcp-probe ships a stdio client (it launches a server process). Production MCP servers are often
*remote* — reachable at an HTTPS endpoint over the Streamable HTTP transport. This client speaks
that transport and implements the exact `MCPClient` protocol mcp-probe expects (`list_tools` /
`call_tool`), so the same grading code runs unchanged over HTTP.

Hardening (see mcpwatch.security):
  * SSRF: the URL is validated up front, and a GuardedTransport re-validates DNS at connect time
    (anti-rebinding). Redirects are NOT followed, so a 3xx cannot escape the checks.
  * Timeouts: granular connect/read/write/pool limits, so a slow or hung server can't pin a
    worker.
  * Body cap: the response is read as a stream and aborted once it exceeds the limit — a
    malicious server cannot exhaust memory with a huge body.
"""
from __future__ import annotations

import json

import httpx

from mcp_probe.client import ToolError
from mcp_probe.model import ToolSpec

_PROTOCOL_VERSION = "2024-11-05"


class HttpMCPClient:
    """JSON-RPC-over-HTTP MCP client. Use as a context manager, like StdioMCPClient."""

    def __init__(self, url: str, timeout: float = 25.0, headers: dict | None = None,
                 max_response_bytes: int = 5_000_000):
        self.url = url
        self.timeout = timeout
        self._extra_headers = headers or {}
        self._max_bytes = max_response_bytes
        self._session_id: str | None = None
        self._id = 0
        self._http: httpx.Client | None = None
        self.protocol_version: str | None = None
        self.server_name: str | None = None
        self.server_version: str | None = None

    # -- lifecycle ------------------------------------------------------------
    def __enter__(self) -> "HttpMCPClient":
        from ..security import GuardedTransport, validate_http_target
        validate_http_target(self.url)                       # fail fast, before any connection
        timeout = httpx.Timeout(
            connect=min(10.0, self.timeout), read=self.timeout,
            write=min(10.0, self.timeout), pool=min(10.0, self.timeout))
        # follow_redirects=False so a 3xx can't bypass the SSRF checks; GuardedTransport
        # re-validates DNS at connect time (anti-rebinding).
        self._http = httpx.Client(timeout=timeout, follow_redirects=False,
                                  transport=GuardedTransport())
        init = self._rpc("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mcpwatch", "version": "1.0"},
        })
        self.protocol_version = init.get("protocolVersion")
        info = init.get("serverInfo") or {}
        self.server_name = info.get("name")
        self.server_version = info.get("version")
        self._notify("notifications/initialized", {})
        return self

    def __exit__(self, *exc) -> None:
        if self._http is not None:
            self._http.close()

    # -- transport ------------------------------------------------------------
    def _headers(self) -> dict:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self._extra_headers,
        }
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    def _read_capped(self, resp: httpx.Response) -> bytes:
        """Read a streamed response, aborting if it exceeds the byte cap."""
        total, chunks = 0, []
        for chunk in resp.iter_bytes():
            total += len(chunk)
            if total > self._max_bytes:
                raise ToolError(f"server response exceeds {self._max_bytes} bytes; refusing to read")
            chunks.append(chunk)
        return b"".join(chunks)

    def _notify(self, method: str, params: dict) -> None:
        # Notifications carry no id; drain (capped) and discard the acknowledgement.
        assert self._http is not None
        with self._http.stream("POST", self.url, headers=self._headers(),
                               json={"jsonrpc": "2.0", "method": method, "params": params}) as resp:
            self._read_capped(resp)

    def _rpc(self, method: str, params: dict) -> dict:
        assert self._http is not None
        self._id += 1
        req_id = self._id
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        with self._http.stream("POST", self.url, headers=self._headers(), json=payload) as resp:
            sid = resp.headers.get("mcp-session-id") or resp.headers.get("Mcp-Session-Id")
            if sid:
                self._session_id = sid
            status = resp.status_code
            ctype = resp.headers.get("content-type", "")
            body = self._read_capped(resp)
        if status >= 400:
            raise ToolError(f"HTTP {status} from server: {body[:200]!r}")
        msg = self._parse(body, ctype, req_id)
        if "error" in msg:
            raise ToolError(json.dumps(msg["error"]))
        return msg.get("result", {})

    def _parse(self, body: bytes, ctype: str, req_id: int) -> dict:
        """Return the JSON-RPC message matching req_id, from a JSON or SSE body."""
        text = body.decode("utf-8", errors="replace")
        if "text/event-stream" in ctype:
            for obj in _iter_sse_json(text):
                if obj.get("id") == req_id or "error" in obj:
                    return obj
            raise ToolError("no JSON-RPC response in SSE stream")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            raise ToolError(f"server sent non-JSON body ({e}): {text[:200]!r}") from None
        if isinstance(parsed, list):
            for obj in parsed:
                if obj.get("id") == req_id or "error" in obj:
                    return obj
            raise ToolError("no JSON-RPC response in batch")
        return parsed

    # -- MCPClient protocol ---------------------------------------------------
    def list_tools(self) -> list[ToolSpec]:
        res = self._rpc("tools/list", {})
        return [ToolSpec(t["name"], t.get("description", ""),
                         t.get("inputSchema", t.get("input_schema", {})),
                         t.get("execution", {}) or {})
                for t in res.get("tools", [])]

    def call_tool(self, name: str, args: dict) -> dict:
        res = self._rpc("tools/call", {"name": name, "arguments": args})
        if isinstance(res, dict) and res.get("isError"):
            raise ToolError(json.dumps(res))
        return res


def _iter_sse_json(text: str):
    """Yield each JSON object carried on an SSE `data:` line."""
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if not data or data == "[DONE]":
            continue
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue
