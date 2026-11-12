"""The conversation layer: assistant configuration (C2) and the director (C1)."""

# Each import registers an offer provider with the offer registry, so every process that loads
# the conversation layer (API, worker, golden runs) has them: the handoff card (C5), the lead
# offer and its pre-turn hook (C4), the sales-reply providers (C6: chips and one CTA).
import callobuzz_rag.conversation.handoff.provider
import callobuzz_rag.conversation.leads
import callobuzz_rag.conversation.replies  # noqa: F401
