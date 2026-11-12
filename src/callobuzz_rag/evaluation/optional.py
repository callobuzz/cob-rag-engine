"""The optional answer-quality evaluation add-on (RAGAS, which brings LangChain).

The default install carries neither package. Everything that needs them imports inside a
function and calls `require_ragas()` first, so a missing add-on is one clear error instead of a
`ModuleNotFoundError` deep inside a run. The golden-set CRUD, own-judge criteria, behaviour
checks and the COB offline eval never touch RAGAS and work without it.
"""

import importlib.util

from callobuzz_rag.core.exceptions import EvaluationAddonMissing

__all__ = ["ADDON_MESSAGE", "EvaluationAddonMissing", "ragas_available", "require_ragas"]

ADDON_MESSAGE = (
    "Answer-quality evaluation needs the evaluation add-on: "
    "pip install 'cob-rag-engine[eval]' (Docker: build with INSTALL_EVAL=true)"
)

# RAGAS imports langchain_core; both must be importable for a run to work.
_REQUIRED = ("ragas", "langchain_core")


def ragas_available() -> bool:
    """True when RAGAS and LangChain are installed. Looks the packages up without importing
    them (importing RAGAS takes seconds)."""
    try:
        return all(importlib.util.find_spec(name) is not None for name in _REQUIRED)
    except (ImportError, ValueError):
        return False


def require_ragas() -> None:
    """Raise `EvaluationAddonMissing` unless the add-on is installed."""
    if not ragas_available():
        raise EvaluationAddonMissing(ADDON_MESSAGE)
