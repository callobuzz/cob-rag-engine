"""The questions COB offers when it opens: those of the screen the person is on, else generic."""

from typing import Final

from callobuzz_rag.cob.config import generic_starters
from callobuzz_rag.cob.types import CobRole, DocsIndex, allowed_audiences

MAX_STARTERS: Final = 4
_ADMIN_ROLES: Final = frozenset({CobRole.ADMIN, CobRole.OPERATOR_OWNER})


def starters_for(
    index: DocsIndex, *, role: CobRole, route: str, language: str
) -> tuple[str | None, list[str]]:
    """(slug of the screen's help page or None, up to four starter questions).

    The page must be one the role may read: an owner on an operator screen gets the generic
    list, so the questions never reveal that the screen exists."""
    page = index.page_for_route(route)
    if page is not None and page.audience.value in allowed_audiences(role):
        questions = list(page.starters)
        if role in _ADMIN_ROLES:
            questions += list(page.admin_starters)
        if questions:
            return page.slug, questions[:MAX_STARTERS]
    return None, generic_starters(language)
