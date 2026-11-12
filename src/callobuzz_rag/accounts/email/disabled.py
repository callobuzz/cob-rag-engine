"""No provider configured (production without SMTP): every send is refused.

Flows check `email_configured(settings)` before creating anything and answer 503
`email_not_configured`; this provider is the backstop.
"""

from callobuzz_rag.accounts.email.base import EmailProvider, OutgoingEmail
from callobuzz_rag.core.exceptions import EmailNotConfiguredError


class DisabledEmailProvider(EmailProvider):
    name = "disabled"

    async def send(self, email: OutgoingEmail) -> None:
        raise EmailNotConfiguredError("E-mail is not configured (set RAG_SMTP_HOST)")
