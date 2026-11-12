"""One e-mail rule: real domains always, special-use names only when RAG_ENV=dev."""

from collections.abc import Iterator

import pytest
from pydantic import BaseModel, ValidationError

from callobuzz_rag.accounts import email_rules
from callobuzz_rag.accounts.email_rules import EmailRuleError, ValidatedEmail, check_email
from callobuzz_rag.core.config import Settings

SPECIAL = ["a@shop.local", "a@shop.test", "a@shop.example", "a@shop.localhost", "a@shop.invalid"]
REAL = [
    "priya@shree-labs.in",
    "Priya.S@Shree-Labs.IN",
    "priya+invoices@gmail.com",
    "owner@example.com",  # example.com is a real, registered documentation domain
    "ops@sub.domain.co.in",
]


@pytest.fixture(autouse=True)
def _reset_mode() -> Iterator[None]:
    yield
    email_rules.configure(is_prod=None)


class TestCheckEmail:
    @pytest.mark.parametrize("address", REAL)
    @pytest.mark.parametrize("allow", [True, False])
    def test_real_domains_pass_in_both_modes(self, address: str, allow: bool) -> None:
        assert check_email(address, allow_special_use=allow).lower() == address.lower()

    @pytest.mark.parametrize("address", SPECIAL)
    def test_special_use_passes_in_dev(self, address: str) -> None:
        assert check_email(address, allow_special_use=True) == address

    @pytest.mark.parametrize("address", SPECIAL)
    def test_special_use_is_refused_in_prod_with_a_clear_message(self, address: str) -> None:
        with pytest.raises(EmailRuleError) as caught:
            check_email(address, allow_special_use=False)
        message = str(caught.value)
        assert "cannot receive mail" in message
        assert "RAG_ENV" not in message  # an operator detail, not for a customer
        assert "yourcompany.in" in message

    def test_case_and_whitespace(self) -> None:
        assert check_email("  Asha@Shop.LOCAL ", allow_special_use=True) == "Asha@shop.local"
        with pytest.raises(EmailRuleError):
            check_email("Asha@Shop.TEST", allow_special_use=False)

    def test_the_domain_is_lowercased_the_local_part_is_not(self) -> None:
        assert check_email("Priya.S@Shree-Labs.IN", allow_special_use=False) == (
            "Priya.S@shree-labs.in"
        )

    @pytest.mark.parametrize(
        "address",
        [
            "",
            "   ",
            "priya",
            "priya@",
            "@shree.in",
            "priya@shree",
            "pri ya@shree.in",
            "a@b..in",
            "a@" + "x" * 300 + ".in",
            "a@@shree.in",
        ],
    )
    @pytest.mark.parametrize("allow", [True, False])
    def test_malformed_is_refused_in_both_modes(self, address: str, allow: bool) -> None:
        with pytest.raises(EmailRuleError):
            check_email(address, allow_special_use=allow)

    @pytest.mark.parametrize("address", ["a@host.arpa", "a@hidden.onion"])
    def test_arpa_and_onion_are_never_accepted(self, address: str) -> None:
        with pytest.raises(EmailRuleError):
            check_email(address, allow_special_use=True)

    def test_a_special_use_name_as_a_middle_label_is_a_real_domain(self) -> None:
        # Only the last label decides: this is a registered-style name under .in.
        assert check_email("a@test.shree.in", allow_special_use=False) == "a@test.shree.in"

    def test_a_malformed_special_use_address_is_still_malformed_in_dev(self) -> None:
        with pytest.raises(EmailRuleError):
            check_email("not an address@shop.local", allow_special_use=True)


class Body(BaseModel):
    email: ValidatedEmail


class TestPydanticType:
    def test_dev_accepts_special_use(self) -> None:
        email_rules.configure(is_prod=False)
        assert Body(email="asha@shop.test").email == "asha@shop.test"

    def test_prod_refuses_special_use(self) -> None:
        email_rules.configure(is_prod=True)
        with pytest.raises(ValidationError) as caught:
            Body(email="asha@shop.test")
        assert "cannot receive mail" in str(caught.value)

    def test_prod_accepts_real(self) -> None:
        email_rules.configure(is_prod=True)
        assert Body(email="asha@shop.in").email == "asha@shop.in"

    def test_json_schema_is_still_an_email_string(self) -> None:
        prop = Body.model_json_schema()["properties"]["email"]
        assert prop["type"] == "string"
        assert prop["format"] == "email"

    def test_without_configuration_the_environment_decides(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RAG_ENV", "prod")
        email_rules.configure(is_prod=None)
        assert email_rules.special_use_allowed() is False
        monkeypatch.setenv("RAG_ENV", "dev")
        email_rules.configure(is_prod=None)
        assert email_rules.special_use_allowed() is True


class TestEveryEngineEntryPointUsesTheRule:
    """No request body or setting may fall back to pydantic's EmailStr (which refuses
    every reserved name in dev too) or to nothing at all."""

    def test_no_emailstr_left_in_the_engine(self) -> None:
        import pathlib

        src = pathlib.Path(__file__).resolve().parents[2] / "src" / "callobuzz_rag"
        offenders = [
            str(path.relative_to(src))
            for path in src.rglob("*.py")
            if path.name != "email_rules.py" and "EmailStr" in path.read_text(encoding="utf-8")
        ]
        assert offenders == []

    @pytest.mark.parametrize("is_prod", [False, True])
    def test_invitation_body(self, is_prod: bool) -> None:
        from callobuzz_rag.api.schemas.members import InviteRequest

        email_rules.configure(is_prod=is_prod)
        if is_prod:
            with pytest.raises(ValidationError):
                InviteRequest(email="meera@clinic.local", role="member")
        else:
            assert InviteRequest(email="meera@clinic.local", role="member")

    @pytest.mark.parametrize("is_prod", [False, True])
    def test_smtp_test_body(self, is_prod: bool) -> None:
        from callobuzz_rag.api.routes.email_admin import SMTPTestRequest

        email_rules.configure(is_prod=is_prod)
        if is_prod:
            with pytest.raises(ValidationError):
                SMTPTestRequest(to="me@laptop.test")
        else:
            assert SMTPTestRequest(to="me@laptop.test")

    @pytest.mark.parametrize("is_prod", [False, True])
    def test_support_email_in_branding(self, is_prod: bool) -> None:
        from callobuzz_rag.accounts.settings_store import Branding
        from callobuzz_rag.api.routes.engine_settings import BrandingPatch

        email_rules.configure(is_prod=is_prod)
        for model in (Branding, BrandingPatch):
            if is_prod:
                with pytest.raises(ValidationError):
                    model(support_email="help@shop.invalid")
            else:
                assert model(support_email="help@shop.invalid")


class TestSuperAdminSetting:
    def test_dev_accepts_a_local_address(self) -> None:
        settings = Settings(_env_file=None, env="dev", superadmin_email="admin@callobuzz.local")
        assert settings.superadmin_email == "admin@callobuzz.local"

    def test_prod_refuses_it_naming_the_variable(self) -> None:
        with pytest.raises(ValueError, match="RAG_SUPERADMIN_EMAIL"):
            Settings(_env_file=None, env="prod", superadmin_email="admin@callobuzz.local")

    def test_prod_refusal_tells_the_operator_about_rag_env(self) -> None:
        with pytest.raises(ValueError, match="RAG_ENV=dev"):
            Settings(_env_file=None, env="prod", superadmin_email="admin@callobuzz.local")

    def test_prod_accepts_a_real_address(self) -> None:
        settings = Settings(_env_file=None, env="prod", superadmin_email="admin@callobuzz.in")
        assert settings.superadmin_email == "admin@callobuzz.in"

    def test_empty_is_allowed_in_both(self) -> None:
        for env in ("dev", "prod"):
            assert Settings(_env_file=None, env=env, superadmin_email="").superadmin_email == ""

    def test_malformed_is_refused_even_in_dev(self) -> None:
        with pytest.raises(ValueError, match="RAG_SUPERADMIN_EMAIL"):
            Settings(_env_file=None, env="dev", superadmin_email="not-an-address")
