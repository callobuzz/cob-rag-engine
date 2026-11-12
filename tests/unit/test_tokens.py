"""accounts/tokens.py: JWT access tokens and refresh tokens (accounts A3)."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import SecretStr, ValidationError

from callobuzz_rag.accounts import tokens
from callobuzz_rag.accounts.tokens import (
    accounts_enabled,
    decode_access_token,
    issue_access_token,
    new_refresh_token,
    parse_refresh_token,
    session_secrets,
    sha256_hex,
)
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import (
    AccountsNotConfiguredError,
    TokenExpiredError,
    TokenInvalidError,
)

SECRET = "a" * 40
OLD = "b" * 40
USER, SID, TID = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _settings(secret: str = SECRET, **kw: object) -> Settings:
    return Settings(_env_file=None, session_secret=secret, **kw)  # type: ignore[arg-type]


def _claims(**over: object) -> dict[str, object]:
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "iss": "cob-rag-engine",
        "aud": "cob-rag-dashboard",
        "sub": str(USER),
        "sid": str(SID),
        "tid": str(TID),
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "typ": "access",
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None or k == "tid"}


def test_round_trip_carries_exactly_the_claims() -> None:
    settings = _settings()
    token, expires_in = issue_access_token(settings, user_id=USER, session_id=SID, tenant_id=TID)
    assert expires_in == 900
    raw = jwt.decode(token, SECRET, algorithms=["HS256"], audience="cob-rag-dashboard")
    assert set(raw) == {"iss", "aud", "sub", "sid", "tid", "iat", "exp", "typ"}
    assert raw["typ"] == "access" and raw["exp"] - raw["iat"] == 900
    claims = decode_access_token(settings, token)
    assert (claims.user_id, claims.session_id, claims.tenant_id) == (USER, SID, TID)


def test_super_admin_token_has_a_null_tenant() -> None:
    settings = _settings()
    token, _ = issue_access_token(settings, user_id=USER, session_id=SID, tenant_id=None)
    assert decode_access_token(settings, token).tenant_id is None


def test_lifetime_follows_the_setting() -> None:
    settings = _settings(access_token_minutes=5)
    assert issue_access_token(settings, user_id=USER, session_id=SID, tenant_id=None)[1] == 300


@pytest.mark.parametrize(
    "over",
    [
        {"aud": "someone-else"},
        {"iss": "evil"},
        {"typ": "refresh"},
        {"sub": "not-a-uuid"},
        {"sid": None, "tid": None},  # missing a required claim
    ],
)
def test_wrong_claims_are_invalid(over: dict[str, object]) -> None:
    token = jwt.encode(_claims(**over), SECRET, algorithm="HS256")
    with pytest.raises(TokenInvalidError):
        decode_access_token(_settings(), token)


def test_other_algorithms_are_refused() -> None:
    none_token = jwt.encode(_claims(), None, algorithm="none")
    hs512 = jwt.encode(_claims(), SECRET * 2, algorithm="HS512")
    for token in (none_token, hs512, "garbage", ""):
        with pytest.raises(TokenInvalidError):
            decode_access_token(_settings(), token)


def test_wrong_secret_is_invalid() -> None:
    token = jwt.encode(_claims(), "c" * 40, algorithm="HS256")
    with pytest.raises(TokenInvalidError):
        decode_access_token(_settings(), token)


def test_expired_beyond_the_leeway() -> None:
    now = datetime.now(UTC)
    stale = jwt.encode(_claims(exp=now - timedelta(seconds=31)), SECRET, algorithm="HS256")
    with pytest.raises(TokenExpiredError):
        decode_access_token(_settings(), stale)
    fresh = jwt.encode(_claims(exp=now - timedelta(seconds=10)), SECRET, algorithm="HS256")
    assert decode_access_token(_settings(), fresh).user_id == USER  # within 30 s


def test_rotation_list_verifies_tokens_signed_by_an_old_secret() -> None:
    old_token, _ = issue_access_token(_settings(OLD), user_id=USER, session_id=SID, tenant_id=TID)
    rotated = _settings(f"{SECRET},{OLD}")
    assert decode_access_token(rotated, old_token).session_id == SID
    new_token, _ = issue_access_token(rotated, user_id=USER, session_id=SID, tenant_id=TID)
    jwt.decode(new_token, SECRET, algorithms=["HS256"], audience="cob-rag-dashboard")  # signs


def test_expired_under_an_old_secret_is_still_expired() -> None:
    stale = jwt.encode(
        _claims(exp=datetime.now(UTC) - timedelta(minutes=5)), OLD, algorithm="HS256"
    )
    with pytest.raises(TokenExpiredError):
        decode_access_token(_settings(f"{SECRET},{OLD}"), stale)


@pytest.mark.parametrize("secret", ["short", f"{SECRET},short"])
def test_secret_under_32_chars_is_a_startup_error(secret: str) -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(_env_file=None, session_secret=secret)  # type: ignore[arg-type]


def test_secret_list_is_trimmed() -> None:
    settings = Settings(_env_file=None, session_secret=f" {SECRET} , {OLD} ")  # type: ignore[arg-type]
    assert session_secrets(settings) == [SECRET, OLD]


def test_no_secret_disables_accounts_outside_debug() -> None:
    settings = Settings(_env_file=None)
    with pytest.raises(AccountsNotConfiguredError):
        session_secrets(settings)
    assert accounts_enabled(settings) is False
    with pytest.raises(AccountsNotConfiguredError):
        decode_access_token(settings, "x.y.z")


def test_debug_uses_one_random_secret_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tokens, "_debug_secret", None)
    settings = Settings(_env_file=None, debug=True)
    first = session_secrets(settings)
    assert len(first) == 1 and len(first[0]) >= 32
    assert session_secrets(settings) == first
    assert accounts_enabled(settings) is True
    assert Settings(_env_file=None, session_secret=SecretStr(SECRET)).debug is False


def test_refresh_token_format_and_parse() -> None:
    token = new_refresh_token(SID)
    assert token.startswith(f"rt_{SID.hex}_") and len(token) == 3 + 32 + 1 + 43
    assert parse_refresh_token(token) == (SID, token)
    assert new_refresh_token(SID) != token
    for bad in ("", "rt_", "xx_" + SID.hex + "_abc", f"rt_{SID.hex}_", "rt_nothex_abc", "rt_abc"):
        assert parse_refresh_token(bad) is None


def test_sha256_hex() -> None:
    assert sha256_hex("abc") == ("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
