"""Tests for the SSRF guard."""

import ipaddress
from typing import Any

import httpcore
import pytest

from scraper.crawl.netguard import (
    BlockedURLError,
    _GuardedBackend,
    check_url,
    is_blocked_ip,
)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.5.4",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "255.255.255.255",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "2002:0a00:0001::1",  # 6to4 wrapping 10.0.0.1
        "64:ff9b::a9fe:a9fe",  # NAT64 wrapping 169.254.169.254
    ],
)
def test_blocked_addresses(address: str) -> None:
    assert is_blocked_ip(ipaddress.ip_address(address))


@pytest.mark.parametrize("address", ["93.184.215.14", "8.8.8.8", "2606:4700:4700::1111"])
def test_public_addresses(address: str) -> None:
    assert not is_blocked_ip(ipaddress.ip_address(address))


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.1.2.3/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:8000/",
        "http://[::ffff:127.0.0.1]/",
        "http://app:8000/",
        "http://qdrant:6333/collections",
        "http://localhost/",
        "http://foo.localhost/",
        "http://metadata.google.internal/",
        "http://printer.local/",
        "file:///etc/passwd",
        "gopher://example.com/",
        "ftp://example.com/",
        "http:///nohost",
    ],
)
async def test_check_url_blocks(url: str) -> None:
    with pytest.raises(BlockedURLError):
        await check_url(url)


async def test_public_name_resolving_private_is_blocked(fake_dns: dict[str, list[str]]) -> None:
    fake_dns["evil.example.com"] = ["93.184.215.14", "10.0.0.7"]
    with pytest.raises(BlockedURLError):
        await check_url("https://evil.example.com/")


async def test_unresolvable_name_is_blocked(fake_dns: dict[str, list[str]]) -> None:
    fake_dns["nx.example.com"] = []
    with pytest.raises(BlockedURLError):
        await check_url("https://nx.example.com/")


async def test_public_url_passes() -> None:
    await check_url("https://example.com/page")
    await check_url("http://93.184.215.14:8080/")


async def test_allow_private_lifts_address_checks_not_scheme() -> None:
    await check_url("http://10.1.2.3/", allow_private=True)
    await check_url("http://app:8000/", allow_private=True)
    with pytest.raises(BlockedURLError):
        await check_url("file:///etc/passwd", allow_private=True)


class _FakeInner(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self.connected: list[tuple[str, int]] = []

    async def connect_tcp(self, host: str, port: int, *a: Any, **kw: Any) -> Any:
        self.connected.append((host, port))
        return object()


async def test_connect_time_guard_blocks_rebinding(fake_dns: dict[str, list[str]]) -> None:
    """Even if a name passed the pre-check, the connection re-resolves and refuses."""
    inner = _FakeInner()
    backend = _GuardedBackend(inner)
    fake_dns["rebind.example.com"] = ["127.0.0.1"]
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("rebind.example.com", 443)
    assert inner.connected == []


async def test_connect_time_guard_pins_vetted_ip(fake_dns: dict[str, list[str]]) -> None:
    inner = _FakeInner()
    backend = _GuardedBackend(inner)
    fake_dns["ok.example.com"] = ["93.184.215.14"]
    await backend.connect_tcp("ok.example.com", 443)
    assert inner.connected == [("93.184.215.14", 443)]
