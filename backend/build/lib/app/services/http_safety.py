"""Shared SSRF guard for every ingestion lane that fetches over HTTP.

The trust contract requires that every outbound HTTP fetch on behalf of the
ingestion layer rejects:

- non-https schemes,
- hostnames not in the per-tool `official_hosts` allowlist,
- IP literals or resolved addresses that fall in any private / loopback /
  link-local / multicast / reserved / unspecified class.

This module owns the policy so the docs (7b), openapi (7c.1), and future
schema lanes (7c.2, 7c.3) share one auditable definition.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class HttpFetchError(ValueError):
    """Raised when a target URL is not safe to fetch."""


def host_in_allowlist(hostname: str, allowed: set[str] | frozenset[str]) -> bool:
    """Match exact hostname or any subdomain of an allowed host."""
    return any(hostname == host or hostname.endswith("." + host) for host in allowed)


def assert_url_is_safe(url: str, *, allowed_hosts: frozenset[str]) -> None:
    """Reject URLs that would expose the ingester to SSRF.

    Order:
    1. Require https.
    2. Require non-empty hostname.
    3. Require hostname (or one of its parent domains) to be in `allowed_hosts`.
    4. If hostname parses as a literal IP, require it to be public.
    5. Otherwise resolve the hostname; every returned address must be public.
    """

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise HttpFetchError(
            f"Only https:// URLs are allowed; got scheme {parsed.scheme!r}."
        )
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise HttpFetchError("URL is missing a hostname.")
    if not host_in_allowlist(hostname, allowed_hosts):
        raise HttpFetchError(
            f"Host {hostname!r} is not in the allowed hosts for this tool."
        )

    # Hostname literal as IP. Parse first, raise second so we don't swallow
    # the safety rejection (HttpFetchError subclasses ValueError, which made
    # a broader try/except mask the loopback / link-local rejection).
    ip_literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None
    try:
        ip_literal = ipaddress.ip_address(hostname)
    except ValueError:
        ip_literal = None
    if ip_literal is not None:
        _assert_public_ip(ip_literal)
        return

    # Resolve hostname to every reachable address and require each to be public.
    try:
        infos = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise HttpFetchError(f"Could not resolve {hostname!r}: {exc}") from exc

    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        addr = str(sockaddr[0])
        if addr in seen:
            continue
        seen.add(addr)
        try:
            _assert_public_ip(ipaddress.ip_address(addr))
        except HttpFetchError as exc:
            raise HttpFetchError(
                f"Hostname {hostname!r} resolves to disallowed address {addr}: {exc}"
            ) from exc


def _assert_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise HttpFetchError(f"IP {ip} is private / loopback / reserved.")
