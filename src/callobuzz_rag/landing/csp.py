"""The Content-Security-Policy of the public website (LP1, decision D22).

Scripts may come only from the engine's own address or be one of the few inline scripts we wrote,
named by hash (never a nonce, so a cached page stays valid). Styles allow `'unsafe-inline'`
because the colour variables are an inline `<style>` and the chat widget styles its own Shadow
DOM. Nothing else may load from elsewhere except pictures over https.
"""

import base64
import hashlib
from collections.abc import Iterable


def script_hash(body: str) -> str:
    digest = hashlib.sha256(body.encode("utf-8")).digest()
    return f"'sha256-{base64.b64encode(digest).decode('ascii')}'"


def build_csp(
    inline_scripts: Iterable[str],
    *,
    preview: bool = False,
    dashboard_origins: Iterable[str] = (),
) -> str:
    """The header value. A preview may be framed by this site and by the dashboard only; a
    published page may not be framed at all."""
    hashes = " ".join(dict.fromkeys(script_hash(body) for body in inline_scripts))
    scripts = f"'self' {hashes}".strip()
    ancestors = "'none'"
    if preview:
        ancestors = " ".join(["'self'", *dict.fromkeys(dashboard_origins)])
    return "; ".join(
        (
            "default-src 'self'",
            f"script-src {scripts}",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' https: data:",
            "media-src 'self'",
            "font-src 'self'",
            "connect-src 'self'",
            "form-action 'self'",
            f"frame-ancestors {ancestors}",
            "base-uri 'none'",
            "object-src 'none'",
        )
    )
