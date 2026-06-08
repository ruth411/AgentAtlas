"""Regression tests for the shared SSRF transport helpers."""

from __future__ import annotations

import gzip
import socket

import pytest

from app.services.http_safety import (
    HttpFetchError,
    resolve_url_for_safe_fetch,
    safe_https_request,
)


class _FakeHTTPResponse:
    status = 200

    def __init__(
        self,
        body: bytes = b'{"ok": true}',
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> None:
        self._body = body
        self._headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Location", "/next"),
        ]
        if extra_headers:
            self._headers.extend(extra_headers)

    def read(self, amt: int | None = None) -> bytes:
        # Mirror http.client.HTTPResponse.read(amt): return up to `amt`
        # bytes, or the whole body when `amt` is None.
        if amt is None:
            return self._body
        return self._body[:amt]

    def getheaders(self):
        return list(self._headers)


class _FakePinnedConnection:
    instances: list["_FakePinnedConnection"] = []

    def __init__(self, *, host: str, pinned_ip: str, port: int, timeout: float) -> None:
        self.host = host
        self.pinned_ip = pinned_ip
        self.port = port
        self.timeout = timeout
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.closed = False
        self.__class__.instances.append(self)

    def request(self, method: str, target: str, headers: dict[str, str]) -> None:
        self.requests.append((method, target, dict(headers)))

    def getresponse(self) -> _FakeHTTPResponse:
        return _FakeHTTPResponse()

    def close(self) -> None:
        self.closed = True


def test_safe_https_request_uses_pinned_ip_without_second_dns_lookup(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    def fake_getaddrinfo(host, port, *args, **kwargs):
        calls.append((host, port))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(
        "app.services.http_safety._PinnedHTTPSConnection",
        _FakePinnedConnection,
    )

    resolution = resolve_url_for_safe_fetch(
        "https://api.openai.com/v1/openapi.json?view=full",
        allowed_hosts=frozenset({"api.openai.com"}),
    )
    response = safe_https_request(
        "GET",
        resolution=resolution,
        headers={"Accept": "application/json"},
        timeout=5.0,
    )

    assert calls == [("api.openai.com", 443)]
    conn = _FakePinnedConnection.instances[-1]
    assert conn.host == "api.openai.com"
    assert conn.pinned_ip == "93.184.216.34"
    assert conn.requests == [
        (
            "GET",
            "/v1/openapi.json?view=full",
            {
                "Accept": "application/json",
                "Host": "api.openai.com",
            },
        )
    ]
    assert conn.closed is True
    assert response.status_code == 200
    assert response.text == '{"ok": true}'
    assert response.headers["location"] == "/next"


def _connection_returning(response: _FakeHTTPResponse):
    class _Conn(_FakePinnedConnection):
        def getresponse(self) -> _FakeHTTPResponse:
            return response

    return _Conn


def _pin_dns(monkeypatch) -> None:
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def test_safe_https_request_rejects_oversized_body(monkeypatch) -> None:
    """An allowlisted host returning a body larger than the cap must be
    rejected before it is fully read into memory (P1-1)."""
    _pin_dns(monkeypatch)
    monkeypatch.setattr(
        "app.services.http_safety._PinnedHTTPSConnection",
        _connection_returning(_FakeHTTPResponse(body=b"A" * 4096)),
    )
    resolution = resolve_url_for_safe_fetch(
        "https://api.openai.com/x", allowed_hosts=frozenset({"api.openai.com"})
    )
    with pytest.raises(HttpFetchError, match="safety limit"):
        safe_https_request(
            "GET", resolution=resolution, headers={}, timeout=5.0, max_bytes=1024
        )


def test_safe_https_request_rejects_compression_bomb(monkeypatch) -> None:
    """A small gzip payload that inflates past the cap must be rejected
    during decompression rather than expanded into memory first (P1-1)."""
    _pin_dns(monkeypatch)
    bomb = gzip.compress(b"\0" * (64 * 1024))  # ~80 compressed bytes
    monkeypatch.setattr(
        "app.services.http_safety._PinnedHTTPSConnection",
        _connection_returning(
            _FakeHTTPResponse(
                body=bomb, extra_headers=[("Content-Encoding", "gzip")]
            )
        ),
    )
    resolution = resolve_url_for_safe_fetch(
        "https://api.openai.com/x", allowed_hosts=frozenset({"api.openai.com"})
    )
    with pytest.raises(HttpFetchError, match="compression bomb"):
        safe_https_request(
            "GET", resolution=resolution, headers={}, timeout=5.0, max_bytes=4096
        )


def test_resolve_url_for_safe_fetch_preserves_explicit_port(monkeypatch) -> None:
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    resolution = resolve_url_for_safe_fetch(
        "https://docs.example.com:8443/path/to/spec.json",
        allowed_hosts=frozenset({"example.com"}),
    )

    assert resolution.hostname == "docs.example.com"
    assert resolution.port == 8443
    assert resolution.request_target == "/path/to/spec.json"
    assert resolution.connect_ip == "93.184.216.34"
