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

from dataclasses import dataclass
import http.client
import ipaddress
import socket
import ssl
import zlib
from urllib.parse import urlparse


# Hard ceiling on how many bytes a single outbound fetch may pull into
# memory — applied to BOTH the compressed wire bytes and the decompressed
# result. The largest legitimate lane cap is 8 MiB (openapi / graphql), so
# 16 MiB leaves generous headroom while still bounding a hostile or
# compromised allowlisted host from exhausting memory with a gigabyte body
# or a small gzip/deflate bomb. Per-lane `max_bytes` still applies on top
# of this as the precise limit; this is the defence-in-depth floor.
_DEFAULT_MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class HttpFetchError(ValueError):
    """Raised when a target URL is not safe to fetch."""


@dataclass(frozen=True)
class SafeUrlResolution:
    """Validated HTTPS target plus the exact public IP to connect to."""

    url: str
    hostname: str
    port: int
    request_target: str
    connect_ip: str


@dataclass(frozen=True)
class SafeHttpResponse:
    """Minimal response surface the ingestion lanes need."""

    status_code: int
    text: str
    headers: dict[str, str]
    url: str


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection that pins the TCP connect step to one resolved IP.

    `self.host` remains the original hostname, so TLS SNI and certificate
    validation still target the host the trust contract allowlisted.
    """

    def __init__(
        self,
        *,
        host: str,
        pinned_ip: str,
        port: int,
        timeout: float,
    ) -> None:
        super().__init__(
            host=host,
            port=port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        sock = socket.create_connection(
            (self._pinned_ip, self.port),
            self.timeout,
            self.source_address,
        )
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
        server_hostname = self.host if self._context.check_hostname else None
        self.sock = self._context.wrap_socket(sock, server_hostname=server_hostname)


def host_in_allowlist(hostname: str, allowed: set[str] | frozenset[str]) -> bool:
    """Match exact hostname or any subdomain of an allowed host."""
    return any(hostname == host or hostname.endswith("." + host) for host in allowed)


def resolve_url_for_safe_fetch(url: str, *, allowed_hosts: frozenset[str]) -> SafeUrlResolution:
    """Validate a URL and pin it to a single already-vetted public IP.

    The caller must carry the returned `connect_ip` through to the actual
    socket connect step. That closes the DNS-rebinding window where one
    lookup is validated but a second lookup performed by the HTTP client
    connects somewhere else.
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

    port = parsed.port or 443
    request_target = _request_target(parsed)

    try:
        ip_literal = ipaddress.ip_address(hostname)
    except ValueError:
        ip_literal = None
    if ip_literal is not None:
        _assert_public_ip(ip_literal)
        return SafeUrlResolution(
            url=url,
            hostname=hostname,
            port=port,
            request_target=request_target,
            connect_ip=str(ip_literal),
        )

    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise HttpFetchError(f"Could not resolve {hostname!r}: {exc}") from exc

    seen: set[str] = set()
    connect_ip: str | None = None
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
        if connect_ip is None:
            connect_ip = addr

    if connect_ip is None:
        raise HttpFetchError(f"Could not resolve {hostname!r} to any public address.")

    return SafeUrlResolution(
        url=url,
        hostname=hostname,
        port=port,
        request_target=request_target,
        connect_ip=connect_ip,
    )


def assert_url_is_safe(url: str, *, allowed_hosts: frozenset[str]) -> None:
    """Reject URLs that would expose the ingester to SSRF.

    Order:
    1. Require https.
    2. Require non-empty hostname.
    3. Require hostname (or one of its parent domains) to be in `allowed_hosts`.
    4. If hostname parses as a literal IP, require it to be public.
    5. Otherwise resolve the hostname; every returned address must be public.
    """
    resolve_url_for_safe_fetch(url, allowed_hosts=allowed_hosts)


def safe_https_request(
    method: str,
    *,
    resolution: SafeUrlResolution,
    headers: dict[str, str],
    timeout: float,
    max_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
) -> SafeHttpResponse:
    """Issue one HTTPS request against the pinned public IP from `resolution`.

    `max_bytes` caps both the compressed bytes read off the wire and the
    decompressed body, so a hostile or compromised allowlisted host cannot
    exhaust memory with an oversized response or a compression bomb.
    """

    conn = _PinnedHTTPSConnection(
        host=resolution.hostname,
        pinned_ip=resolution.connect_ip,
        port=resolution.port,
        timeout=timeout,
    )
    request_headers = dict(headers)
    if not any(key.lower() == "host" for key in request_headers):
        request_headers["Host"] = (
            resolution.hostname
            if resolution.port == 443
            else f"{resolution.hostname}:{resolution.port}"
        )
    try:
        conn.request(method, resolution.request_target, headers=request_headers)
        response = conn.getresponse()
        # Read one byte past the limit so we can tell "exactly at cap" from
        # "over cap" — http.client's read(amt) fulfils `amt` unless EOF.
        raw_body = response.read(max_bytes + 1)
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        raise HttpFetchError(
            f"HTTPS {method} failed for {resolution.url}: {exc}"
        ) from exc
    finally:
        conn.close()

    if len(raw_body) > max_bytes:
        raise HttpFetchError(
            f"Response body from {resolution.url} exceeded the "
            f"{max_bytes}-byte safety limit."
        )

    headers_lc = {key.lower(): value for key, value in response.getheaders()}
    decoded = _decode_response_body(raw_body, headers_lc, max_bytes=max_bytes)
    return SafeHttpResponse(
        status_code=int(response.status),
        text=decoded,
        headers=headers_lc,
        url=resolution.url,
    )


def _request_target(parsed) -> str:
    path = parsed.path or "/"
    if parsed.params:
        path = f"{path};{parsed.params}"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


def _decompress_bounded(raw_body: bytes, *, wbits: int, max_bytes: int) -> bytes:
    """Inflate `raw_body` but never produce more than `max_bytes` of output.

    Uses a streaming `decompressobj` with an output cap instead of the
    one-shot `gzip.decompress` / `zlib.decompress`, which would happily
    expand a small compression bomb into gigabytes before any check ran.
    """
    dobj = zlib.decompressobj(wbits)
    out = dobj.decompress(raw_body, max_bytes + 1)
    # `unconsumed_tail` is non-empty when output hit the cap with input
    # still pending — i.e. the decompressed body is larger than allowed.
    if dobj.unconsumed_tail or len(out) > max_bytes:
        raise HttpFetchError(
            f"Decompressed response exceeded the {max_bytes}-byte safety "
            "limit (possible compression bomb)."
        )
    out += dobj.flush()
    if len(out) > max_bytes:
        raise HttpFetchError(
            f"Decompressed response exceeded the {max_bytes}-byte safety "
            "limit (possible compression bomb)."
        )
    return out


def _decode_response_body(
    raw_body: bytes, headers: dict[str, str], *, max_bytes: int
) -> str:
    encoding = headers.get("content-encoding", "").lower()
    if encoding == "gzip":
        # wbits 16+MAX_WBITS selects gzip framing.
        raw_body = _decompress_bounded(
            raw_body, wbits=16 + zlib.MAX_WBITS, max_bytes=max_bytes
        )
    elif encoding == "deflate":
        raw_body = _decompress_bounded(
            raw_body, wbits=zlib.MAX_WBITS, max_bytes=max_bytes
        )

    charset = "utf-8"
    content_type = headers.get("content-type", "")
    for part in content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            charset = value.strip().strip('"')
            break
    try:
        return raw_body.decode(charset, errors="replace")
    except LookupError:
        return raw_body.decode("utf-8", errors="replace")


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
