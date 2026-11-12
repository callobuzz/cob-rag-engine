"""Site scope, canonical URLs and exclusions for website learning (BIZ1).

The scope of a site is the host the owner listed minus a leading `www.`, plus its subdomains:
never broader than the registrable domain, and equal to it in the usual case (the owner types the
apex or the `www` host). No Public Suffix List dependency (decision D1). Query strings are
dropped from every canonical URL: query variants of a page are excluded by design.
"""

import hashlib
import ipaddress
import re
from collections.abc import Sequence
from typing import Final
from urllib.parse import quote, urljoin, urlsplit

DEFAULT_EXCLUDES: Final[tuple[str, ...]] = (
    "/login",
    "/signin",
    "/sign-in",
    "/logout",
    "/register",
    "/signup",
    "/sign-up",
    "/cart",
    "/basket",
    "/checkout",
    "/account",
    "/my-account",
    "/admin",
    "/wp-admin",
    "/wp-login",
    "/search",
    "/feed",
    "/wp-json",
    "/xmlrpc.php",
    "/cdn-cgi",
)  # matched as a path prefix at any segment boundary, case-insensitive; owner patterns add to these

# Sandbox, embed and proxy hosts an owner may have to allow for the widget (Google Sites embeds)
# but whose pages are not the business's own site.
NOT_LEARNABLE_SUFFIXES: Final = ("googleusercontent.com", "translate.goog", "ampproject.org")

_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_PERCENT_RE = re.compile(r"%([0-9a-fA-F]{2})")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_DEFAULT_PORT = {"http": 80, "https": 443}
_MAX_ID = 490


class UrlError(ValueError):
    """Not a website address we can learn."""


def origin_of(raw: str) -> str:
    """The origin (`https://shreedental.in`) of what an owner typed ("shreedental.in/pricing").

    A missing scheme means https. Raises UrlError for anything that is not a learnable site.
    """
    text = raw.strip()
    if not text or any(ch.isspace() for ch in text):
        raise UrlError("Not a website address")
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise UrlError("Not a website address") from exc
    if parts.scheme.lower() not in _DEFAULT_PORT or parts.username or parts.password:
        raise UrlError("Not a website address")
    host = _host(parts.hostname)
    keep_port = port is not None and port != _DEFAULT_PORT[parts.scheme.lower()]
    return f"{parts.scheme.lower()}://{host}" + (f":{port}" if keep_port else "")


def _host(hostname: str | None) -> str:
    if not hostname:
        raise UrlError("Not a website address")
    host = hostname.strip().rstrip(".").lower()
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        raise UrlError("An IP address is not a website")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UrlError("Not a website address") from exc
    if host == "localhost" or not _HOST_RE.match(host):
        raise UrlError("Not a website address")
    return host


def site_scope(origin: str) -> str:
    """`https://www.ShreeDental.in` -> `shreedental.in`; UrlError for anything not learnable."""
    if origin.strip() == "*":
        raise UrlError("A wildcard is not a website")
    parts = urlsplit(origin.strip())
    if parts.scheme.lower() not in _DEFAULT_PORT:
        raise UrlError("Not a website address")
    try:
        parts.port  # noqa: B018 - validates the port
    except ValueError as exc:
        raise UrlError("Not a website address") from exc
    host = _host(parts.hostname)
    host = host.removeprefix("www.")
    if "." not in host:
        raise UrlError("Not a website address")
    if any(host == s or host.endswith("." + s) for s in NOT_LEARNABLE_SUFFIXES):
        raise UrlError("This address is not your own website")
    return host


def in_scope(url: str, scope: str, *, port: int | None = None) -> bool:
    """True when `url` is http(s), on `scope` or a subdomain, with no userinfo, on the default
    port of its scheme or on `port` (the allowed origin's own port)."""
    try:
        parts = urlsplit(url)
        url_port = parts.port
    except ValueError:
        return False
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORT or parts.username is not None or parts.password is not None:
        return False
    host = _ascii_host(parts.hostname)
    if not host or not (host == scope or host.endswith("." + scope)):
        return False
    return url_port is None or url_port == _DEFAULT_PORT[scheme] or url_port == port


def _ascii_host(hostname: str | None) -> str:
    """The host in its punycode spelling (as scopes are kept), or "" when it cannot be encoded."""
    host = (hostname or "").rstrip(".").lower()
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return ""


def _normalise_path(path: str) -> str:
    def fix(match: re.Match[str]) -> str:
        char = chr(int(match.group(1), 16))
        return char if char in _UNRESERVED else "%" + match.group(1).upper()

    path = quote(path, safe="/%:@!$&'()*+,;=-._~")
    path = _PERCENT_RE.sub(fix, path)
    if not path:
        return "/"
    for tail in ("/index.html", "/index.htm"):
        if path.endswith(tail):
            return path[: -len(tail)] + "/"
    return path


def canonical(url: str, *, base: str | None = None) -> str | None:
    """The one spelling of a page's address, or None for anything that is not an http(s) page."""
    try:
        parts = urlsplit(urljoin(base, url) if base else url)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = _ascii_host(parts.hostname)
    if scheme not in _DEFAULT_PORT or not host or parts.username or parts.password:
        return None
    authority = host if port is None or port == _DEFAULT_PORT[scheme] else f"{host}:{port}"
    return f"{scheme}://{authority}{_normalise_path(parts.path)}"


def _glob(pattern: str, path: str) -> bool:
    regex = "^" + ".*".join(re.escape(p) for p in pattern.split("*")) + "$"
    if re.match(regex, path, re.IGNORECASE):
        return True
    # "/blog/*" also covers the folder page itself
    return pattern.endswith("/*") and path.lower().rstrip("/") == pattern[:-2].lower()


def _segment_boundary_hit(pattern: str, path: str) -> bool:
    return path == pattern or path.startswith(pattern + "/")


def is_excluded(url: str, patterns: Sequence[str]) -> str | None:
    """The pattern that excludes `url` (the defaults first, then the owner's), or None."""
    path = urlsplit(url).path or "/"
    low = path.lower()
    segments = [s for s in low.split("/") if s]
    for pattern in DEFAULT_EXCLUDES:
        for i in range(len(segments)):
            if _segment_boundary_hit(pattern, "/" + "/".join(segments[i:])):
                return pattern
    for raw in patterns:
        pattern = raw.strip()
        if not pattern:
            continue
        probe = pattern if pattern.startswith("/") else "/" + pattern
        if "*" in probe:
            if _glob(probe, path):
                return pattern
        elif _segment_boundary_hit(probe.lower().rstrip("/") or "/", low.rstrip("/") or "/"):
            return pattern
    return None


def external_id(url: str) -> str:
    """`web:<canonical url>`, or `web:sha256:<hex>` when that would not fit the column."""
    value = f"web:{url}"
    if len(value) > _MAX_ID:
        return "web:sha256:" + hashlib.sha256(url.encode()).hexdigest()
    return value


def url_hash(url: str) -> str:
    """The stable key of a page within a site (`website_pages.url_hash`)."""
    return hashlib.sha256(url.encode()).hexdigest()
