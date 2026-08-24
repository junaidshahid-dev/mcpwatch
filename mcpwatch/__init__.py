"""MCPWatch — uptime and schema-health monitoring for MCP servers.

A scheduled monitor connects to each registered MCP server, measures reachability and
latency, and grades the server's advertised tool schemas (0-100, A-F) using the mcp-probe
engine. It records every check, and alerts the owner when a server goes down or its grade
regresses. A public status badge turns every monitored server into a growth loop.
"""
__version__ = "0.1.0"
