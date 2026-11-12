"""Python client for cob-rag-engine.

from cob_rag_client import CobRag

rag = CobRag("http://127.0.0.1:8000", api_key="ck_...")
rag.ingest("handbook.pdf", wait=True)
print(rag.query("What is the refund policy?")["answer"])
for token in rag.query_stream("Summarise the handbook"):
    print(token, end="", flush=True)
"""

from cob_rag_client.client import AsyncCobRag, CobRag, CobRagError

__all__ = ["AsyncCobRag", "CobRag", "CobRagError"]
__version__ = "0.2.0"
