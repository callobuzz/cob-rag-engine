"""The address every per-IP limit, session record and audit row is keyed on."""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from callobuzz_rag.core.config import Settings
from callobuzz_rag.widget.security import client_ip, request_client_ip

PEER = "10.0.0.9"


def _request(*forwarded: str, peer: str | None = PEER) -> Request:
    """A real Starlette request carrying one X-Forwarded-For header per argument."""
    headers = [(b"x-forwarded-for", value.encode()) for value in forwarded]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (peer, 5000) if peer else None,
    }
    return Request(scope)


class TestTrustOff:
    def test_forwarded_for_is_ignored_entirely(self) -> None:
        request = _request("1.2.3.4, 5.6.7.8")
        assert client_ip(request, False) == PEER
        assert client_ip(request, False, 3) == PEER

    def test_no_peer_is_unknown(self) -> None:
        assert client_ip(_request(peer=None), False) == "unknown"


class TestOneHop:
    def test_takes_the_entry_the_proxy_appended(self) -> None:
        # The proxy saw 203.0.113.7 connect and appended it.
        assert client_ip(_request("203.0.113.7"), True) == "203.0.113.7"

    def test_a_spoofed_leading_entry_is_not_believed(self) -> None:
        # The caller sent "1.1.1.1"; the proxy appended the real address.
        assert client_ip(_request("1.1.1.1, 203.0.113.7"), True) == "203.0.113.7"

    def test_many_spoofed_entries_still_resolve_to_the_last(self) -> None:
        spoof = ", ".join(f"9.9.9.{n}" for n in range(1, 40))
        assert client_ip(_request(f"{spoof}, 203.0.113.7"), True) == "203.0.113.7"

    def test_repeated_headers_count_as_one_list(self) -> None:
        assert client_ip(_request("1.1.1.1", "203.0.113.7"), True) == "203.0.113.7"

    def test_no_header_falls_back_to_the_peer(self) -> None:
        assert client_ip(_request(), True) == PEER


class TestSeveralHops:
    def test_two_hops_skips_the_inner_proxy(self) -> None:
        # client -> CDN -> nginx -> app: "client, cdn".
        request = _request("198.51.100.4, 203.0.113.7")
        assert client_ip(request, True, 2) == "198.51.100.4"

    def test_spoofed_entry_before_the_chain_is_ignored(self) -> None:
        request = _request("1.1.1.1, 198.51.100.4, 203.0.113.7")
        assert client_ip(request, True, 2) == "198.51.100.4"

    def test_a_shorter_chain_than_configured_falls_back_to_the_peer(self) -> None:
        # Two hops are trusted but only one entry arrived: the request skipped a proxy,
        # so nothing in the header can be believed.
        assert client_ip(_request("1.1.1.1"), True, 2) == PEER

    def test_hops_below_one_behave_as_one(self) -> None:
        assert client_ip(_request("1.1.1.1, 203.0.113.7"), True, 0) == "203.0.113.7"


class TestAddressForms:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("2001:db8::1", "2001:db8::1"),
            ("2001:DB8:0:0:0:0:0:1", "2001:db8::1"),
            ("[2001:db8::1]", "2001:db8::1"),
            ("[2001:db8::1]:443", "2001:db8::1"),
            ("203.0.113.7:51234", "203.0.113.7"),
            ("::ffff:203.0.113.7", "203.0.113.7"),
            ("  203.0.113.7  ", "203.0.113.7"),
        ],
    )
    def test_valid_forms_are_normalised(self, value: str, expected: str) -> None:
        assert client_ip(_request(f"1.1.1.1, {value}"), True) == expected

    @pytest.mark.parametrize(
        "value",
        [
            "unknown",
            "not-an-ip",
            "999.1.1.1",
            "1.2.3",
            "'; DROP TABLE users;--",
            "203.0.113.7/24",
            "",
            "   ",
        ],
    )
    def test_malformed_entries_fall_back_to_the_peer(self, value: str) -> None:
        assert client_ip(_request(f"1.1.1.1, {value}"), True) == PEER

    def test_a_trailing_comma_is_malformed_not_a_shorter_list(self) -> None:
        assert client_ip(_request("203.0.113.7,"), True) == PEER

    def test_an_oversized_header_is_refused(self) -> None:
        assert client_ip(_request("1.1.1.1, " + "9" * 5000), True) == PEER


class TestPaddedHeader:
    """Only the entries the trusted proxies wrote count, however much the caller pads."""

    def test_a_long_spoofed_prefix_does_not_hide_the_real_address(self) -> None:
        padded = ", ".join(["6.6.6.6"] * 800) + ", 198.51.100.9"
        assert len(padded) > 5000
        assert client_ip(_request(padded), True) == "198.51.100.9"

    def test_padding_across_several_header_lines(self) -> None:
        lines = [", ".join(["6.6.6.6"] * 400)] * 3 + ["198.51.100.9"]
        assert client_ip(_request(*lines), True) == "198.51.100.9"

    def test_padding_with_two_hops(self) -> None:
        padded = ", ".join(["6.6.6.6"] * 800) + ", 198.51.100.9, 10.0.0.2"
        assert client_ip(_request(padded), True, trusted_hops=2) == "198.51.100.9"

    def test_an_oversized_trusted_entry_is_refused(self) -> None:
        assert client_ip(_request("198.51.100.9, " + "1" * 100), True) == PEER


def test_dict_headers_work_too() -> None:
    request = SimpleNamespace(
        headers={"x-forwarded-for": "1.1.1.1, 203.0.113.7"},
        client=SimpleNamespace(host=PEER),
    )
    assert client_ip(request, True) == "203.0.113.7"  # type: ignore[arg-type]


class TestSettings:
    def test_defaults(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.trust_proxy_headers is False
        assert settings.trusted_proxy_hops == 1

    def test_new_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_TRUST_PROXY_HEADERS", "true")
        monkeypatch.setenv("RAG_TRUSTED_PROXY_HOPS", "2")
        settings = Settings(_env_file=None)
        assert settings.trust_proxy_headers is True
        assert settings.trusted_proxy_hops == 2

    def test_old_name_still_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_WIDGET_TRUST_PROXY_HEADERS", "true")
        settings = Settings(_env_file=None)
        assert settings.trust_proxy_headers is True
        assert settings.widget_trust_proxy_headers is True

    def test_new_name_wins_over_the_old(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_TRUST_PROXY_HEADERS", "false")
        monkeypatch.setenv("RAG_WIDGET_TRUST_PROXY_HEADERS", "true")
        assert Settings(_env_file=None).trust_proxy_headers is False

    def test_keyword_names(self) -> None:
        assert Settings(_env_file=None, trust_proxy_headers=True).trust_proxy_headers is True
        assert Settings(_env_file=None, widget_trust_proxy_headers=True).trust_proxy_headers is True

    @pytest.mark.parametrize("bad", ["0", "-1", "11"])
    def test_hops_are_bounded(self, bad: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_TRUSTED_PROXY_HOPS", bad)
        with pytest.raises(ValueError):
            Settings(_env_file=None)


class TestRequestClientIp:
    """The one entry point every caller uses: it reads the app's settings."""

    def _with_settings(self, request: Request, settings: Settings) -> Request:
        request.scope["app"] = SimpleNamespace(state=SimpleNamespace(settings=settings))
        return request

    def test_uses_trust_and_hops_from_settings(self) -> None:
        settings = Settings(_env_file=None, trust_proxy_headers=True, trusted_proxy_hops=2)
        request = self._with_settings(_request("1.1.1.1, 198.51.100.4, 203.0.113.7"), settings)
        assert request_client_ip(request) == "198.51.100.4"

    def test_trust_off_ignores_the_header(self) -> None:
        request = self._with_settings(_request("1.1.1.1"), Settings(_env_file=None))
        assert request_client_ip(request) == PEER

    def test_no_app_settings_means_no_trust(self) -> None:
        request = _request("1.1.1.1")
        request.scope["app"] = SimpleNamespace(state=SimpleNamespace())
        assert request_client_ip(request) == PEER


def test_every_caller_goes_through_the_shared_helper() -> None:
    """A caller that reads X-Forwarded-For itself, or passes only the trust flag, would
    ignore the hop count and be spoofable again."""
    import pathlib
    import re

    src = pathlib.Path(__file__).resolve().parents[2] / "src" / "callobuzz_rag"
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        if path.name == "security.py" and path.parent.name == "widget":
            continue
        text = path.read_text(encoding="utf-8")
        reads_header = re.search(r"""["']x-forwarded-for["']""", text, re.IGNORECASE)
        bare_call = "client_ip(" in text.replace("request_client_ip(", "")
        if reads_header or bare_call or "request.client.host" in text:
            offenders.append(str(path.relative_to(src)))
    assert offenders == []
