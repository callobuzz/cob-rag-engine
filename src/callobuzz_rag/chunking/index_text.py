"""The text a chunk is embedded and keyword-indexed with.

A chunk cut from the middle of a section often never names what it is about: "It
must be unused and in its original packaging" is about returns only because the
heading says so. Embedding the heading path with the chunk ("Help Center > Returns")
puts that back. For an imported record the path is its title, which is how the
BEIR benchmark (and every model evaluated on it) embeds documents: title, then text.

Only indexing sees this text. What is stored, returned and cited stays the chunk.
Every place that builds vectors for a chunk — ingest, re-embed, migrations, reindex —
must call `index_text`, or a chunk's vectors will disagree with its neighbours'.
"""

from collections.abc import Sequence
from functools import lru_cache


@lru_cache(maxsize=1)
def _enabled_by_default() -> bool:
    from callobuzz_rag.core.config import get_settings

    return get_settings().index_heading_context


def index_text(
    text: str,
    structure_path: Sequence[str] | None,
    *,
    heading_context: bool | None = None,
) -> str:
    """`text` preceded by its heading path, when there is one and the setting is on."""
    if heading_context is None:
        heading_context = _enabled_by_default()
    path = [part.strip() for part in structure_path or () if part and part.strip()]
    if not heading_context or not path:
        return text
    return " > ".join(path) + "\n\n" + text
