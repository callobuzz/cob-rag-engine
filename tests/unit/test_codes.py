"""accounts/codes.py: e-mailed 6-digit codes (accounts A3)."""

from datetime import UTC, datetime, timedelta

import pytest

from callobuzz_rag.accounts.codes import MAX_ATTEMPTS, code_hmac, consume_code, issue_code
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.email_code import EmailCode
from tests.unit.accounts_fakes import FakeDB

SECRET = "k" * 40
EMAIL = "asha@shree.example"


def _settings(secret: str = SECRET) -> Settings:
    return Settings(_env_file=None, session_secret=secret)  # type: ignore[arg-type]


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 10**6:06d}"


async def _consume(
    db: FakeDB, code: str, settings: Settings | None = None, **kw: object
) -> EmailCode:
    purposes = kw.pop("purposes", ("reset",))
    return await consume_code(
        db.session(),
        settings=settings or _settings(),
        email=str(kw.pop("email", EMAIL)),
        purposes=purposes,  # type: ignore[arg-type]
        code=code,
    )


async def test_issue_stores_only_an_hmac_and_expires_in_ten_minutes() -> None:
    db = FakeDB()
    code = await issue_code(
        db.session(),
        settings=_settings(),
        email=" Asha@Shree.Example ",
        purpose="reset",
        user_id=None,
    )
    assert len(code) == 6 and code.isdigit()
    (row,) = db.all(EmailCode)
    assert row.email == EMAIL
    assert row.code_hash == code_hmac(SECRET, purpose="reset", email=EMAIL, code=code)
    assert code not in row.code_hash
    assert timedelta(minutes=9) < row.expires_at - datetime.now(UTC) <= timedelta(minutes=10)
    assert row.attempts == 0 and row.used_at is None and row.payload == {}


async def test_setup_codes_last_a_day() -> None:
    db = FakeDB()
    await issue_code(
        db.session(), settings=_settings(), email=EMAIL, purpose="superadmin_setup", user_id=None
    )
    (row,) = db.all(EmailCode)
    assert row.expires_at - datetime.now(UTC) > timedelta(hours=23)


async def test_unknown_purpose_is_refused() -> None:
    with pytest.raises(ValueError):
        await issue_code(
            FakeDB().session(), settings=_settings(), email=EMAIL, purpose="x", user_id=None
        )


async def test_consume_marks_used_and_cannot_be_reused() -> None:
    db = FakeDB()
    code = await issue_code(
        db.session(), settings=_settings(), email=EMAIL, purpose="reset", user_id=None
    )
    row = await _consume(db, code)
    assert row.used_at is not None
    with pytest.raises(APIError) as exc:
        await _consume(db, code)
    assert exc.value.code == "code_expired"


async def test_issue_supersedes_older_codes() -> None:
    db = FakeDB()
    first = await issue_code(
        db.session(), settings=_settings(), email=EMAIL, purpose="reset", user_id=None
    )
    old_row = db.all(EmailCode)[0]
    old_row.created_at = datetime.now(UTC) - timedelta(minutes=1)
    second = await issue_code(
        db.session(), settings=_settings(), email=EMAIL, purpose="reset", user_id=None
    )
    assert old_row.used_at is not None
    if first != second:
        with pytest.raises(APIError) as exc:
            await _consume(db, first)
        assert exc.value.code == "invalid_code"  # checked against the newest code
    assert (await _consume(db, second)).used_at is not None


async def test_other_purposes_are_not_superseded() -> None:
    db = FakeDB()
    verify = await issue_code(
        db.session(), settings=_settings(), email=EMAIL, purpose="verify", user_id=None
    )
    await issue_code(db.session(), settings=_settings(), email=EMAIL, purpose="reset", user_id=None)
    assert (await _consume(db, verify, purposes=("verify",))).purpose == "verify"


async def test_purposes_tuple() -> None:
    db = FakeDB()
    code = await issue_code(
        db.session(), settings=_settings(), email=EMAIL, purpose="superadmin_setup", user_id=None
    )
    with pytest.raises(APIError) as exc:
        await _consume(db, code, purposes=("reset",))
    assert exc.value.code == "code_expired"
    row = await _consume(db, code, purposes=("reset", "superadmin_setup"))
    assert row.purpose == "superadmin_setup"


async def test_wrong_code_counts_and_commits_before_raising() -> None:
    db = FakeDB()
    code = await issue_code(
        db.session(), settings=_settings(), email=EMAIL, purpose="reset", user_id=None
    )
    with pytest.raises(APIError) as exc:
        await _consume(db, _wrong(code))
    assert exc.value.status_code == 400 and exc.value.code == "invalid_code"
    (row,) = db.all(EmailCode)
    assert row.attempts == 1
    assert db.commits == 1  # persisted although the request errors


async def test_five_wrong_attempts_exhaust_the_code() -> None:
    db = FakeDB()
    code = await issue_code(
        db.session(), settings=_settings(), email=EMAIL, purpose="reset", user_id=None
    )
    codes = []
    for _ in range(MAX_ATTEMPTS):
        with pytest.raises(APIError) as exc:
            await _consume(db, _wrong(code))
        codes.append(exc.value.code)
    assert codes == ["invalid_code"] * 4 + ["code_expired"]
    with pytest.raises(APIError) as exc:
        await _consume(db, code)  # even the right one
    assert exc.value.code == "code_expired"


async def test_expired_code() -> None:
    db = FakeDB()
    code = await issue_code(
        db.session(), settings=_settings(), email=EMAIL, purpose="reset", user_id=None
    )
    db.all(EmailCode)[0].expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(APIError) as exc:
        await _consume(db, code)
    assert exc.value.code == "code_expired"


async def test_no_code_at_all() -> None:
    with pytest.raises(APIError) as exc:
        await _consume(FakeDB(), "123456")
    assert exc.value.code == "code_expired"


async def test_verifies_across_secret_rotation() -> None:
    db = FakeDB()
    old = "o" * 40
    code = await issue_code(
        db.session(), settings=_settings(old), email=EMAIL, purpose="reset", user_id=None
    )
    row = await _consume(db, code, settings=_settings(f"{SECRET},{old}"))
    assert row.used_at is not None


async def test_email_is_normalized_on_consume() -> None:
    db = FakeDB()
    code = await issue_code(
        db.session(), settings=_settings(), email=EMAIL, purpose="reset", user_id=None
    )
    assert (await _consume(db, code, email="ASHA@shree.example ")).used_at is not None
