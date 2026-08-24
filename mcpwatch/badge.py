"""Public status badge — an embeddable SVG, shields.io style.

Every monitored server gets a badge its owner can drop into a README. That badge links back
to MCPWatch, so each customer's server quietly markets the product. This is the growth loop.
"""
from __future__ import annotations

_COLORS = {
    "up": "#2ea043",        # green
    "degraded": "#d29922",  # amber
    "down": "#da3633",      # red
    "unknown": "#6e7681",   # grey
}

# Approximate width per character at 11px Verdana, enough for tidy auto-sizing.
_CHAR_W = 6.5


def _width(text: str) -> float:
    return len(text) * _CHAR_W + 10


def render_badge(label: str, status: str, grade: str | None = None) -> str:
    status = status if status in _COLORS else "unknown"
    right = status.upper()
    if grade and status != "down":
        right = f"{status.upper()} · {grade}"
    color = _COLORS[status]

    lw = _width(label)
    rw = _width(right)
    total = lw + rw
    lx = lw / 2 * 10
    rx = (lw + rw / 2) * 10

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total:.0f}" height="20" \
role="img" aria-label="{label}: {right}">
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r"><rect width="{total:.0f}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{lw:.0f}" height="20" fill="#24292f"/>
    <rect x="{lw:.0f}" width="{rw:.0f}" height="20" fill="{color}"/>
    <rect width="{total:.0f}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" \
font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">
    <text x="{lx:.0f}" y="150" fill="#010101" fill-opacity=".3" \
transform="scale(.1)" textLength="{(lw-10)*10:.0f}">{label}</text>
    <text x="{lx:.0f}" y="140" transform="scale(.1)" \
textLength="{(lw-10)*10:.0f}">{label}</text>
    <text x="{rx:.0f}" y="150" fill="#010101" fill-opacity=".3" \
transform="scale(.1)" textLength="{(rw-10)*10:.0f}">{right}</text>
    <text x="{rx:.0f}" y="140" transform="scale(.1)" \
textLength="{(rw-10)*10:.0f}">{right}</text>
  </g>
</svg>"""
