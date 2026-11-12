"""Instruction prefixes that open-weight retrieval models were trained with.

Many embedding models expect a short instruction before a search query — and some
before a passage too — and score measurably worse without it. BGE English v1.5, for
one, is evaluated on BEIR with "Represent this sentence for searching relevant
passages: " in front of every query. The engine used to embed queries bare.

`RAG_EMBEDDING_QUERY_PREFIX` / `RAG_EMBEDDING_PASSAGE_PREFIX` override the table
below; set either to an empty string to turn it off. Changing the PASSAGE prefix
changes stored vectors, so it needs a re-embed (an embedding migration); the query
prefix can change at any time.
"""

import re

_BGE_EN = "Represent this sentence for searching relevant passages: "
_BGE_ZH = "为这个句子生成表示以用于检索相关文章："  # noqa: RUF001 — the official instruction ends in a full-width colon

# (pattern on the lower-cased model name, query prefix, passage prefix)
_KNOWN: list[tuple[str, str, str]] = [
    (r"bge-(small|base|large)-en(-v1\.5)?$", _BGE_EN, ""),
    (r"bge-(small|base|large)-zh(-v1\.5)?$", _BGE_ZH, ""),
    (r"mxbai-embed-large", _BGE_EN, ""),
    (r"snowflake-arctic-embed", _BGE_EN, ""),
    (r"(multilingual-)?e5-(small|base|large)(-v2)?$", "query: ", "passage: "),
    (r"nomic-embed-text", "search_query: ", "search_document: "),
]


def default_prefixes(model: str) -> tuple[str, str]:
    """(query prefix, passage prefix) documented for `model`; ("", "") if unknown."""
    name = model.lower().strip()
    for pattern, query, passage in _KNOWN:
        if re.search(pattern, name):
            return query, passage
    return "", ""


def resolve_prefixes(
    model: str, query_override: str | None, passage_override: str | None
) -> tuple[str, str]:
    query, passage = default_prefixes(model)
    return (
        query if query_override is None else query_override,
        passage if passage_override is None else passage_override,
    )
