"""The grading engine — MCPWatch's moat.

MCPWatch does not re-implement MCP schema analysis; it wraps `mcp-probe`, a separately
maintained auditing tool, as its scoring engine. This package adapts mcp-probe into the two
check depths a monitoring product needs:

  * liveness  — connect, list tools, statically grade the advertised schemas. Sends NO
                adversarial traffic, so it is safe to run on a schedule against any server.
  * audit     — the full adversarial fuzz. Sends hundreds of malformed payloads, so it is
                on-demand only and gated to servers the operator owns.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ..config import SETTINGS


def ensure_engine_importable() -> None:
    """Make `mcp_probe` importable. No-op once it is pip-installed."""
    try:
        import mcp_probe  # noqa: F401
        return
    except ImportError:
        pass
    path = Path(SETTINGS.mcp_probe_path)
    if path.exists() and str(path) not in sys.path:
        sys.path.insert(0, str(path))


ensure_engine_importable()
