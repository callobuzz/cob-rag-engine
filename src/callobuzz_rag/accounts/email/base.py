"""The e-mail provider interface.

Implementations: `SMTPEmailProvider` (smtp.py), `LogEmailProvider` (log.py, development
only), `DisabledEmailProvider` (disabled.py). `factory.py` picks one from the settings.
Exceptions live in `core/exceptions.py` (`EmailError` and subclasses).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class OutgoingEmail:
    """One rendered message, ready to send."""

    to: str
    subject: str
    text: str
    html: str


class EmailProvider(ABC):
    """Sends one rendered message.

    Raises `EmailTransientError` for failures worth retrying, `EmailPermanentError` for
    the rest, and `EmailNotConfiguredError` when there is nothing to send with.
    """

    name: str

    @abstractmethod
    async def send(self, email: OutgoingEmail) -> None: ...
