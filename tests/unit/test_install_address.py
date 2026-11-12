from types import SimpleNamespace

import pytest

from callobuzz_rag.core.install import install_domain, is_dev_domain


@pytest.mark.parametrize(
    "host",
    [
        None,
        "",
        "localhost",
        "LOCALHOST.",
        "shop.localhost",
        "printer.local",
        "app.test",
        "db.internal",
        "nas.lan",
        "router.home.arpa",
        "127.0.0.1",
        "10.0.0.5",
        "172.16.3.4",
        "192.168.1.20",
        "169.254.1.1",
        "100.64.0.1",
        "[::1]",
        "fd00::1",
        "fe80::1",
    ],
)
def test_development_addresses(host: str | None) -> None:
    assert is_dev_domain(host)


@pytest.mark.parametrize(
    "host",
    [
        "chat.sharmafurniture.in",
        "rag.example.com",
        "8.8.8.8",
        "2001:4860:4860::8888",
        "localhost.evil.example",
        "xn--r2bdr8aze.xn--h2brj9c",
    ],
)
def test_public_addresses(host: str) -> None:
    assert not is_dev_domain(host)


def test_install_domain_prefers_the_public_url() -> None:
    s = SimpleNamespace(
        public_url="https://chat.sharmafurniture.in:8443/x",
        dashboard_url="https://admin.example.in",
    )
    assert install_domain(s) == "chat.sharmafurniture.in"  # type: ignore[arg-type]
    s2 = SimpleNamespace(public_url="", dashboard_url="https://admin.example.in")
    assert install_domain(s2) == "admin.example.in"  # type: ignore[arg-type]


def test_an_indian_domain_is_compared_in_punycode() -> None:
    s = SimpleNamespace(public_url="https://शर्मा.भारत", dashboard_url="")
    assert install_domain(s) == "xn--r2bdr8aze.xn--h2brj9c"  # type: ignore[arg-type]
