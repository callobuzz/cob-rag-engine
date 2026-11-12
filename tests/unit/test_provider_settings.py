"""BIL1: the three optional provider admin keys are empty by default and never printed."""

from callobuzz_rag.core.config import Settings


def test_defaults_are_empty() -> None:
    s = Settings()
    assert s.openrouter_management_key.get_secret_value() == ""
    assert s.openai_admin_key.get_secret_value() == ""
    assert s.anthropic_admin_key.get_secret_value() == ""


def test_keys_never_appear_in_repr() -> None:
    s = Settings(
        openrouter_management_key="sk-or-mgmt-SECRET1",
        openai_admin_key="sk-admin-SECRET2",
        anthropic_admin_key="sk-ant-admin-SECRET3",
    )
    text = repr(s) + str(s)
    for secret in ("SECRET1", "SECRET2", "SECRET3"):
        assert secret not in text
    assert s.openai_admin_key.get_secret_value() == "sk-admin-SECRET2"
