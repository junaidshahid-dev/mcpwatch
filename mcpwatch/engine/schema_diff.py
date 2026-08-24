"""Schema-diff engine — detect and classify changes in an MCP server's tool surface.

For each check we snapshot the server's advertised tools in a normalised form and hash it. When
the hash changes, we diff the new surface against the previous snapshot and classify the change:

  * breaking (🔴)             — will break existing agents: a tool removed, a required param
                                added, or a param's type changed.
  * potentially_breaking (🟡) — may break some callers: a param removed, or an optional param
                                made required.
  * non_breaking (🟢)         — safe: a new tool, a new optional param, or a description change.

The consumer of an MCP tool is an agent that generates calls from the schema, so "breaking" is
defined from that caller's point of view.
"""
from __future__ import annotations

import hashlib
import json

Severity = str  # "breaking" | "potentially_breaking" | "non_breaking"
_RANK = {"non_breaking": 0, "potentially_breaking": 1, "breaking": 2}


def _as_dict(tool) -> dict:
    """Accept a mcp-probe ToolSpec or a plain dict; return name/description/input_schema."""
    if isinstance(tool, dict):
        return {
            "name": tool.get("name", ""),
            "description": tool.get("description", "") or "",
            "input_schema": tool.get("input_schema") or tool.get("inputSchema") or {},
        }
    return {
        "name": getattr(tool, "name", ""),
        "description": getattr(tool, "description", "") or "",
        "input_schema": getattr(tool, "input_schema", {}) or {},
    }


def normalize_tools(tools) -> list[dict]:
    """Canonical, comparable representation of a tool surface (order-independent)."""
    out = []
    for t in tools:
        d = _as_dict(t)
        schema = d["input_schema"] or {}
        props = schema.get("properties", {}) or {}
        required = set(schema.get("required", []) or [])
        params = {
            name: {"type": (p or {}).get("type"), "required": name in required}
            for name, p in props.items()
        }
        out.append({"name": d["name"], "description": d["description"], "params": params})
    out.sort(key=lambda x: x["name"])
    return out


def schema_hash(tools) -> str:
    """Stable hash of the normalised tool surface. Description changes DO move the hash."""
    norm = tools if _looks_normalized(tools) else normalize_tools(tools)
    blob = json.dumps(norm, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _looks_normalized(tools) -> bool:
    return bool(tools) and isinstance(tools[0], dict) and "params" in tools[0]


def diff_tools(old: list[dict], new: list[dict]) -> tuple[dict, Severity, bool]:
    """Diff two normalised surfaces. Returns (diff, overall_severity, has_changes)."""
    old_by = {t["name"]: t for t in old}
    new_by = {t["name"]: t for t in new}

    added = sorted(set(new_by) - set(old_by))
    removed = sorted(set(old_by) - set(new_by))
    changed: dict[str, dict] = {}
    severity = "non_breaking"

    def bump(level: Severity):
        nonlocal severity
        if _RANK[level] > _RANK[severity]:
            severity = level

    if removed:
        bump("breaking")  # a removed tool breaks any agent that used it

    for name in sorted(set(old_by) & set(new_by)):
        o, n = old_by[name], new_by[name]
        op, np_ = o["params"], n["params"]
        added_p = {k: np_[k] for k in np_ if k not in op}
        removed_p = {k: op[k] for k in op if k not in np_}
        changed_p: dict[str, dict] = {}

        for k in set(op) & set(np_):
            if op[k]["type"] != np_[k]["type"]:
                changed_p[k] = {"kind": "type", "from": op[k]["type"], "to": np_[k]["type"]}
                bump("breaking")
            elif op[k]["required"] != np_[k]["required"]:
                made_required = np_[k]["required"] and not op[k]["required"]
                changed_p[k] = {"kind": "required", "from": op[k]["required"], "to": np_[k]["required"]}
                bump("potentially_breaking" if made_required else "non_breaking")

        for k, meta in added_p.items():
            bump("breaking" if meta["required"] else "non_breaking")
        if removed_p:
            bump("potentially_breaking")

        desc_changed = o["description"] != n["description"]
        if added_p or removed_p or changed_p or desc_changed:
            changed[name] = {
                "added_params": added_p,
                "removed_params": removed_p,
                "changed_params": changed_p,
                "description_changed": desc_changed,
            }

    has_changes = bool(added or removed or changed)
    diff = {"added_tools": added, "removed_tools": removed, "changed_tools": changed}
    return diff, severity, has_changes


def render_text(diff: dict) -> str:
    """Human-readable summary, in the style shown to the customer in alerts."""
    lines = ["Schema change detected", ""]
    for name in diff.get("added_tools", []):
        lines.append(f"ADDED tool: {name}")
    for name in diff.get("removed_tools", []):
        lines.append(f"REMOVED tool: {name}")
    for name, ch in diff.get("changed_tools", {}).items():
        lines.append(f"tool: {name}")
        for p, meta in ch["removed_params"].items():
            lines.append(f"  REMOVED: {p}")
        for p, meta in ch["changed_params"].items():
            if meta["kind"] == "type":
                lines.append(f"  CHANGED: {p}: {meta['from']} → {meta['to']}")
            else:
                lines.append(f"  CHANGED: {p}: required {meta['from']} → {meta['to']}")
        for p, meta in ch["added_params"].items():
            req = " (required)" if meta["required"] else ""
            lines.append(f"  ADDED: {p}: {meta['type']}{req}")
        if ch["description_changed"]:
            lines.append("  description changed")
        lines.append("")
    return "\n".join(lines).strip()
