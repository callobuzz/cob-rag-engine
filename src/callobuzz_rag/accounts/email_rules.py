"""The one rule for what counts as an e-mail address, everywhere the engine accepts one.

An address must be well formed and its domain must not be reserved. Real domains are
always accepted. Special-use names (`.local`, `.test`, `.example`, `.localhost`,
`.invalid`) are accepted only when `RAG_ENV=dev`, so a developer can sign in as
`admin@callobuzz.local` on a laptop, while a live deployment refuses an address that can
never receive mail. `.arpa` and `.onion` are refused in both modes: nobody develops with
them.

The dashboard applies the same rule (`services/dashboard/src/lib/forms/email-rule.ts`),
told which mode the engine is in by `special_use_emails_allowed` on
`GET /api/v1/public/branding`; `scripts/setup.py` applies it to the super-admin prompt.
Keep the three in step.

This module imports nothing from `core.config` at load time: `Settings` validates the
super-admin address with it.
"""

import unicodedata
from functools import cache
from typing import Annotated, Final

from email_validator import EmailNotValidError, validate_email
from pydantic import AfterValidator, Field

# Reserved by RFC 6761 for documentation and local use, so they never receive real mail.
SPECIAL_USE_TLDS: Final = frozenset({"local", "test", "example", "localhost", "invalid"})

MAX_LENGTH: Final = 320


class EmailRuleError(ValueError):
    """The address is refused. The message is written for the person who typed it."""


def special_use_tld(address: str) -> str | None:
    """The reserved top-level name the address ends in, or None for a real domain."""
    domain = address.rsplit("@", 1)[-1].rstrip(".").lower()
    tld = domain.rsplit(".", 1)[-1]
    return tld if tld in SPECIAL_USE_TLDS and "." in domain else None


def check_email(raw: str, *, allow_special_use: bool) -> str:
    """The address, trimmed and with its domain lower-cased; raises `EmailRuleError`.

    `allow_special_use` is True in a development deployment. The local part keeps its
    case (storage lower-cases it separately, `passwords.normalize_email`).
    """
    text = unicodedata.normalize("NFKC", raw).strip()
    if not text or len(text) > MAX_LENGTH:
        raise EmailRuleError("Enter an email address like priya@yourcompany.in.")
    tld = special_use_tld(text)
    if tld is not None and not allow_special_use:
        raise EmailRuleError(
            f"Addresses ending in .{tld} cannot receive mail. Use a real address like "
            "priya@yourcompany.in."
        )
    # A reserved name is allowed here, so it is checked for shape as an ordinary one.
    shape = text if tld is None else text.rsplit(".", 1)[0] + ".com"
    try:
        validated = validate_email(shape, check_deliverability=False)
    except EmailNotValidError as exc:
        raise EmailRuleError(f"That is not a valid email address ({exc}).") from exc
    if tld is None:
        return validated.normalized
    return f"{validated.local_part}@{validated.domain.rsplit('.', 1)[0]}.{tld}"


# --- Which mode this process is in -------------------------------------------------

_override: bool | None = None


def configure(*, is_prod: bool | None) -> None:
    """Set the mode explicitly (the app does, from its settings); None goes back to
    reading `RAG_ENV` the first time it is needed."""
    global _override
    _override = is_prod
    _env_is_prod.cache_clear()


@cache
def _env_is_prod() -> bool:
    try:
        from callobuzz_rag.core.config import Settings

        return Settings().is_prod
    except Exception:  # an unreadable configuration must not loosen the rule
        return True


def special_use_allowed() -> bool:
    """True in a development deployment."""
    return not (_override if _override is not None else _env_is_prod())


def _validate(value: str) -> str:
    try:
        return check_email(value, allow_special_use=special_use_allowed())
    except EmailRuleError as exc:
        raise ValueError(str(exc)) from exc


# For request bodies and settings, in place of pydantic's `EmailStr`, which refuses every
# reserved name in every mode. The JSON schema is unchanged (`format: email`).
ValidatedEmail = Annotated[
    str, Field(json_schema_extra={"format": "email"}), AfterValidator(_validate)
]
