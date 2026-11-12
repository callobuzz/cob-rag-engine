"""Redis and Qdrant credentials, and the URL encoding they depend on.

Redis and Qdrant both ship with authentication OFF — that is upstream's
default. Until 2026-08-13 neither had a credential here either, so loopback
binding in docker-compose.yml was the entire defence: correct, but a single
point of failure that one stray `RAG_BIND_ADDR=0.0.0.0` undoes silently.

Both credentials are now optional in this layer and REQUIRED by
docker-compose.yml, matching how the Postgres password already work.
The split matters: the engine still runs against a store that has no auth (an
existing dev box, a managed instance behind a network policy), while a
deployment cannot end up unauthenticated by forgetting a variable.

The encoding tests are the ones that earn their keep. An unencoded `@` in a
password does not raise — it re-parses the URL so that everything after it
becomes the HOST, and the client cheerfully connects somewhere else and reports
a name-resolution error that says nothing about passwords. Generated secrets
hit this regularly: `secrets.token_urlsafe` emits `-` and `_`, but base64 and
most password managers emit `/`, `+` and `@`.
"""

from urllib.parse import urlparse

import pytest

from callobuzz_rag.core.config import Settings

# Every character that is structurally significant in a URL, in one password.
NASTY = "p@ss:w/rd#1?&x"

# Hosts and ports are pinned explicitly rather than left to the field defaults.
# pydantic-settings reads `.env` from the CURRENT WORKING DIRECTORY, and the
# directory pytest runs from may hold one that sets RAG_REDIS_HOST=localhost. So
# an earlier version of this file passed from one directory and failed from
# another, on the host rather than on anything it meant to test. These tests
# are about URL COMPOSITION; they must not depend on what a default resolves to
# or on where pytest was invoked.
HOST = {
    "redis_host": "10.0.0.1",
    "redis_port": 6380,
    "qdrant_host": "10.0.0.2",
    "qdrant_port": 6333,
    "db_host": "10.0.0.3",
    "db_port": 5433,
}


def _s(**kw: object) -> Settings:
    return Settings(**{**HOST, **kw})  # type: ignore[arg-type]


class TestRedisCredential:
    def test_absent_password_produces_no_auth_section(self) -> None:
        # The other half of the assertion: if a password were always emitted,
        # the tests below would pass while proving nothing about the empty case,
        # and every existing unauthenticated dev Redis would break.
        assert _s(redis_password="").redis_url == "redis://10.0.0.1:6380/0"

    def test_password_appears_in_the_url(self) -> None:
        url = _s(redis_password="hunter2").redis_url
        assert url == "redis://:hunter2@10.0.0.1:6380/0"

    def test_no_username_is_emitted(self) -> None:
        # Classic `requirepass` authenticates on a password alone. Emitting a
        # username would send AUTH <user> <pass>, which Redis 7 routes through
        # ACLs and rejects for a server that only has requirepass set.
        assert urlparse(_s(redis_password="hunter2").redis_url).username == ""

    def test_special_characters_do_not_move_the_host(self) -> None:
        parsed = urlparse(_s(redis_password=NASTY).redis_url)
        assert parsed.hostname == "10.0.0.1"
        assert parsed.port == 6380
        assert "@" not in (parsed.password or "")

    def test_result_backend_uses_the_next_db_and_the_same_password(self) -> None:
        s = _s(redis_password=NASTY)
        broker, backend = urlparse(s.celery_broker_url), urlparse(s.celery_result_backend)
        assert broker.password == backend.password
        assert broker.hostname == backend.hostname == "10.0.0.1"
        assert (broker.path, backend.path) == ("/0", "/1")

    def test_broker_url_is_the_redis_url(self) -> None:
        s = _s(redis_password=NASTY)
        assert s.celery_broker_url == s.redis_url


class TestQdrantCredential:
    def test_absent_key_is_none_not_empty_string(self) -> None:
        # AsyncQdrantClient treats "" as a key and sends an empty `api-key`
        # header, which an authenticated Qdrant rejects. None skips the header.
        assert _s(qdrant_api_key="").qdrant_api_key_or_none is None

    def test_present_key_is_returned(self) -> None:
        assert _s(qdrant_api_key="k@y/1").qdrant_api_key_or_none == "k@y/1"

    def test_key_is_not_in_the_url(self) -> None:
        # Qdrant authenticates with a header. A key in the URL would be a bug
        # and would also leak into request logs.
        s = _s(qdrant_api_key="k@y/1")
        assert s.qdrant_url == "http://10.0.0.2:6333"
        assert "k@y" not in s.qdrant_url

    def test_key_is_not_rendered_by_repr(self) -> None:
        # SecretStr, per the engine's "secrets use SecretStr" rule.
        assert "k@y/1" not in repr(_s(qdrant_api_key="k@y/1"))


class TestPostgresCredentialEncoding:
    """Same latent bug, same fix — db_url interpolated the password raw."""

    def test_special_characters_do_not_move_the_host(self) -> None:
        parsed = urlparse(_s(db_password=NASTY).db_url)
        assert parsed.hostname == "10.0.0.3"
        assert parsed.port == 5433
        assert parsed.path == "/rag_kernel"

    def test_username_survives(self) -> None:
        assert urlparse(_s(db_password=NASTY, db_user="rag").db_url).username == "rag"


@pytest.mark.parametrize("password", ["a/b", "a@b", "a:b", "a#b", "a?b", "a%b", "a b"])
def test_every_structural_character_round_trips(password: str) -> None:
    """Decoding the URL must give back exactly what was configured.

    This is the property that actually matters: whatever encoding is used, the
    server has to receive the original bytes, or AUTH fails with a message that
    blames the password rather than the URL.
    """
    parsed = urlparse(_s(redis_password=password).redis_url)
    from urllib.parse import unquote

    assert unquote(parsed.password or "") == password
    assert parsed.hostname == "10.0.0.1"
