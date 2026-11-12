"""Razorpay's checkout may load on ONE page (P1): the dashboard's nginx gives `/pay` its own
Content-Security-Policy and no other location mentions Razorpay. The shell cases that render the
template are in `services/dashboard/deploy/test-config.sh`; this pins the template itself."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "services/dashboard/deploy/nginx.conf.template").read_text("utf-8")


def _locations() -> dict[str, str]:
    """Each `location ... {` block's body, by its head, comments removed."""
    code = "\n".join(line for line in TEMPLATE.splitlines() if not line.strip().startswith("#"))
    return {
        head.strip(): body
        for head, body in re.findall(r"location\s+([^{]+)\{(.*?)\n\t\}", code, flags=re.S)
    }


def test_only_the_pay_location_names_razorpay() -> None:
    named = [head for head, body in _locations().items() if "razorpay.com" in body]
    assert named == ["= /pay"]


def test_pay_is_an_exact_location_that_serves_the_app() -> None:
    body = _locations()["= /pay"]
    assert "try_files /index.html =404;" in body
    assert "Cache-Control" in body and "no-cache" in body


def test_pay_csp_opens_exactly_the_checkout_hosts() -> None:
    body = _locations()["= /pay"]
    csp = re.search(r'Content-Security-Policy "([^"]+)"', body)
    assert csp is not None
    directives = {d.split()[0]: d.split()[1:] for d in csp.group(1).split("; ")}
    assert directives["script-src"] == ["'self'", "https://checkout.razorpay.com"]
    assert directives["frame-src"] == [
        "'self'",
        "https://api.razorpay.com",
        "https://checkout.razorpay.com",
    ]
    assert "https://api.razorpay.com" in directives["connect-src"]
    assert "https://lumberjack.razorpay.com" in directives["connect-src"]
    # nothing else is relaxed: no inline scripts, no eval, no wildcard
    assert "'unsafe-inline'" not in directives["script-src"]
    assert "'unsafe-eval'" not in csp.group(1) and "*" not in csp.group(1)
    # the page that opens a real payment window is never framed, whatever the demo allows
    assert directives["frame-ancestors"] == ["'none'"]


def test_the_pay_page_is_reached_with_a_full_page_load() -> None:
    """A page's CSP is fixed when its document loads, so the dashboard never navigates to /pay
    with the client-side router: it assigns `window.location`."""
    checkout = (ROOT / "services/dashboard/src/features/payments/checkout.ts").read_text("utf-8")
    assert "window.location.assign" in checkout
