"""website/netguard.py — keep website learning off private and internal networks (SSRF).

A port of `services/scraper/scraper/crawl/netguard.py`: the engine cannot import the scraper
package (its own image and imports). `tests/unit/test_website_netguard.py` loads the scraper file
by path and compares the constants, so the two copies cannot drift apart silently. The one
addition is `check_hop`, called for every redirect hop (website learning refuses a hop that
leaves the site).

A business owner chooses the site the engine reads. Without a guard that turns the engine into a
proxy into its own network: the engine (``http://app:8000``),
the datastores, a cloud metadata service (``169.254.169.254``), the host itself.

Two layers:

* ``check_url`` runs before every fetch (seed URLs, sitemap URLs, every URL found
  in a sitemap, every redirect hop, every browser request): http(s) only, no
  ``localhost`` / ``*.internal`` / single-label names such as compose services, and
  every address the host resolves to must be public.
* ``GuardedTransport`` re-checks at connect time: it resolves the host itself,
  refuses private addresses and connects to the vetted IP. So a name that resolves
  to a public address during the check and a private one a moment later (DNS
  rebinding) still cannot reach the private one. TLS still verifies against the
  original hostname.

``RAG_WEBSITE_ALLOW_PRIVATE_NETWORKS=true`` turns both off (for reading an intranet on
purpose); the scheme check stays.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

ALLOWED_SCHEMES = frozenset({"http", "https"})
BLOCKED_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})
BLOCKED_SUFFIXES = (
    ".localhost",
    ".internal",
    ".local",
    ".localdomain",
    ".home.arpa",
    ".lan",
    ".intranet",
    ".corp",
)
# NAT64 well-known prefix: the low 32 bits are an IPv4 address.
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")


class BlockedURLError(Exception):
    """The URL points somewhere the scraper must not fetch."""


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    """IPv4 addresses carried inside an IPv6 one (mapped, 6to4, Teredo, NAT64, compat)."""
    found: list[ipaddress.IPv4Address] = []
    if ip.ipv4_mapped is not None:
        found.append(ip.ipv4_mapped)
    if ip.sixtofour is not None:
        found.append(ip.sixtofour)
    if ip.teredo is not None:
        found.extend(ip.teredo)
    if ip in _NAT64:
        found.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    packed = ip.packed
    if packed[:12] == bytes(12) and int(ip) > 1:  # deprecated IPv4-compatible ::a.b.c.d
        found.append(ipaddress.IPv4Address(packed[12:]))
    return found


def is_blocked_ip(ip: IPAddress) -> bool:
    """True for anything that is not a plain public unicast address."""
    candidates: list[IPAddress] = [ip]
    if isinstance(ip, ipaddress.IPv6Address):
        candidates.extend(_embedded_ipv4(ip))
    for addr in candidates:
        if (
            addr.is_loopback
            or addr.is_private
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_reserved
            or addr.is_unspecified
            or not addr.is_global  # e.g. 100.64.0.0/10 carrier-grade NAT
        ):
            return True
    return False


async def resolve_host(host: str, port: int) -> list[str]:
    """All addresses ``host`` resolves to, without blocking the event loop."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _normalise_host(host: str) -> str:
    return host.strip().rstrip(".").lower()


def _literal_ip(host: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return None


def check_hostname(host: str) -> None:
    """Refuse names that only make sense inside a private network."""
    if not host:
        raise BlockedURLError("URL has no host")
    if _literal_ip(host) is not None:
        return
    if host in BLOCKED_HOSTNAMES or host.endswith(BLOCKED_SUFFIXES):
        raise BlockedURLError(f"Host '{host}' is internal")
    if "." not in host:
        raise BlockedURLError(f"Host '{host}' is a single-label (internal) name")


async def resolve_public(host: str, port: int) -> list[str]:
    """Resolve ``host`` and return its addresses, or raise if any of them is private."""
    host = _normalise_host(host)
    check_hostname(host)
    literal = _literal_ip(host)
    if literal is not None:
        addresses = [str(literal)]
    else:
        try:
            addresses = await resolve_host(host, port)
        except (OSError, UnicodeError) as exc:
            raise BlockedURLError(f"Cannot resolve host '{host}'") from exc
    if not addresses:
        raise BlockedURLError(f"Cannot resolve host '{host}'")
    for address in addresses:
        ip = _literal_ip(address)
        if ip is None or is_blocked_ip(ip):
            raise BlockedURLError(f"Host '{host}' resolves to a non-public address")
    return addresses


async def check_url(url: str, allow_private: bool = False) -> None:
    """Raise BlockedURLError unless ``url`` is an http(s) URL on the public internet."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise BlockedURLError("Malformed URL") from exc
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise BlockedURLError(f"Scheme '{parts.scheme}' is not allowed; use http or https")
    if not parts.hostname:
        raise BlockedURLError("URL has no host")
    if allow_private:
        return
    await resolve_public(parts.hostname, port or (443 if scheme == "https" else 80))


class _GuardedBackend(httpcore.AsyncNetworkBackend):
    """Resolve, vet and connect to the vetted IP — closes the DNS-rebinding gap."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend) -> None:
        self._inner = inner

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            addresses = await resolve_public(host, port)
        except BlockedURLError as exc:
            raise httpcore.ConnectError(str(exc)) from exc
        return await self._inner.connect_tcp(
            addresses[0],
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("Unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class GuardedTransport(httpx.AsyncHTTPTransport):
    """An httpx transport whose connections can only reach public addresses."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("trust_env", False)  # a proxy would bypass the connect check
        super().__init__(**kwargs)
        pool = self._pool
        pool._network_backend = _GuardedBackend(pool._network_backend)


def make_client(allow_private: bool, timeout: float) -> httpx.AsyncClient:
    """An AsyncClient for crawling: no automatic redirects, guarded unless allowed."""
    transport = None if allow_private else GuardedTransport()
    return httpx.AsyncClient(
        transport=transport,
        follow_redirects=False,
        timeout=httpx.Timeout(timeout),
        trust_env=allow_private,
    )


MAX_REDIRECTS = 5
DEFAULT_MAX_RESPONSE_BYTES = 10 * 1024 * 1024

# Hop-by-hop / framing headers that no longer describe a body httpx has already
# de-chunked and decompressed.
_STRIP_RESPONSE_HEADERS = frozenset(
    {"content-encoding", "content-length", "transfer-encoding", "connection", "keep-alive"}
)


class ResponseTooLargeError(Exception):
    """A response body exceeded the configured size limit."""


async def _read_limited(response: httpx.Response, max_bytes: int) -> bytes:
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise ResponseTooLargeError(f"Response is {declared} bytes; the limit is {max_bytes}")
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ResponseTooLargeError(f"Response exceeds the {max_bytes}-byte limit")
    return bytes(body)


async def guarded_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    allow_private: bool,
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    max_redirects: int = MAX_REDIRECTS,
    total_timeout: float | None = None,
    check_hop: Callable[[str], Awaitable[None]] | None = None,
) -> httpx.Response:
    """Send a request, following redirects by hand and checking every hop.

    The body is read with a size cap and returned already decoded (the returned
    response carries no Content-Encoding / Content-Length). ``total_timeout``
    bounds the whole exchange, redirects included.

    ``check_hop`` (website learning; async) is awaited for every URL before it is requested, the first one
    included; whatever it raises propagates and the URL is never requested.

    Raises BlockedURLError for a blocked URL or hop, ResponseTooLargeError past
    ``max_bytes``, httpx.TooManyRedirects past ``max_redirects``, and
    httpx.TimeoutException when ``total_timeout`` runs out.
    """
    try:
        async with asyncio.timeout(total_timeout):
            return await _follow(
                client,
                method,
                url,
                allow_private,
                headers,
                content,
                max_bytes,
                max_redirects,
                check_hop,
            )
    except TimeoutError as exc:
        raise httpx.ReadTimeout(f"No complete response within {total_timeout}s") from exc


async def _follow(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    allow_private: bool,
    headers: dict[str, str] | None,
    content: bytes | None,
    max_bytes: int,
    max_redirects: int,
    check_hop: Callable[[str], Awaitable[None]] | None = None,
) -> httpx.Response:
    current, current_method, current_content = url, method.upper(), content
    for _ in range(max_redirects + 1):
        if check_hop is not None:
            await check_hop(current)
        await check_url(current, allow_private)
        async with client.stream(
            current_method, current, headers=headers, content=current_content
        ) as response:
            location = response.headers.get("location")
            if response.is_redirect and location:
                next_url = str(response.url.join(location))
                if response.status_code in (301, 302, 303) and current_method != "HEAD":
                    # What browsers do: the follow-up is a GET without the body.
                    current_method, current_content = "GET", None
                current = next_url
                continue
            body = await _read_limited(response, max_bytes)
            kept = [
                (k, v)
                for k, v in response.headers.multi_items()
                if k.lower() not in _STRIP_RESPONSE_HEADERS
            ]
            return httpx.Response(
                response.status_code,
                headers=kept,
                content=body,
                request=response.request,
            )
    raise httpx.TooManyRedirects(f"More than {max_redirects} redirects", request=None)


async def guarded_get(
    client: httpx.AsyncClient,
    url: str,
    allow_private: bool,
    headers: dict[str, str] | None = None,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    max_redirects: int = MAX_REDIRECTS,
    total_timeout: float | None = None,
    check_hop: Callable[[str], Awaitable[None]] | None = None,
) -> httpx.Response:
    """GET ``url`` through ``guarded_request``."""
    return await guarded_request(
        client,
        "GET",
        url,
        allow_private,
        headers=headers,
        max_bytes=max_bytes,
        max_redirects=max_redirects,
        total_timeout=total_timeout,
        check_hop=check_hop,
    )
