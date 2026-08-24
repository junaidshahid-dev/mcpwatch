"""A tiny, real MCP server over stdio — something to monitor out of the box.

It deliberately mixes a well-formed tool with two flawed ones (one missing a description, one
with an untyped parameter) so a liveness check produces an interesting, non-perfect grade.
Run a monitor against it with:  kind="stdio", endpoint="python scripts/demo_server.py"
"""
from __future__ import annotations

import json
import sys

TOOLS = [
    {
        "name": "ping",
        "description": "Health check. Returns 'pong'.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "add",
        "description": "Add two integers and return the sum.",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    },
    {
        # no description -> a schema-hygiene WARN
        "name": "lookup",
        "description": "",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {}},   # untyped param -> INFO
            "required": ["key"],
        },
    },
]


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _result(req_id, result) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, req_id = msg.get("method"), msg.get("id")

        if method == "initialize":
            _result(req_id, {"protocolVersion": "2024-11-05", "capabilities": {},
                             "serverInfo": {"name": "demo", "version": "1.0"}})
        elif method == "notifications/initialized":
            pass  # notification, no response
        elif method == "tools/list":
            _result(req_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params", {})
            name, args = params.get("name"), params.get("arguments", {})
            if name == "ping":
                _result(req_id, {"content": [{"type": "text", "text": "pong"}]})
            elif name == "add":
                try:
                    total = int(args["a"]) + int(args["b"])
                    _result(req_id, {"content": [{"type": "text", "text": str(total)}]})
                except (KeyError, TypeError, ValueError):
                    _result(req_id, {"isError": True,
                                     "content": [{"type": "text", "text": "invalid arguments"}]})
            elif name == "lookup":
                _result(req_id, {"content": [{"type": "text", "text": "not found"}]})
            else:
                _result(req_id, {"isError": True,
                                 "content": [{"type": "text", "text": f"unknown tool {name}"}]})
        elif req_id is not None:
            _send({"jsonrpc": "2.0", "id": req_id,
                   "error": {"code": -32601, "message": f"method not found: {method}"}})


if __name__ == "__main__":
    main()
