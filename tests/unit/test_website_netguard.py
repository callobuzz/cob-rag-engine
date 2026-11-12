import importlib.util
import ipaddress
from pathlib import Path

import httpcore
import httpx
import pytest

from callobuzz_rag.website import netguard
from callobuzz_rag.website.netguard import BlockedURLError, check_url, is_blocked_ip


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.3.4",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "64:ff9b::a00:1",
        "2002:a00:1::",
    ],
)
def test_private_and_special_addresses_are_blocked(ip: str) -> None:
    assert is_blocked_ip(ipaddress.ip_address(ip))


def test_public_address_passes() -> None:
    assert not is_blocked_ip(ipaddress.ip_address("93.184.216.34"))


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://app:8000/",
        "http://qdrant/",
        "https://db.internal/",
        "http://printer.local/",
        "ftp://shreedental.in/",
        "file:///etc/passwd",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.1.2.3/",
    ],
)
async def test_internal_urls_are_refused(url: str) -> None:
    with pytest.raises(BlockedURLError):
        await check_url(url)


async def test_public_name_resolving_to_a_private_address_is_refused(monkeypatch) -> None:
    async def fake(host: str, port: int) -> list[str]:
        return ["93.184.216.34", "10.0.0.7"]

    monkeypatch.setattr(netguard, "resolve_host", fake)
    with pytest.raises(BlockedURLError):
        await check_url("https://shreedental.in/")


async def test_connect_time_check_closes_dns_rebinding(monkeypatch) -> None:
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])

    async def fake(host: str, port: int) -> list[str]:
        return next(answers)

    monkeypatch.setattr(netguard, "resolve_host", fake)
    await check_url("https://shreedental.in/")  # first answer: public
    backend = netguard._GuardedBackend(httpcore.AsyncConnectionPool()._network_backend)
    with pytest.raises(httpcore.ConnectError):  # second answer: private
        await backend.connect_tcp("shreedental.in", 443)


async def test_allow_private_lifts_the_address_check_but_not_the_scheme() -> None:
    await check_url("http://127.0.0.1:8000/", allow_private=True)
    with pytest.raises(BlockedURLError):
        await check_url("file:///etc/passwd", allow_private=True)


async def test_check_hop_runs_before_every_request_and_can_refuse() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/a":
            return httpx.Response(302, headers={"location": "https://other.example/b"})
        return httpx.Response(200, text="ok")

    async def hop(url: str) -> None:
        if "other.example" in url:
            raise BlockedURLError("off site")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BlockedURLError):
            await netguard.guarded_get(
                client, "https://shreedental.in/a", allow_private=True, check_hop=hop
            )
    assert seen == ["https://shreedental.in/a"]  # the other host was never requested


def test_constants_match_the_scraper_copy() -> None:
    path = Path(__file__).resolve().parents[2] / "services/scraper/scraper/crawl/netguard.py"
    spec = importlib.util.spec_from_file_location("scraper_netguard", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert netguard.BLOCKED_SUFFIXES == mod.BLOCKED_SUFFIXES
    assert netguard.BLOCKED_HOSTNAMES == mod.BLOCKED_HOSTNAMES
    assert netguard.ALLOWED_SCHEMES == mod.ALLOWED_SCHEMES
