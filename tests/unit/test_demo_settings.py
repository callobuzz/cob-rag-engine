
import pytest
from pydantic import ValidationError

from callobuzz_rag.core.config import Settings

GOOD = {
    "demo_mode": True,
    "superadmin_email": "demo-operator@sahayak.example.com",
    "demo_owner_email": "owner@nirmalclinic.example.com",
    "demo_password": "Nirmal-Clinic-Demo-2026",
    "admin_api_key": "adm_" + "x" * 40,
    "session_secret": "s" * 48,
}


def _settings(**over: object) -> Settings:
    return Settings(**{**GOOD, **over})  # type: ignore[arg-type]


def test_defaults_are_off() -> None:
    s = Settings()
    assert s.demo_mode is False
    assert s.demo_reset_at_utc == "21:30"
    assert s.demo_model_calls_per_day == 1200
    assert s.demo_answers_per_day == 300
    assert s.demo_answers_per_visitor_per_day == 15


def test_a_complete_demo_configuration_starts() -> None:
    assert _settings().demo_mode is True


@pytest.mark.parametrize(
    ("field", "value", "word"),
    [
        ("superadmin_email", "", "RAG_SUPERADMIN_EMAIL"),
        ("demo_owner_email", "", "RAG_DEMO_OWNER_EMAIL"),
        ("demo_owner_email", "demo-operator@sahayak.example.com", "RAG_DEMO_OWNER_EMAIL"),
        ("demo_password", "", "RAG_DEMO_PASSWORD"),
        ("demo_password", "short", "RAG_DEMO_PASSWORD"),
        ("demo_password", "unbelievable", "RAG_DEMO_PASSWORD"),  # a common password
        ("admin_api_key", "", "RAG_ADMIN_API_KEY"),
        ("session_secret", "", "RAG_SESSION_SECRET"),
        ("demo_reset_at_utc", "25:00", "demo_reset_at_utc"),
        ("demo_model_calls_per_day", 0, "demo_model_calls_per_day"),
        ("demo_answers_per_day", 0, "demo_answers_per_day"),
        ("demo_answers_per_visitor_per_day", 0, "demo_answers_per_visitor_per_day"),
    ],
)
def test_demo_mode_refuses_an_incomplete_configuration(
    field: str, value: object, word: str
) -> None:
    with pytest.raises(ValidationError) as err:
        _settings(**{field: value})
    assert word in str(err.value)


def test_nothing_is_required_when_demo_mode_is_off() -> None:
    assert Settings(demo_mode=False, demo_password="", demo_owner_email="").demo_mode is False
