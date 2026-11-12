"""The website's contact form as a JSON route (LP3): `POST /api/v1/public/landing/contact`.

No credential (the page's script posts here); the form-time token, the honeypot and the Redis
limits in `landing/contact.py` stand in for a captcha. The no-JavaScript form posts to `/contact`
instead (`api/routes/landing.py`) and shares the same `submit`.

The body is read by hand, not declared as a FastAPI model: a malformed body must count against the
visitor's limit and get the very same answer as a failed anti-spam check, which FastAPI's own 422
(a list with field names) would not give.
"""

from fastapi import APIRouter, Request

from callobuzz_rag.landing import contact

router = APIRouter(prefix="/api/v1/public/landing", tags=["landing"])


@router.post(
    "/contact",
    status_code=202,
    response_model=contact.ContactAccepted,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": contact.ContactRequest.model_json_schema()}},
        }
    },
)
async def send_contact(request: Request) -> contact.ContactAccepted:
    """Send a message from the website's contact page to the support inbox.

    The body is a `ContactRequest`: name, e-mail or phone, topic, message, consent, the hidden
    `website` field and the page's `form_token`. Answers 202 once the message is stored. 422
    `landing_contact_invalid` for any form problem (the same words for a missing field and for a
    failed anti-spam check), 429 `landing_contact_rate_limited` with `Retry-After`, 503
    `landing_contact_unavailable`."""
    try:
        raw = await request.json()
    except ValueError:
        raw = None  # not JSON: submit() counts the attempt, then refuses it like any bad form
    await contact.submit(request, raw)
    return contact.ContactAccepted()
