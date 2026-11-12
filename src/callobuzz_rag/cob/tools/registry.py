"""The tool registry: every COB tool by name, and which ones a role may use."""

from callobuzz_rag.cob.tools.base import ActionTool, CobTool
from callobuzz_rag.cob.types import CobRole

REGISTRY: dict[str, CobTool] = {}


def register(tool: CobTool) -> CobTool:
    if tool.name in REGISTRY:
        raise ValueError(f"COB tool {tool.name!r} is registered twice")
    REGISTRY[tool.name] = tool
    return tool


def tools_for(role: CobRole) -> list[CobTool]:
    """The tools `role` may use, in registry order. The public COB has none: it answers from
    the docs only and never reads an account."""
    if role == CobRole.PUBLIC:
        return []
    return [t for t in REGISTRY.values() if role in t.roles]


# COB5 actions
def actions() -> list["ActionTool"]:
    """Every action tool (the ones that change something after a confirmed card)."""
    return [t for t in REGISTRY.values() if isinstance(t, ActionTool)]


# end COB5 actions
