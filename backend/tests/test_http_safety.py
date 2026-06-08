"""Regression tests for the shared SSRF transport helpers."""

from __future__ import annotations

import socket

from app.services.http_safety import (
    resolve_url_for_safe_fetch,
    safe_https_request,
)


class _FakeHTTPResponse:
    status = 200

    def __init__(self) -> None:
        self._headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Location", "/next"),
        ]

    def read(self) -> bytes:
        return b'{"ok": true}'

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
