"""Refuse outbound URLs that point inside the operator's network (SSRF).

A tenant chooses a knowledge-base webhook URL and the worker posts to it, so the address has
to be checked: at create time (so the owner gets an answer) and again at delivery (DNS can
change between the two). A URL passes only when it is https, has a host, and every address
that host resolves to is a public one.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from callobuzz_rag.core.exceptions import RAGKernelError


class UnsafeURLError(RAGKernelError):
    """The URL is not an https address on the public internet."""


_BLOCKED_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


def _check_address(raw: str) -> None:
    try:
        address = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError as e:
        raise UnsafeURLError("unresolvable address") from e
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if not address.is_global or address.is_multicast:
        raise UnsafeURLError("address is not public")


def check_public_https_url(url: str, *, resolve: bool = True) -> None:
    """Raise `UnsafeURLError` unless `url` is https and its host is public.

    `resolve=False` checks only the literal (a name is checked by shape, not by DNS).
    Blocking: call `acheck_public_https_url` from async code.
    """
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise UnsafeURLError("only https addresses are allowed")
    if parts.username or parts.password:
        raise UnsafeURLError("credentials in the address are not allowed")
    host = parts.hostname.rstrip(".").lower()
    if host in _BLOCKED_NAMES or host.endswith(_BLOCKED_SUFFIXES):
        raise UnsafeURLError("address is not public")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not resolve:
            return
        try:
            infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
        except OSError as e:
            raise UnsafeURLError("host does not resolve") from e
        if not infos:
            raise UnsafeURLError("host does not resolve") from None
        for info in infos:
            _check_address(str(info[4][0]))
    else:
        _check_address(host)


async def acheck_public_https_url(url: str, *, resolve: bool = True) -> None:
    await asyncio.to_thread(check_public_https_url, url, resolve=resolve)
