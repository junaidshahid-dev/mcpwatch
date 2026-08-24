"""Security guards for user-supplied monitor targets.

MCPWatch connects to endpoints its customers type in. Without guards, that turns the service
into an unrestricted network scanner / SSRF pivot: a customer could point a monitor at
`http://169.254.169.254/` (cloud metadata) or `http://10.0.0.5/` (our private network) and read
the response through our infrastructure. These functions refuse that.

Two attack surfaces:
  * http monitors — validate the URL scheme and resolve the host, rejecting any private,
    loopback, link-local, reserved or otherwise non-public IP (unless explicitly allowed for
    self-hosting). Resolving before connecting also blunts DNS-rebinding.
  * stdio monitors — launching a customer's command is remote code execution on our workers.
    Allowed only when the deployment opts in (a self-hosted box); the hosted control plane runs
    stdio exclusively inside an isolated sandbox worker.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse, urlunparse

import httpx

# matches userinfo credentials in a URL: scheme://user:pass@host
_CREDS_RE = re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@")

from .config import SETTINGS


class SecurityError(ValueError):
    """A target was refused for security reasons. The message is safe to show the user."""


def _ip_is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_multicast or addr.is_reserved or addr.is_unspecified)


def resolve_public_ips(host: str) -> list[str]:
    """Resolve a hostname and return its IPs, raising SecurityError if any is non-public."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise SecurityError(f"could not resolve host {host!r}: {e}") from None
    ips = sorted({info[4][0] for info in infos})
    if not ips:
        raise SecurityError(f"host {host!r} did not resolve to any address")
    if SETTINGS.ssrf_allow_private:
        return ips
    bad = [ip for ip in ips if not _ip_is_public(ip)]
    if bad:
        raise SecurityError(
            f"host {host!r} resolves to a non-public address ({', '.join(bad)}); "
            "monitoring private, loopback or reserved addresses is not allowed")
    return ips


def validate_http_target(url: str) -> list[str]:
    """Validate an http(s) monitor URL. Returns resolved public IPs, or raises SecurityError."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SecurityError("http monitors must use an http:// or https:// URL")
    if not parsed.hostname:
        raise SecurityError("URL is missing a host")
    return resolve_public_ips(parsed.hostname)


def validate_stdio_allowed() -> None:
    if not SETTINGS.allow_stdio_monitors:
        raise SecurityError(
            "stdio monitors (arbitrary launch commands) are disabled on this deployment; "
            "use an http endpoint, or self-host to run local servers")


def validate_target(kind: str, endpoint: str) -> None:
    """Gate a monitor target at creation and before every check."""
    if kind == "http":
        validate_http_target(endpoint)
    elif kind == "stdio":
        validate_stdio_allowed()
    else:
        raise SecurityError(f"unknown monitor kind: {kind!r}")


def sanitize_url(url: str) -> str:
    """Strip any credentials from a URL so it is safe to log or surface in an error."""
    try:
        p = urlparse(url)
        if p.username or p.password:
            netloc = p.hostname or ""
            if p.port:
                netloc += f":{p.port}"
            return urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))
        return url
    except Exception:
        return "<url>"


def scrub_text(text: str) -> str:
    """Remove any embedded URL credentials from arbitrary text (errors, logs)."""
    return _CREDS_RE.sub(r"\1", text or "")


class GuardedTransport(httpx.HTTPTransport):
    """An httpx transport that re-resolves and re-validates the host at CONNECT time.

    Validating only when the monitor is created (or when the client is built) leaves a
    time-of-check/time-of-use gap: DNS can be flipped to a private address between the check and
    the actual connection (DNS rebinding). Re-validating here, immediately before httpcore
    connects, closes almost all of that window — any resolution to a private/loopback/reserved
    address is refused. Redirects are disabled at the client level so a 3xx cannot escape this.
    """

    def handle_request(self, request):
        host = request.url.host.decode() if isinstance(request.url.host, bytes) else request.url.host
        resolve_public_ips(host)  # raises SecurityError on a non-public resolution
        return super().handle_request(request)
