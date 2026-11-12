"""Exercise QdrantVectorProvider against the live, authenticated Qdrant.

Not a unit test: the point is that the REAL provider class, built from real
settings, can create and drop a collection on the running engine now that
Qdrant demands an `api-key` header. A mock cannot show that.

Run from the repo root:  .venv/Scripts/python.exe scripts/check_qdrant_auth.py
"""

import asyncio
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ENV = Path(__file__).resolve().parents[1] / ".env"
overrides = {}
for line in ENV.read_text(encoding="utf-8").splitlines():
    if line.startswith(("RAG_QDRANT_API_KEY=", "RAG_QDRANT_PORT=")):
        k, _, v = line.partition("=")
        overrides[k] = v.strip()

from callobuzz_rag.core.config import Settings  # noqa: E402
from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider  # noqa: E402

settings = Settings(
    qdrant_host="127.0.0.1",
    qdrant_port=int(overrides.get("RAG_QDRANT_PORT", 6333)),
    qdrant_api_key=overrides["RAG_QDRANT_API_KEY"],
)


async def main() -> int:
    provider = QdrantVectorProvider(settings)
    name = "_anchor_probe"
    try:
        await provider.create_collection(name, 8)
        found = {c.name for c in (await provider._client.get_collections()).collections}
        assert name in found, f"{name} not created"
        await provider._client.delete_collection(name)
        print(
            f"QdrantVectorProvider create/list/delete OK against authenticated Qdrant "
            f"({len(found)} collections visible)"
        )
        return 0
    finally:
        await provider._client.close()


sys.exit(asyncio.run(main()))
