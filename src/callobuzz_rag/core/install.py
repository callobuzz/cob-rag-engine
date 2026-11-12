"""Where this install lives: its domain, and whether that address is a development address.

No DNS lookup happens here.
"""

import ipaddress
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings

_DEV_SUFFIXES = (
    ".localhost",
    ".local",
    ".test",
    ".internal",
    ".lan",
    ".home.arpa",
)
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_ULA = ipaddress.ip_network("fc00::/7")


def to_ascii_host(host: str) -> str:
    """The ASCII (punycode) form of an internationalised host, so a `.भारत` address means the
    same everywhere; a name that will not convert is kept as it is."""
    if host.isascii():
        return host
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return host


def _host_of(url: str) -> str | None:
    text = (url or "").strip()
    if not text or text == "null":
        return None
    if "://" not in text:
        text = "//" + text
    try:
        host = urlsplit(text).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower()
    host = host[:-1] if host.endswith(".") else host
    return to_ascii_host(host)


def install_domain(settings: "Settings") -> str | None:
    """Host of RAG_PUBLIC_URL, else of RAG_DASHBOARD_URL; lower-case, no port."""
    return _host_of(settings.public_url) or _host_of(settings.dashboard_url)


def is_dev_domain(host: str | None) -> bool:
    if host is None:
        return True
    name = host.strip().lower().rstrip(".").strip("[]")
    if not name:
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return name == "localhost" or name.endswith(_DEV_SUFFIXES)
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or (ip.version == 4 and ip in _CGNAT)
        or (ip.version == 6 and ip in _ULA)
    )
