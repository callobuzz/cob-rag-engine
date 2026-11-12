"""The one-off instruction COB adds to every turn: who is asking and where.

It states the role, what that role may do, and the screen the person is on, so answers are
role-aware without a prompt of their own. It never carries ids, names or data.
"""

from callobuzz_rag.cob.types import CobCaller, CobRole, DocPage, allowed_audiences

_ROLE_NOTES = {
    CobRole.MEMBER: (
        "The person is a workspace member. They cannot delete things, manage keys or publish; "
        "when they ask how, say an owner of the workspace must do it."
    ),
    CobRole.OWNER: (
        "The person is a workspace owner. They can manage knowledge bases, keys, the assistant "
        "and the team."
    ),
    CobRole.ADMIN: (
        "The person is the platform operator. They may also ask about server settings, "
        "deployment, customers and platform costs."
    ),
    CobRole.OPERATOR_OWNER: (
        "The person is the platform operator, working inside the operator's own workspace. "
        "They may also ask about server settings and deployment; actions here affect this "
        "workspace only, and the operator view has the platform screens."
    ),
    CobRole.PUBLIC: "The person is not signed in. Answer only from the public documentation.",
}

# COB9: the knowledge base holds the engine's own docs and pages the operator added.
SOURCES_NOTE = (
    "The documents come from two places. Pages about how the product works (screens, "
    "settings, steps) come with the engine and are authoritative about the product: if a "
    "page the operator added disagrees about how the product behaves, follow the engine's "
    "pages. Pages the operator added are authoritative about the operator's own business "
    "(support hours, prices, contacts)."
)


def role_note(caller: CobCaller, page: DocPage | None) -> str:
    """The `one_off_instructions` text for a turn."""
    parts = [_ROLE_NOTES[caller.role], SOURCES_NOTE]
    if page is not None and page.audience.value in allowed_audiences(caller.role):
        where = f"They are on the screen '{page.title}'"
        parts.append(f"{where} ({page.route})." if page.route else f"{where}.")
    return " ".join(parts)
