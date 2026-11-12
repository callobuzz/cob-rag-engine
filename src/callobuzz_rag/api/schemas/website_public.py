"""Bodies of the public website-freshness routes (BIZ2): the push link and the widget sensor."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from callobuzz_rag.core.schemas import StrictRequest


class LearnPushRequest(StrictRequest):
    """IndexNow-shaped, so tools that can post to a custom endpoint can use it as is.

    `host`, `keyLocation` and an IndexNow `key` are accepted and ignored (the key in the address
    is the credential; the knowledge base's own sites decide what is in scope). Only a body
    `key` that is one of our own update-link keys (`lk_...`) must be the one in the address.
    """

    model_config = ConfigDict(populate_by_name=True)

    url: str | None = Field(default=None, max_length=2048, description="One changed page.")
    url_list: list[str] | None = Field(
        default=None,
        alias="urlList",
        max_length=100,
        description="Up to 100 changed pages.",
    )
    host: str | None = Field(default=None, max_length=255)
    key: str | None = Field(default=None, max_length=64)
    key_location: str | None = Field(default=None, alias="keyLocation", max_length=2048)

    @model_validator(mode="after")
    def _needs_a_url(self) -> Self:
        if not self.url and not self.url_list:
            raise ValueError("Send `url` or `urlList`.")
        return self

    def urls(self) -> list[str]:
        found = ([self.url] if self.url else []) + list(self.url_list or [])
        return [u for u in found if u and len(u) <= 2048]


class LearnAcceptedOut(BaseModel):
    accepted: int = Field(description="Pages that will be read again now.")
    rejected: int = Field(description="Pages that are not on this knowledge base's sites.")


class WidgetPageRequest(StrictRequest):
    """What the widget tells the engine about the page it is on: its address and a short code of
    its text. Never the text itself."""

    page_url: str = Field(..., max_length=2048)
    fingerprint: str = Field(..., pattern=r"^[0-9a-f]{16}$")
