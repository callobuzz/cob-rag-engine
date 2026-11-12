import pytest
from pydantic import ValidationError

from callobuzz_rag.core.config import Settings

_OK = {
    "whatsapp_enabled": True,
    "whatsapp_app_id": "555",
    "whatsapp_app_secret": "s3cret",
    "whatsapp_verify_token": "tok",
}


def test_disabled_needs_nothing() -> None:
    assert Settings(whatsapp_enabled=False).whatsapp_enabled is False


def test_enabled_with_the_meta_app_loads() -> None:
    assert Settings(**_OK).whatsapp_enabled is True


@pytest.mark.parametrize(
    "missing", ["whatsapp_app_id", "whatsapp_app_secret", "whatsapp_verify_token"]
)
@pytest.mark.parametrize("blank", ["", "   "])
def test_enabled_without_a_meta_setting_is_refused(missing: str, blank: str) -> None:
    with pytest.raises(ValidationError, match="RAG_WHATSAPP_ENABLED needs"):
        Settings(**{**_OK, missing: blank})


@pytest.mark.parametrize(
    "bad",
    [
        {"whatsapp_graph_url": "http://graph.facebook.com"},
        {"whatsapp_graph_version": "23"},
        {"whatsapp_graph_version": "v23"},
    ],
)
def test_enabled_with_a_bad_graph_address_or_version_is_refused(bad: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        Settings(**{**_OK, **bad})
