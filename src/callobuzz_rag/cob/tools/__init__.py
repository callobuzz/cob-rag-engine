"""COB tools: what COB can look up (and, from COB5, change) on a person's behalf.

Importing this package registers the read tools in `REGISTRY`."""

from callobuzz_rag.cob.tools import (  # noqa: F401  (registers the read and action tools)
    actions,
    read,
)
from callobuzz_rag.cob.tools.registry import REGISTRY, tools_for

__all__ = ["REGISTRY", "tools_for"]
