"""Server-side validation of lead submissions (the widget validates too, for speed only).

India first: a 10-digit mobile starting 6-9, typed with or without 0 / 91 / +91, becomes
+91XXXXXXXXXX. Other countries need a leading + and 8-15 digits. E-mail syntax only (no
DNS lookup inside the visitor's request).
"""

import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

from email_validator import EmailNotValidError, validate_email

from callobuzz_rag.core.exceptions import RAGKernelError

if TYPE_CHECKING:
    from callobuzz_rag.conversation.schema import Leads

_SEPARATORS = re.compile(r"[\s\-().]")
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_NAME_MAX = 120
_EMAIL_MAX = 254


class LeadValidationError(RAGKernelError):
    def __init__(self, code: str, *, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.code = code
        self.field = field
        self.reason = reason


def normalize_phone(raw: str) -> str:
    """Lead phone numbers. Wraps C2's single parser `conversation.schema.normalize_phone`
    and adds what a callback number needs on top of a business-card number: digits only,
    an Indian number must be a **mobile** (6-9 after +91; landlines are fine on the
    business card but a lead has to be reachable on a phone), and an international
    number cannot start with 0."""
    from callobuzz_rag.conversation.schema import normalize_phone as parse_phone

    value = _SEPARATORS.sub("", raw or "")
    digits = value[1:] if value.startswith("+") else value
    if not digits.isdigit():
        raise LeadValidationError("lead_invalid_phone", field="phone", reason="format")
    try:
        e164 = parse_phone(value)
    except ValueError:
        raise LeadValidationError("lead_invalid_phone", field="phone", reason="format") from None
    if e164 is None:
        raise LeadValidationError("lead_invalid_phone", field="phone", reason="format")
    if e164.startswith("+91"):
        if len(e164) != 13 or e164[3] not in "6789":
            raise LeadValidationError("lead_invalid_phone", field="phone", reason="format")
        return e164
    if e164[1] == "0":
        raise LeadValidationError("lead_invalid_phone", field="phone", reason="format")
    return e164


def normalize_email(raw: str) -> str:
    value = (raw or "").strip()
    if not value or len(value) > _EMAIL_MAX:
        raise LeadValidationError("lead_invalid_email", field="email", reason="format")
    try:
        return validate_email(value, check_deliverability=False).normalized
    except EmailNotValidError as e:
        raise LeadValidationError("lead_invalid_email", field="email", reason="format") from e


@dataclass(frozen=True)
class CleanLead:
    name: str | None
    phone: str | None
    email: str | None
    custom: dict[str, str | float] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {"name": self.name, "phone": self.phone, "email": self.email, "custom": self.custom}


def _text(value: Any) -> str:
    """Trimmed text with control characters replaced (a newline would end up in CSV cells,
    e-mail bodies and the summary prompt)."""
    if not isinstance(value, str):
        return ""
    return _CONTROL.sub(" ", value).strip()


def validate_submission(leads: "Leads", fields: dict[str, Any]) -> CleanLead:
    allowed = {f.id for f in leads.custom_fields}
    for key in ("name", "phone", "email"):
        if getattr(leads, key) != "off":
            allowed.add(key)
    for key in fields:
        if key not in allowed:
            raise LeadValidationError("lead_field_invalid", field=key, reason="unknown_field")

    def builtin(key: str) -> str | None:
        value = _text(fields.get(key))
        if not value:
            if getattr(leads, key) == "required":
                code = {"phone": "lead_invalid_phone", "email": "lead_invalid_email"}.get(
                    key, "lead_field_invalid"
                )
                raise LeadValidationError(code, field=key, reason="required")
            return None
        return value

    name = builtin("name")
    if name is not None and len(name) > _NAME_MAX:
        raise LeadValidationError("lead_field_invalid", field="name", reason="too_long")
    phone_raw, email_raw = builtin("phone"), builtin("email")
    phone = normalize_phone(phone_raw) if phone_raw else None
    email = normalize_email(email_raw) if email_raw else None

    custom: dict[str, str | float] = {}
    for spec in leads.custom_fields:
        value = _text(fields.get(spec.id))
        if not value:
            if spec.required:
                raise LeadValidationError("lead_field_invalid", field=spec.id, reason="required")
            continue
        if len(value) > spec.max_length:
            raise LeadValidationError("lead_field_invalid", field=spec.id, reason="too_long")
        if spec.type == "select":
            if value not in spec.options:
                raise LeadValidationError(
                    "lead_field_invalid", field=spec.id, reason="not_an_option"
                )
            custom[spec.id] = value
        elif spec.type == "date":
            try:
                custom[spec.id] = date.fromisoformat(value).isoformat()
            except ValueError as e:
                raise LeadValidationError(
                    "lead_field_invalid", field=spec.id, reason="not_a_date"
                ) from e
        elif spec.type == "number":
            try:
                number = float(value)
            except ValueError as e:
                raise LeadValidationError(
                    "lead_field_invalid", field=spec.id, reason="not_a_number"
                ) from e
            if not math.isfinite(number):
                raise LeadValidationError(
                    "lead_field_invalid", field=spec.id, reason="not_a_number"
                )
            custom[spec.id] = number
        else:
            custom[spec.id] = value
    return CleanLead(name=name, phone=phone, email=email, custom=custom)
