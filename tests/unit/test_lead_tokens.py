import uuid

import pytest

from callobuzz_rag.conversation.leads.tokens import (
    WithdrawTokenError,
    make_withdraw_token,
    new_withdraw_secret,
    read_withdraw_token,
    secret_hash,
    secret_matches,
    withdraw_url,
)


def test_round_trip_and_url_uses_fragment() -> None:
    lead, secret = uuid.uuid4(), new_withdraw_secret()
    token = make_withdraw_token(lead, secret)
    assert read_withdraw_token(token) == (lead, secret)
    url = withdraw_url("https://rag.shree-labs.in/", token)
    assert url == f"https://rag.shree-labs.in/privacy/withdraw#t={token}"


def test_secrets_are_random_and_only_the_hash_is_kept() -> None:
    a, b = new_withdraw_secret(), new_withdraw_secret()
    assert a != b and len(a) >= 40
    stored = secret_hash(a)
    assert len(stored) == 64 and a not in stored
    assert secret_matches(stored, a)
    assert not secret_matches(stored, b)


def test_a_lead_with_no_hash_never_matches() -> None:
    assert not secret_matches(None, "anything")
    assert not secret_matches("", "")


def test_the_link_does_not_depend_on_the_encryption_key() -> None:
    """The point of the hash: nothing in a token or its check reads RAG_ENCRYPTION_KEY, so a
    key rotation cannot break a link a visitor holds."""
    import inspect

    from callobuzz_rag.conversation.leads import tokens

    source = inspect.getsource(tokens)
    assert "LeadCrypto" not in source and "Fernet" not in source and "core.secrets" not in source


@pytest.mark.parametrize(
    "bad", ["x" * 40, "", "no-dot-here", f"{uuid.uuid4()}.", "notauuid.secret"]
)
def test_malformed_tokens_are_refused(bad: str) -> None:
    with pytest.raises(WithdrawTokenError):
        read_withdraw_token(bad)
