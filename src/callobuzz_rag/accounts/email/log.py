"""Development provider: writes the whole message to the log instead of sending it.

The text body goes in full so a developer sees verification codes. Refused in
production (`factory.email_provider_from_settings`), so codes never reach those logs.
"""

import logging

from callobuzz_rag.accounts.email.base import EmailProvider, OutgoingEmail

logger = logging.getLogger(__name__)


class LogEmailProvider(EmailProvider):
    name = "log"

    async def send(self, email: OutgoingEmail) -> None:
        logger.warning("DEV EMAIL to=%s subject=%s\n%s", email.to, email.subject, email.text)
