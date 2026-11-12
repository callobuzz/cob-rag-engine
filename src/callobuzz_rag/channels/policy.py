"""What channels a plan includes (TV6): the `plans.channels` JSONB.

Missing keys mean the defaults: WhatsApp is off, and Meta's per-message fees are passed through
to the business (only ever for a number on the deployer's own WABA, see `whatsapp/fees.py`).
"""

from typing import Any, Literal

from pydantic import ValidationError

from callobuzz_rag.core.schemas import StrictRequest

WhatsAppFees = Literal["pass_through", "included"]


class ChannelsPolicy(StrictRequest):
    whatsapp: bool = False
    whatsapp_fees: WhatsAppFees = "pass_through"


def parse_channels(value: Any) -> ChannelsPolicy:
    """The stored dict as a policy; unknown keys are dropped and an invalid value falls back to
    its default, so one bad stored field never breaks the plan screens."""
    raw = value.model_dump() if isinstance(value, ChannelsPolicy) else value
    stored = {k: v for k, v in (raw if isinstance(raw, dict) else {}).items()}
    stored = {k: v for k, v in stored.items() if k in ChannelsPolicy.model_fields}
    try:
        return ChannelsPolicy.model_validate(stored)
    except ValidationError:
        good = {}
        for key, item in stored.items():
            try:
                ChannelsPolicy.model_validate({key: item})
            except ValidationError:
                continue
            good[key] = item
        return ChannelsPolicy.model_validate(good)


def normalize_channels(value: Any) -> dict[str, Any]:
    """What is stored: the full set, defaults filled in (so `{}` and every default compare equal)."""
    return parse_channels(value).model_dump()


def plan_channels(plan: Any) -> ChannelsPolicy:
    """A plan row's channels."""
    return parse_channels(getattr(plan, "channels", None))
