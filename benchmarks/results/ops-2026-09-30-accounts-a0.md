# Operations benchmark — 2026-09-30 (accounts-a0)

**Tenant isolation: 163/163 cross-tenant attempts refused.**
**Widget abuse: 5/5 checks passed.**

| Caller | Attempt | Status | Refused |
|---|---|---|---|
| tenant A key | `GET /api/v1/corpora/{B}` | 403 | yes |
| tenant A key | `PUT /api/v1/corpora/{B}` | 403 | yes |
| tenant A key | `POST /api/v1/corpora/{B}/rotate-key` | 403 | yes |
| tenant A key | `DELETE /api/v1/corpora/{B}` | 403 | yes |
| tenant A key | `POST /api/v1/query/retrieve` | 403 | yes |
| tenant A key | `POST /api/v1/query` | 403 | yes |
| tenant A key | `POST /api/v1/ingest/records` | 403 | yes |
| tenant A key | `GET /api/v1/ingest/jobs/{job}` | 403 | yes |
| tenant A key | `DELETE /api/v1/documents/{doc}?corpus_id={B}` | 403 | yes |
| tenant A key | `GET /api/v1/corpora/{B}/widget-keys` | 403 | yes |
| tenant A key | `POST /api/v1/corpora/{B}/widget-keys` | 403 | yes |
| tenant A key | `DELETE /api/v1/corpora/{B}/widget-keys/{widget}` | 403 | yes |
| tenant A key | `GET /api/v1/usage?corpus_id={B}` | 403 | yes |
| tenant A key | `GET /api/v1/admin/tenants/{tenantB}` | 403 | yes |
| tenant A key | `POST /api/v1/admin/tenants/{tenantB}/rotate-key` | 403 | yes |
| tenant A key | `GET /api/v1/documents?corpus_id={B}` | 403 | yes |
| tenant A key | `GET /api/v1/documents/{doc}?corpus_id={B}` | 403 | yes |
| tenant A key | `GET /api/v1/config?corpus_id={B}` | 403 | yes |
| tenant A key | `PATCH /api/v1/config?corpus_id={B}` | 403 | yes |
| tenant A key | `GET /api/v1/corpora/{B}/conversations` | 403 | yes |
| tenant A key | `POST /api/v1/corpora/{B}/conversations` | 403 | yes |
| tenant A key | `GET /api/v1/corpora/{B}/memories` | 403 | yes |
| tenant A key | `POST /api/v1/corpora/{B}/memories` | 403 | yes |
| tenant A key | `GET /api/v1/ingest/jobs?corpus_id={B}` | 403 | yes |
| tenant A key | `GET /api/v1/feedback?corpus_id={B}` | 403 | yes |
| tenant A key | `GET /api/v1/migrations?corpus_id={B}` | 403 | yes |
| tenant A key | `GET /api/v1/corpora/{B}/migrations` | 403 | yes |
| tenant A key | `GET /api/v1/migrations/{migration}` | 403 | yes |
| tenant A key | `POST /api/v1/migrations/{migration}/approve` | 403 | yes |
| tenant A key | `PATCH /api/v1/golden/{pair}` | 403 | yes |
| tenant A key | `GET /api/v1/corpora?tenant_id={tenantB}` | 403 | yes |
| tenant A key | `PUT /api/v1/corpora/{A}` | 403 | yes |
| tenant A key | `GET /api/v1/portal/workspaces/{workspaceB}` | 403 | yes |
| tenant A key | `PATCH /api/v1/portal/workspaces/{workspaceB}` | 403 | yes |
| tenant A key | `DELETE /api/v1/portal/workspaces/{workspaceB}` | 403 | yes |
| tenant A key | `POST /api/v1/portal/workspaces/{workspaceB}/lock` | 403 | yes |
| tenant A key | `GET /api/v1/portal/workspaces/{workspaceB}/content` | 403 | yes |
| tenant A key | `POST /api/v1/portal/workspaces/{workspaceB}/content` | 403 | yes |
| tenant A key | `GET /api/v1/portal/workspaces/{workspaceB}/chunks` | 403 | yes |
| tenant A key | `POST /api/v1/portal/workspaces/{workspaceB}/chunks` | 403 | yes |
| tenant A key | `POST /api/v1/portal/workspaces/{workspaceB}/submit` | 403 | yes |
| tenant A key | `POST /api/v1/portal/workspaces/{workspaceB}/approve` | 403 | yes |
| tenant A key | `POST /api/v1/portal/workspaces/{workspaceB}/ingest` | 403 | yes |
| tenant A key | `POST /api/v1/portal/workspaces/{workspaceB}/chat` | 403 | yes |
| tenant A key | `GET /api/v1/portal/workspaces/{workspaceB}/audit-trail` | 403 | yes |
| tenant A key | `GET /api/v1/portal/corpus/{B}/rules` | 403 | yes |
| tenant A key | `POST /api/v1/portal/corpus/{B}/rules` | 403 | yes |
| tenant A key | `POST /api/v1/portal/workspaces/from-ingestion` | 403 | yes |
| tenant A key | `GET /api/v1/query/{query}` | 404 | yes |
| tenant A key | `POST /api/v1/feedback` | 403 | yes |
| corpus A key | `GET /api/v1/corpora/{B}` | 403 | yes |
| corpus A key | `PUT /api/v1/corpora/{B}` | 403 | yes |
| corpus A key | `POST /api/v1/corpora/{B}/rotate-key` | 403 | yes |
| corpus A key | `DELETE /api/v1/corpora/{B}` | 403 | yes |
| corpus A key | `POST /api/v1/query/retrieve` | 403 | yes |
| corpus A key | `POST /api/v1/query` | 403 | yes |
| corpus A key | `POST /api/v1/ingest/records` | 403 | yes |
| corpus A key | `GET /api/v1/ingest/jobs/{job}` | 403 | yes |
| corpus A key | `DELETE /api/v1/documents/{doc}?corpus_id={B}` | 403 | yes |
| corpus A key | `GET /api/v1/corpora/{B}/widget-keys` | 403 | yes |
| corpus A key | `POST /api/v1/corpora/{B}/widget-keys` | 403 | yes |
| corpus A key | `DELETE /api/v1/corpora/{B}/widget-keys/{widget}` | 403 | yes |
| corpus A key | `GET /api/v1/usage?corpus_id={B}` | 403 | yes |
| corpus A key | `GET /api/v1/admin/tenants/{tenantB}` | 403 | yes |
| corpus A key | `POST /api/v1/admin/tenants/{tenantB}/rotate-key` | 403 | yes |
| corpus A key | `GET /api/v1/documents?corpus_id={B}` | 403 | yes |
| corpus A key | `GET /api/v1/documents/{doc}?corpus_id={B}` | 403 | yes |
| corpus A key | `GET /api/v1/config?corpus_id={B}` | 403 | yes |
| corpus A key | `PATCH /api/v1/config?corpus_id={B}` | 403 | yes |
| corpus A key | `GET /api/v1/corpora/{B}/conversations` | 403 | yes |
| corpus A key | `POST /api/v1/corpora/{B}/conversations` | 403 | yes |
| corpus A key | `GET /api/v1/corpora/{B}/memories` | 403 | yes |
| corpus A key | `POST /api/v1/corpora/{B}/memories` | 403 | yes |
| corpus A key | `GET /api/v1/ingest/jobs?corpus_id={B}` | 403 | yes |
| corpus A key | `GET /api/v1/feedback?corpus_id={B}` | 403 | yes |
| corpus A key | `GET /api/v1/migrations?corpus_id={B}` | 403 | yes |
| corpus A key | `GET /api/v1/corpora/{B}/migrations` | 403 | yes |
| corpus A key | `GET /api/v1/migrations/{migration}` | 403 | yes |
| corpus A key | `POST /api/v1/migrations/{migration}/approve` | 403 | yes |
| corpus A key | `PATCH /api/v1/golden/{pair}` | 403 | yes |
| corpus A key | `GET /api/v1/corpora?tenant_id={tenantB}` | 403 | yes |
| corpus A key | `PUT /api/v1/corpora/{A}` | 403 | yes |
| corpus A key | `GET /api/v1/portal/workspaces/{workspaceB}` | 403 | yes |
| corpus A key | `PATCH /api/v1/portal/workspaces/{workspaceB}` | 403 | yes |
| corpus A key | `DELETE /api/v1/portal/workspaces/{workspaceB}` | 403 | yes |
| corpus A key | `POST /api/v1/portal/workspaces/{workspaceB}/lock` | 403 | yes |
| corpus A key | `GET /api/v1/portal/workspaces/{workspaceB}/content` | 403 | yes |
| corpus A key | `POST /api/v1/portal/workspaces/{workspaceB}/content` | 403 | yes |
| corpus A key | `GET /api/v1/portal/workspaces/{workspaceB}/chunks` | 403 | yes |
| corpus A key | `POST /api/v1/portal/workspaces/{workspaceB}/chunks` | 403 | yes |
| corpus A key | `POST /api/v1/portal/workspaces/{workspaceB}/submit` | 403 | yes |
| corpus A key | `POST /api/v1/portal/workspaces/{workspaceB}/approve` | 403 | yes |
| corpus A key | `POST /api/v1/portal/workspaces/{workspaceB}/ingest` | 403 | yes |
| corpus A key | `POST /api/v1/portal/workspaces/{workspaceB}/chat` | 403 | yes |
| corpus A key | `GET /api/v1/portal/workspaces/{workspaceB}/audit-trail` | 403 | yes |
| corpus A key | `GET /api/v1/portal/corpus/{B}/rules` | 403 | yes |
| corpus A key | `POST /api/v1/portal/corpus/{B}/rules` | 403 | yes |
| corpus A key | `POST /api/v1/portal/workspaces/from-ingestion` | 403 | yes |
| corpus A key | `GET /api/v1/query/{query}` | 404 | yes |
| corpus A key | `POST /api/v1/feedback` | 403 | yes |
| no key | `GET /api/v1/corpora/{B}` | 401 | yes |
| no key | `PUT /api/v1/corpora/{B}` | 401 | yes |
| no key | `POST /api/v1/corpora/{B}/rotate-key` | 401 | yes |
| no key | `DELETE /api/v1/corpora/{B}` | 401 | yes |
| no key | `POST /api/v1/query/retrieve` | 401 | yes |
| no key | `POST /api/v1/query` | 401 | yes |
| no key | `POST /api/v1/ingest/records` | 401 | yes |
| no key | `GET /api/v1/ingest/jobs/{job}` | 401 | yes |
| no key | `DELETE /api/v1/documents/{doc}?corpus_id={B}` | 401 | yes |
| no key | `GET /api/v1/corpora/{B}/widget-keys` | 401 | yes |
| no key | `POST /api/v1/corpora/{B}/widget-keys` | 401 | yes |
| no key | `DELETE /api/v1/corpora/{B}/widget-keys/{widget}` | 401 | yes |
| no key | `GET /api/v1/usage?corpus_id={B}` | 401 | yes |
| no key | `GET /api/v1/admin/tenants/{tenantB}` | 401 | yes |
| no key | `POST /api/v1/admin/tenants/{tenantB}/rotate-key` | 401 | yes |
| no key | `GET /api/v1/documents?corpus_id={B}` | 401 | yes |
| no key | `GET /api/v1/documents/{doc}?corpus_id={B}` | 401 | yes |
| no key | `GET /api/v1/config?corpus_id={B}` | 401 | yes |
| no key | `PATCH /api/v1/config?corpus_id={B}` | 401 | yes |
| no key | `GET /api/v1/corpora/{B}/conversations` | 401 | yes |
| no key | `POST /api/v1/corpora/{B}/conversations` | 401 | yes |
| no key | `GET /api/v1/corpora/{B}/memories` | 401 | yes |
| no key | `POST /api/v1/corpora/{B}/memories` | 401 | yes |
| no key | `GET /api/v1/ingest/jobs?corpus_id={B}` | 401 | yes |
| no key | `GET /api/v1/feedback?corpus_id={B}` | 401 | yes |
| no key | `GET /api/v1/migrations?corpus_id={B}` | 401 | yes |
| no key | `GET /api/v1/corpora/{B}/migrations` | 401 | yes |
| no key | `GET /api/v1/migrations/{migration}` | 401 | yes |
| no key | `POST /api/v1/migrations/{migration}/approve` | 401 | yes |
| no key | `PATCH /api/v1/golden/{pair}` | 401 | yes |
| no key | `GET /api/v1/corpora?tenant_id={tenantB}` | 401 | yes |
| no key | `PUT /api/v1/corpora/{A}` | 401 | yes |
| no key | `GET /api/v1/portal/workspaces/{workspaceB}` | 401 | yes |
| no key | `PATCH /api/v1/portal/workspaces/{workspaceB}` | 401 | yes |
| no key | `DELETE /api/v1/portal/workspaces/{workspaceB}` | 401 | yes |
| no key | `POST /api/v1/portal/workspaces/{workspaceB}/lock` | 401 | yes |
| no key | `GET /api/v1/portal/workspaces/{workspaceB}/content` | 401 | yes |
| no key | `POST /api/v1/portal/workspaces/{workspaceB}/content` | 401 | yes |
| no key | `GET /api/v1/portal/workspaces/{workspaceB}/chunks` | 401 | yes |
| no key | `POST /api/v1/portal/workspaces/{workspaceB}/chunks` | 401 | yes |
| no key | `POST /api/v1/portal/workspaces/{workspaceB}/submit` | 401 | yes |
| no key | `POST /api/v1/portal/workspaces/{workspaceB}/approve` | 401 | yes |
| no key | `POST /api/v1/portal/workspaces/{workspaceB}/ingest` | 401 | yes |
| no key | `POST /api/v1/portal/workspaces/{workspaceB}/chat` | 401 | yes |
| no key | `GET /api/v1/portal/workspaces/{workspaceB}/audit-trail` | 401 | yes |
| no key | `GET /api/v1/portal/corpus/{B}/rules` | 401 | yes |
| no key | `POST /api/v1/portal/corpus/{B}/rules` | 401 | yes |
| no key | `POST /api/v1/portal/workspaces/from-ingestion` | 401 | yes |
| no key | `GET /api/v1/query/{query}` | 401 | yes |
| no key | `POST /api/v1/feedback` | 401 | yes |
| widget key as API key | `GET /api/v1/corpora/{B}` | 401 | yes |
| widget key as API key | `PUT /api/v1/corpora/{B}` | 401 | yes |
| widget key as API key | `POST /api/v1/corpora/{B}/rotate-key` | 401 | yes |
| widget key as API key | `DELETE /api/v1/corpora/{B}` | 401 | yes |
| widget key as API key | `POST /api/v1/query/retrieve` | 401 | yes |
| corpus A key | `GET /api/v1/documents?corpus_id={B} returns B's documents` | 403 | yes |
| tenant A key | `GET /api/v1/corpora lists {B}` | 200 | yes |
| corpus A key | `retrieve returns tenant B text` | 200 | yes |
| tenant A key | `GET /api/v1/portal/workspaces lists {workspaceB}` | 200 | yes |
| corpus A key | `GET /api/v1/portal/workspaces lists {workspaceB}` | 200 | yes |
| all of the above | `B's config and golden pair unchanged, A still owned by tenant A` | 200 | yes |
| all of the above | `B's workspace, staged content, chunks and rules unchanged` | 200 | yes |
| corpus B key, tenant B deactivated | `GET /api/v1/documents` | 401 | yes |

| Widget check | Status | OK |
|---|---|---|
| config from the allowed site | 200 | yes |
| config from another site | 403 | yes |
| config with no Origin | 403 | yes |
| secret corpus key used as widget key | 401 | yes |
| 8 messages at a 5/min limit | [200, 200, 200, 200, 200, 429, 429, 429] | yes |

## Footprint

| Container | Memory |
|---|---|
| app | 2.16GiB |
| worker | 1.647GiB |
| scraper | 6.93MiB |
| celery-beat | 10.48MiB |
| tei | 31.3MiB |
| redis | 11.48MiB |
| qdrant | 136.1MiB |
| dashboard | 3.453MiB |
| postgres | 487.2MiB |
| minio | 118.6MiB |
