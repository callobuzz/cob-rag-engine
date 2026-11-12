"""The AI disclosure and the deployer's lock on it (TV5), for every surface that is not the
widget's own config.

The deployer can require that visitors are told they talk to an AI (`trust.require_ai_disclosure`,
on by default), and decide whether the owner may word it. The widget shows the line itself; the
other surfaces have two ways to say it:

- the prompt: with the owner's switch off and the deployer's lock on, the compiled prompt still
  tells the model to say the line when asked who it is and in its first reply (`CompileTurn.
  disclosure`), and the echo filter lets that line through;
- a fixed line: WhatsApp (TV6) sends it with the first reply of a chat, so it never depends on the
  model obeying (`channels/whatsapp/inbound.py`).

An unreadable engine setting keeps the line (hiding it is the opt-in, never the failure mode).
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from callobuzz_rag.conversation.schema import AssistantConfig


async def read_trust(session_factory: Callable[[], Any]) -> tuple[bool, bool]:
    """`(required, wording editable)` from the engine settings; `(True, True)` when unreadable."""
    import logging

    from callobuzz_rag.accounts import settings_store

    try:
        trust = (await settings_store.get_engine_settings(session_factory)).trust
        return trust.require_ai_disclosure, trust.disclosure_wording_editable
    except Exception:
        logging.getLogger(__name__).warning(
            "Could not read the engine's trust settings; keeping the AI disclosure", exc_info=True
        )
        return True, True


def forced_line(
    config: "AssistantConfig", lang: str, *, required: bool, editable: bool
) -> str | None:
    """The line the visitor must be told: the deployer requires it, or the owner has it on.
    None when neither does."""
    if not (required or config.identity.ai_disclosure):
        return None
    from callobuzz_rag.conversation.surfaces import disclosure_line

    return disclosure_line(config, lang, editable=editable)
