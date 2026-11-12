import socket
from typing import Any

import pytest

from callobuzz_rag.core.url_guard import (
    UnsafeURLError,
    acheck_public_https_url,
    check_public_https_url,
)


def _resolves_to(monkeypatch: pytest.MonkeyPatch, *addresses: str) -> None:
    def fake(host: str, port: Any, **_: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.com/x",
        "https://localhost/x",
        "https://app.localhost/x",
        "https://127.0.0.1/x",
        "https://[::1]/x",
        "https://10.1.2.3/x",
        "https://192.168.0.5/x",
        "https://169.254.169.254/latest/meta-data",
        "https://100.64.0.1/x",
        "https://[::ffff:10.0.0.1]/x",
        "https://user:pw@hooks.example.com/x",
        "https://intranet.internal/x",
        "https:///nohost",
        "ftp://hooks.example.com",
    ],
)
def test_refused_without_dns(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        check_public_https_url(url, resolve=False)


def test_public_literal_and_name_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    check_public_https_url("https://8.8.8.8/hook")
    _resolves_to(monkeypatch, "93.184.216.34")
    check_public_https_url("https://hooks.example.com/x")


def test_a_name_that_resolves_to_a_private_address_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _resolves_to(monkeypatch, "93.184.216.34", "10.0.0.7")
    with pytest.raises(UnsafeURLError):
        check_public_https_url("https://rebind.example.com/x")


def test_an_unresolvable_name_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: Any, **__: Any) -> list[Any]:
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(UnsafeURLError):
        check_public_https_url("https://nope.example.com/x")


async def test_async_variant(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolves_to(monkeypatch, "127.0.0.1")
    with pytest.raises(UnsafeURLError):
        await acheck_public_https_url("https://sneaky.example.com/x")
