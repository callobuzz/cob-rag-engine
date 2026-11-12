"""Content Portal route aggregation.

Combines all portal sub-routers into a single router with /api/v1/portal prefix.
"""

from fastapi import APIRouter

from callobuzz_rag.content_portal.routes.ai_chat import router as ai_chat_router
from callobuzz_rag.content_portal.routes.chunks import router as chunks_router
from callobuzz_rag.content_portal.routes.post_ingest import router as post_ingest_router
from callobuzz_rag.content_portal.routes.review import router as review_router
from callobuzz_rag.content_portal.routes.rules import router as rules_router
from callobuzz_rag.content_portal.routes.staging import router as staging_router
from callobuzz_rag.content_portal.routes.workspaces import router as workspaces_router

portal_router = APIRouter(prefix="/api/v1/portal", tags=["portal"])

portal_router.include_router(workspaces_router)
portal_router.include_router(staging_router)
portal_router.include_router(rules_router)
portal_router.include_router(chunks_router)
portal_router.include_router(review_router)
portal_router.include_router(ai_chat_router)
portal_router.include_router(post_ingest_router)
