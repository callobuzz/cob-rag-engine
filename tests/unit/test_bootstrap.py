"""The super-admin bootstrap CLI, every branch (accounts A3)."""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest

from callobuzz_rag.accounts import bootstrap
from callobuzz_rag.accounts.bootstrap import BANNER, EXIT_FAILED, EXIT_OK, EXIT_REFUSED, run
from callobuzz_rag.accounts.codes import code_hmac
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import EmailTransientError
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.email_code import EmailCode
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.user import User
from callobuzz_rag.db.models.user_session import UserSession
from tests.unit.accounts_fakes import FakeDB

SECRET = "b" * 40
EMAIL = "ops@operator.example"


def _settings(**over: Any) -> Settings:
    values: dict[str, Any] = {
        "session_secret": SECRET,
        "superadmin_email": " Ops@Operator.example ",
        "email_provider": "log",
        "dashboard_url": "https://dash.example",
        **over,
    }
    return Settings(_env_file=None, **values)


async def _run(db: FakeDB, settings: Settings | None = None, **kw: Any) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = await run(settings or _settings(), db.factory(), out=lines.append, **kw)
    return code, lines


def _user(db: FakeDB, email: str = EMAIL, **kw: Any) -> User:
    values: dict[str, Any] = {"name": "x", "is_active": True, "is_superadmin": False, **kw}
    return db.add(User(email=email, **values))


def _boot_audits(db: FakeDB) -> list[AuditLog]:
    return [a for a in db.all(AuditLog) if a.action == "superadmin.bootstrapped"]


def _printed_code(lines: list[str]) -> str:
    (line,) = [ln for ln in lines if ln.strip().startswith("code:")]
    return line.split(":", 1)[1].strip()


async def test_no_superadmin_email_skips() -> None:
    code, lines = await _run(FakeDB(), _settings(superadmin_email=""))
    assert code == EXIT_OK and lines == ["No RAG_SUPERADMIN_EMAIL; skipping"]


@pytest.mark.parametrize("debug", [False, True])
async def test_accounts_disabled_fails(debug: bool) -> None:
    db = FakeDB()
    code, lines = await _run(db, _settings(session_secret="", debug=debug))
    assert code == EXIT_FAILED and "RAG_SESSION_SECRET" in lines[0]
    assert db.all(User) == []


async def test_first_run_creates_the_account_and_prints_the_link() -> None:
    db = FakeDB()
    code, lines = await _run(db)
    assert code == EXIT_OK
    (user,) = db.all(User)
    assert user.email == EMAIL and user.is_superadmin and user.password_hash is None
    assert user.name == "Super admin"
    assert BANNER in lines
    printed = _printed_code(lines)
    (row,) = db.all(EmailCode)
    assert row.purpose == "superadmin_setup" and row.user_id == user.id
    assert row.code_hash == code_hmac(SECRET, purpose="superadmin_setup", email=EMAIL, code=printed)
    assert row.expires_at - datetime.now(UTC) > timedelta(hours=23)
    (link_line,) = [ln for ln in lines if "link:" in ln]
    link = urlsplit(link_line.split("link:", 1)[1].strip())
    assert (link.scheme, link.netloc, link.path, link.query) == (
        "https",
        "dash.example",
        "/set-password",
        "",
    )
    assert parse_qs(link.fragment) == {"email": [EMAIL], "code": [printed]}  # fragment only
    (audit,) = _boot_audits(db)
    assert audit.action == "superadmin.bootstrapped" and audit.actor_kind == "system"
    assert audit.details == {"created": True, "setup_code_issued": True}
    assert db.commits == 1


async def test_ready_when_the_password_is_set() -> None:
    db = FakeDB()
    _user(db, is_superadmin=True, password_hash="h")
    code, lines = await _run(db)
    assert code == EXIT_OK and lines == [f"Super admin ready: {EMAIL}"]
    assert db.all(EmailCode) == [] and _boot_audits(db) == []


async def test_ready_path_restores_the_flag() -> None:
    db = FakeDB()
    user = _user(db, password_hash="h")
    code, _ = await _run(db)
    assert code == EXIT_OK and user.is_superadmin is True
    assert _boot_audits(db)[0].details == {"demoted": [], "promoted": True}


async def test_no_password_yet_issues_a_new_code() -> None:
    db = FakeDB()
    _user(db, is_superadmin=True)
    code, lines = await _run(db)
    assert code == EXIT_OK and BANNER in lines and len(db.all(User)) == 1


async def test_resend_issues_a_new_code_even_with_a_password() -> None:
    db = FakeDB()
    _user(db, is_superadmin=True, password_hash="h")
    await _run(db, resend=True)
    await _run(db, resend=True)
    rows = db.all(EmailCode)
    assert len(rows) == 2 and rows[0].used_at is not None and rows[1].used_at is None


async def test_refuses_a_workspace_member() -> None:
    db = FakeDB()
    user = _user(db)
    tenant = db.add(Tenant(name="Shree", status="active", api_key_hash=""))
    db.add(TenantMember(tenant_id=tenant.id, user_id=user.id, role="owner"))
    code, lines = await _run(db)
    assert code == EXIT_REFUSED and "belongs to a workspace" in lines[0]
    assert user.is_superadmin is False and db.all(EmailCode) == []


async def test_demotes_every_other_super_admin() -> None:
    db = FakeDB()
    old = _user(db, "old-ops@operator.example", is_superadmin=True, password_hash="h")
    session = db.add(
        UserSession(
            user_id=old.id,
            refresh_token_hash="0" * 64,
            expires_at=datetime.now(UTC) + timedelta(days=1),
            absolute_expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    code, _ = await _run(db)
    assert code == EXIT_OK
    assert old.is_superadmin is False
    assert session.revoked_reason == "revoked"
    assert _boot_audits(db)[0].details["demoted"] == ["old-ops@operator.example"]


async def test_smtp_sends_now_not_through_celery() -> None:
    db = FakeDB()
    settings = _settings(email_provider="smtp", smtp_host="smtp.example", smtp_from="x@y.example")
    send = AsyncMock(return_value="smtp")
    with patch("callobuzz_rag.accounts.email.dispatch.send_now", send):
        code, lines = await _run(db, settings)
    assert code == EXIT_OK and lines == [f"Set-password e-mail sent to {EMAIL} (valid 24 h)"]
    template, to, context = send.await_args.args[:3]
    assert (template, to) == ("superadmin_setup", EMAIL)
    assert set(context) == {"code", "link", "hours"} and context["hours"] == 24
    assert BANNER not in lines  # the code is not printed when it was e-mailed


async def test_smtp_failure_says_how_to_recover() -> None:
    db = FakeDB()
    settings = _settings(email_provider="smtp", smtp_host="smtp.example", smtp_from="x@y.example")
    fail = AsyncMock(side_effect=EmailTransientError("down", smtp_code=421))
    with patch("callobuzz_rag.accounts.email.dispatch.send_now", fail):
        code, lines = await _run(db, settings)
    assert code == EXIT_FAILED
    assert "EmailTransientError" in lines[0] and "SMTP 421" in lines[0]
    assert "--resend" in lines[1] and "--print-link" in lines[1]
    assert len(db.all(User)) == 1  # the account exists; a rerun sends a new code


async def test_print_link_overrides_smtp() -> None:
    db = FakeDB()
    settings = _settings(email_provider="smtp", smtp_host="smtp.example", smtp_from="x@y.example")
    send = AsyncMock()
    with patch("callobuzz_rag.accounts.email.dispatch.send_now", send):
        code, lines = await _run(db, settings, print_link=True)
    assert code == EXIT_OK and BANNER in lines
    send.assert_not_awaited()


async def test_disabled_email_prints_even_in_production() -> None:
    db = FakeDB()
    # A production address is on a real domain: `.example` is refused outside development.
    settings = _settings(email_provider="disabled", env="prod", superadmin_email="Ops@Operator.in")
    code, lines = await _run(db, settings)
    assert code == EXIT_OK and BANNER in lines


def test_main_parses_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, bool] = {}

    async def fake_main(resend: bool, print_link: bool, restore: bool) -> int:
        seen.update(resend=resend, print_link=print_link, restore=restore)
        return 7

    monkeypatch.setattr(bootstrap, "_main", fake_main)
    assert bootstrap.main(["--resend", "--print-link"]) == 7
    assert seen == {"resend": True, "print_link": True, "restore": False}
    assert bootstrap.main(["--restore-operator-workspace"]) == 7
    assert seen["restore"] is True
