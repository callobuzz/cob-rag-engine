"""Shared builders for the C6 reply tests."""

from callobuzz_rag.conversation.schema import AssistantConfig


def replies_config(**scope: object) -> AssistantConfig:
    return AssistantConfig.model_validate(
        {
            "identity": {"business_name": "Shree Labs", "business_type": "clinic"},
            "scope": {
                "topics": [
                    {
                        "id": "tests",
                        "name": "Blood tests",
                        "description": "Which tests we run and how to prepare",
                        "examples": ["Do I need to fast for a lipid profile?"],
                        "quick_replies": [
                            {"label": "Fasting rules", "message": "Which tests need fasting?"},
                            {"label": "Report time", "message": "When will my report be ready?"},
                        ],
                    },
                    {
                        "id": "home",
                        "name": "Home collection",
                        "description": "Sample pickup at home in Pune",
                        "examples": [],
                    },
                ],
                **scope,
            },
        }
    )


def sales_config(cta_type: str = "whatsapp") -> AssistantConfig:
    cfg = replies_config()
    cfg.identity.card.whatsapp = "+919812345678"
    cfg.identity.card.phone = "+919812345678"
    cfg.identity.card.booking_url = "https://shree-labs.in/book"
    cfg.conversation.cta.buying_intent_cta = cta_type  # type: ignore[assignment]
    return cfg
