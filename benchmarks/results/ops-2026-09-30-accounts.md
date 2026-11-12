# Operations benchmark — 2026-09-30 (accounts)

**Tenant isolation and account attacks: 412/412 attempts refused (checks held).**
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
| tenant A key | `POST /api/v1/documents/{doc}/versions/{versionB}/restore?corpus_id={B}` | 403 | yes |
| tenant A key | `DELETE /api/v1/golden/{pair}` | 403 | yes |
| tenant A key | `GET /api/v1/webhooks` | 403 | yes |
| tenant A key | `POST /api/v1/webhooks` | 403 | yes |
| tenant A key | `GET /api/v1/admin/audit?tenant_id={tenantB}` | 403 | yes |
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
| tenant A key | `POST /api/v1/portal/workspaces/{reviewB}/finalize-review` | 403 | yes |
| tenant A key | `GET /api/v1/portal/workspaces/{reviewB}/review-status` | 403 | yes |
| tenant A key | `POST /api/v1/portal/workspaces/{workspaceB}/chat/execute` | 403 | yes |
| tenant A key | `GET /api/v1/portal/workspaces/{workspaceA}/content/{contentB}` | 404 | yes |
| tenant A key | `POST /api/v1/portal/workspaces/{workspaceA}/content/{contentB}/reprocess` | 404 | yes |
| tenant A key | `PATCH /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}` | 404 | yes |
| tenant A key | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}/split` | 404 | yes |
| tenant A key | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/merge` | 404 | yes |
| tenant A key | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/reorder` | 404 | yes |
| tenant A key | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}/approve` | 404 | yes |
| tenant A key | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}/reject` | 404 | yes |
| tenant A key | `DELETE /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}?user=attacker` | 404 | yes |
| tenant A key | `POST /api/v1/portal/workspaces/{workspaceA}/chat/execute` | 404 | yes |
| tenant A key | `PATCH /api/v1/portal/corpus/{A}/rules/{ruleB}` | 404 | yes |
| tenant A key | `DELETE /api/v1/portal/corpus/{A}/rules/{ruleB}` | 404 | yes |
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
| corpus A key | `POST /api/v1/documents/{doc}/versions/{versionB}/restore?corpus_id={B}` | 403 | yes |
| corpus A key | `DELETE /api/v1/golden/{pair}` | 403 | yes |
| corpus A key | `GET /api/v1/webhooks` | 403 | yes |
| corpus A key | `POST /api/v1/webhooks` | 403 | yes |
| corpus A key | `GET /api/v1/admin/audit?tenant_id={tenantB}` | 403 | yes |
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
| corpus A key | `POST /api/v1/portal/workspaces/{reviewB}/finalize-review` | 403 | yes |
| corpus A key | `GET /api/v1/portal/workspaces/{reviewB}/review-status` | 403 | yes |
| corpus A key | `POST /api/v1/portal/workspaces/{workspaceB}/chat/execute` | 403 | yes |
| corpus A key | `GET /api/v1/portal/workspaces/{workspaceA}/content/{contentB}` | 404 | yes |
| corpus A key | `POST /api/v1/portal/workspaces/{workspaceA}/content/{contentB}/reprocess` | 404 | yes |
| corpus A key | `PATCH /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}` | 404 | yes |
| corpus A key | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}/split` | 404 | yes |
| corpus A key | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/merge` | 404 | yes |
| corpus A key | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/reorder` | 404 | yes |
| corpus A key | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}/approve` | 404 | yes |
| corpus A key | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}/reject` | 404 | yes |
| corpus A key | `DELETE /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}?user=attacker` | 404 | yes |
| corpus A key | `POST /api/v1/portal/workspaces/{workspaceA}/chat/execute` | 404 | yes |
| corpus A key | `PATCH /api/v1/portal/corpus/{A}/rules/{ruleB}` | 404 | yes |
| corpus A key | `DELETE /api/v1/portal/corpus/{A}/rules/{ruleB}` | 404 | yes |
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
| no key | `POST /api/v1/documents/{doc}/versions/{versionB}/restore?corpus_id={B}` | 401 | yes |
| no key | `DELETE /api/v1/golden/{pair}` | 401 | yes |
| no key | `GET /api/v1/webhooks` | 401 | yes |
| no key | `POST /api/v1/webhooks` | 401 | yes |
| no key | `GET /api/v1/admin/audit?tenant_id={tenantB}` | 401 | yes |
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
| no key | `POST /api/v1/portal/workspaces/{reviewB}/finalize-review` | 401 | yes |
| no key | `GET /api/v1/portal/workspaces/{reviewB}/review-status` | 401 | yes |
| no key | `POST /api/v1/portal/workspaces/{workspaceB}/chat/execute` | 401 | yes |
| no key | `GET /api/v1/portal/workspaces/{workspaceA}/content/{contentB}` | 401 | yes |
| no key | `POST /api/v1/portal/workspaces/{workspaceA}/content/{contentB}/reprocess` | 401 | yes |
| no key | `PATCH /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}` | 401 | yes |
| no key | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}/split` | 401 | yes |
| no key | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/merge` | 401 | yes |
| no key | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/reorder` | 401 | yes |
| no key | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}/approve` | 401 | yes |
| no key | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}/reject` | 401 | yes |
| no key | `DELETE /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}?user=attacker` | 401 | yes |
| no key | `POST /api/v1/portal/workspaces/{workspaceA}/chat/execute` | 401 | yes |
| no key | `PATCH /api/v1/portal/corpus/{A}/rules/{ruleB}` | 401 | yes |
| no key | `DELETE /api/v1/portal/corpus/{A}/rules/{ruleB}` | 401 | yes |
| no key | `GET /api/v1/query/{query}` | 401 | yes |
| no key | `POST /api/v1/feedback` | 401 | yes |
| owner A session | `GET /api/v1/corpora/{B}` | 403 | yes |
| owner A session | `PUT /api/v1/corpora/{B}` | 403 | yes |
| owner A session | `POST /api/v1/corpora/{B}/rotate-key` | 403 | yes |
| owner A session | `DELETE /api/v1/corpora/{B}` | 403 | yes |
| owner A session | `POST /api/v1/query/retrieve` | 403 | yes |
| owner A session | `POST /api/v1/query` | 403 | yes |
| owner A session | `POST /api/v1/ingest/records` | 403 | yes |
| owner A session | `GET /api/v1/ingest/jobs/{job}` | 403 | yes |
| owner A session | `DELETE /api/v1/documents/{doc}?corpus_id={B}` | 403 | yes |
| owner A session | `GET /api/v1/corpora/{B}/widget-keys` | 403 | yes |
| owner A session | `POST /api/v1/corpora/{B}/widget-keys` | 403 | yes |
| owner A session | `DELETE /api/v1/corpora/{B}/widget-keys/{widget}` | 403 | yes |
| owner A session | `GET /api/v1/usage?corpus_id={B}` | 403 | yes |
| owner A session | `GET /api/v1/admin/tenants/{tenantB}` | 403 | yes |
| owner A session | `POST /api/v1/admin/tenants/{tenantB}/rotate-key` | 403 | yes |
| owner A session | `GET /api/v1/documents?corpus_id={B}` | 403 | yes |
| owner A session | `GET /api/v1/documents/{doc}?corpus_id={B}` | 403 | yes |
| owner A session | `GET /api/v1/config?corpus_id={B}` | 403 | yes |
| owner A session | `PATCH /api/v1/config?corpus_id={B}` | 403 | yes |
| owner A session | `GET /api/v1/corpora/{B}/conversations` | 403 | yes |
| owner A session | `POST /api/v1/corpora/{B}/conversations` | 403 | yes |
| owner A session | `GET /api/v1/corpora/{B}/memories` | 403 | yes |
| owner A session | `POST /api/v1/corpora/{B}/memories` | 403 | yes |
| owner A session | `GET /api/v1/ingest/jobs?corpus_id={B}` | 403 | yes |
| owner A session | `GET /api/v1/feedback?corpus_id={B}` | 403 | yes |
| owner A session | `GET /api/v1/migrations?corpus_id={B}` | 403 | yes |
| owner A session | `GET /api/v1/corpora/{B}/migrations` | 403 | yes |
| owner A session | `GET /api/v1/migrations/{migration}` | 403 | yes |
| owner A session | `POST /api/v1/migrations/{migration}/approve` | 403 | yes |
| owner A session | `PATCH /api/v1/golden/{pair}` | 403 | yes |
| owner A session | `GET /api/v1/corpora?tenant_id={tenantB}` | 403 | yes |
| owner A session | `PUT /api/v1/corpora/{A}` | 403 | yes |
| owner A session | `POST /api/v1/documents/{doc}/versions/{versionB}/restore?corpus_id={B}` | 403 | yes |
| owner A session | `DELETE /api/v1/golden/{pair}` | 403 | yes |
| owner A session | `GET /api/v1/webhooks` | 403 | yes |
| owner A session | `POST /api/v1/webhooks` | 403 | yes |
| owner A session | `GET /api/v1/admin/audit?tenant_id={tenantB}` | 403 | yes |
| owner A session | `GET /api/v1/portal/workspaces/{workspaceB}` | 403 | yes |
| owner A session | `PATCH /api/v1/portal/workspaces/{workspaceB}` | 403 | yes |
| owner A session | `DELETE /api/v1/portal/workspaces/{workspaceB}` | 403 | yes |
| owner A session | `POST /api/v1/portal/workspaces/{workspaceB}/lock` | 403 | yes |
| owner A session | `GET /api/v1/portal/workspaces/{workspaceB}/content` | 403 | yes |
| owner A session | `POST /api/v1/portal/workspaces/{workspaceB}/content` | 403 | yes |
| owner A session | `GET /api/v1/portal/workspaces/{workspaceB}/chunks` | 403 | yes |
| owner A session | `POST /api/v1/portal/workspaces/{workspaceB}/chunks` | 403 | yes |
| owner A session | `POST /api/v1/portal/workspaces/{workspaceB}/submit` | 403 | yes |
| owner A session | `POST /api/v1/portal/workspaces/{workspaceB}/approve` | 403 | yes |
| owner A session | `POST /api/v1/portal/workspaces/{workspaceB}/ingest` | 403 | yes |
| owner A session | `POST /api/v1/portal/workspaces/{workspaceB}/chat` | 403 | yes |
| owner A session | `GET /api/v1/portal/workspaces/{workspaceB}/audit-trail` | 403 | yes |
| owner A session | `GET /api/v1/portal/corpus/{B}/rules` | 403 | yes |
| owner A session | `POST /api/v1/portal/corpus/{B}/rules` | 403 | yes |
| owner A session | `POST /api/v1/portal/workspaces/from-ingestion` | 403 | yes |
| owner A session | `POST /api/v1/portal/workspaces/{reviewB}/finalize-review` | 403 | yes |
| owner A session | `GET /api/v1/portal/workspaces/{reviewB}/review-status` | 403 | yes |
| owner A session | `POST /api/v1/portal/workspaces/{workspaceB}/chat/execute` | 403 | yes |
| owner A session | `GET /api/v1/portal/workspaces/{workspaceA}/content/{contentB}` | 404 | yes |
| owner A session | `POST /api/v1/portal/workspaces/{workspaceA}/content/{contentB}/reprocess` | 404 | yes |
| owner A session | `PATCH /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}` | 404 | yes |
| owner A session | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}/split` | 404 | yes |
| owner A session | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/merge` | 404 | yes |
| owner A session | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/reorder` | 404 | yes |
| owner A session | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}/approve` | 404 | yes |
| owner A session | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}/reject` | 404 | yes |
| owner A session | `DELETE /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}?user=attacker` | 404 | yes |
| owner A session | `POST /api/v1/portal/workspaces/{workspaceA}/chat/execute` | 404 | yes |
| owner A session | `PATCH /api/v1/portal/corpus/{A}/rules/{ruleB}` | 404 | yes |
| owner A session | `DELETE /api/v1/portal/corpus/{A}/rules/{ruleB}` | 404 | yes |
| owner A session | `GET /api/v1/query/{query}` | 404 | yes |
| owner A session | `POST /api/v1/feedback` | 403 | yes |
| member A session | `GET /api/v1/corpora/{B}` | 403 | yes |
| member A session | `PUT /api/v1/corpora/{B}` | 403 | yes |
| member A session | `POST /api/v1/corpora/{B}/rotate-key` | 403 | yes |
| member A session | `DELETE /api/v1/corpora/{B}` | 403 | yes |
| member A session | `POST /api/v1/query/retrieve` | 403 | yes |
| member A session | `POST /api/v1/query` | 403 | yes |
| member A session | `POST /api/v1/ingest/records` | 403 | yes |
| member A session | `GET /api/v1/ingest/jobs/{job}` | 403 | yes |
| member A session | `DELETE /api/v1/documents/{doc}?corpus_id={B}` | 403 | yes |
| member A session | `GET /api/v1/corpora/{B}/widget-keys` | 403 | yes |
| member A session | `POST /api/v1/corpora/{B}/widget-keys` | 403 | yes |
| member A session | `DELETE /api/v1/corpora/{B}/widget-keys/{widget}` | 403 | yes |
| member A session | `GET /api/v1/usage?corpus_id={B}` | 403 | yes |
| member A session | `GET /api/v1/admin/tenants/{tenantB}` | 403 | yes |
| member A session | `POST /api/v1/admin/tenants/{tenantB}/rotate-key` | 403 | yes |
| member A session | `GET /api/v1/documents?corpus_id={B}` | 403 | yes |
| member A session | `GET /api/v1/documents/{doc}?corpus_id={B}` | 403 | yes |
| member A session | `GET /api/v1/config?corpus_id={B}` | 403 | yes |
| member A session | `PATCH /api/v1/config?corpus_id={B}` | 403 | yes |
| member A session | `GET /api/v1/corpora/{B}/conversations` | 403 | yes |
| member A session | `POST /api/v1/corpora/{B}/conversations` | 403 | yes |
| member A session | `GET /api/v1/corpora/{B}/memories` | 403 | yes |
| member A session | `POST /api/v1/corpora/{B}/memories` | 403 | yes |
| member A session | `GET /api/v1/ingest/jobs?corpus_id={B}` | 403 | yes |
| member A session | `GET /api/v1/feedback?corpus_id={B}` | 403 | yes |
| member A session | `GET /api/v1/migrations?corpus_id={B}` | 403 | yes |
| member A session | `GET /api/v1/corpora/{B}/migrations` | 403 | yes |
| member A session | `GET /api/v1/migrations/{migration}` | 403 | yes |
| member A session | `POST /api/v1/migrations/{migration}/approve` | 403 | yes |
| member A session | `PATCH /api/v1/golden/{pair}` | 403 | yes |
| member A session | `GET /api/v1/corpora?tenant_id={tenantB}` | 403 | yes |
| member A session | `PUT /api/v1/corpora/{A}` | 403 | yes |
| member A session | `POST /api/v1/documents/{doc}/versions/{versionB}/restore?corpus_id={B}` | 403 | yes |
| member A session | `DELETE /api/v1/golden/{pair}` | 403 | yes |
| member A session | `GET /api/v1/webhooks` | 403 | yes |
| member A session | `POST /api/v1/webhooks` | 403 | yes |
| member A session | `GET /api/v1/admin/audit?tenant_id={tenantB}` | 403 | yes |
| member A session | `GET /api/v1/portal/workspaces/{workspaceB}` | 403 | yes |
| member A session | `PATCH /api/v1/portal/workspaces/{workspaceB}` | 403 | yes |
| member A session | `DELETE /api/v1/portal/workspaces/{workspaceB}` | 403 | yes |
| member A session | `POST /api/v1/portal/workspaces/{workspaceB}/lock` | 403 | yes |
| member A session | `GET /api/v1/portal/workspaces/{workspaceB}/content` | 403 | yes |
| member A session | `POST /api/v1/portal/workspaces/{workspaceB}/content` | 403 | yes |
| member A session | `GET /api/v1/portal/workspaces/{workspaceB}/chunks` | 403 | yes |
| member A session | `POST /api/v1/portal/workspaces/{workspaceB}/chunks` | 403 | yes |
| member A session | `POST /api/v1/portal/workspaces/{workspaceB}/submit` | 403 | yes |
| member A session | `POST /api/v1/portal/workspaces/{workspaceB}/approve` | 403 | yes |
| member A session | `POST /api/v1/portal/workspaces/{workspaceB}/ingest` | 403 | yes |
| member A session | `POST /api/v1/portal/workspaces/{workspaceB}/chat` | 403 | yes |
| member A session | `GET /api/v1/portal/workspaces/{workspaceB}/audit-trail` | 403 | yes |
| member A session | `GET /api/v1/portal/corpus/{B}/rules` | 403 | yes |
| member A session | `POST /api/v1/portal/corpus/{B}/rules` | 403 | yes |
| member A session | `POST /api/v1/portal/workspaces/from-ingestion` | 403 | yes |
| member A session | `POST /api/v1/portal/workspaces/{reviewB}/finalize-review` | 403 | yes |
| member A session | `GET /api/v1/portal/workspaces/{reviewB}/review-status` | 403 | yes |
| member A session | `POST /api/v1/portal/workspaces/{workspaceB}/chat/execute` | 403 | yes |
| member A session | `GET /api/v1/portal/workspaces/{workspaceA}/content/{contentB}` | 404 | yes |
| member A session | `POST /api/v1/portal/workspaces/{workspaceA}/content/{contentB}/reprocess` | 404 | yes |
| member A session | `PATCH /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}` | 404 | yes |
| member A session | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}/split` | 404 | yes |
| member A session | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/merge` | 404 | yes |
| member A session | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/reorder` | 404 | yes |
| member A session | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}/approve` | 404 | yes |
| member A session | `POST /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}/reject` | 404 | yes |
| member A session | `DELETE /api/v1/portal/workspaces/{workspaceA}/chunks/{chunkB}?user=attacker` | 404 | yes |
| member A session | `POST /api/v1/portal/workspaces/{workspaceA}/chat/execute` | 404 | yes |
| member A session | `PATCH /api/v1/portal/corpus/{A}/rules/{ruleB}` | 404 | yes |
| member A session | `DELETE /api/v1/portal/corpus/{A}/rules/{ruleB}` | 404 | yes |
| member A session | `GET /api/v1/query/{query}` | 404 | yes |
| member A session | `POST /api/v1/feedback` | 403 | yes |
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
| owner A session | `GET /api/v1/portal/workspaces lists {workspaceB}` | 200 | yes |
| member A session | `GET /api/v1/portal/workspaces lists {workspaceB}` | 200 | yes |
| owner A session | `portal on A's own knowledge base: create a workspace is allowed` | 201 | yes |
| owner A session | `portal on A's own workspace: list is allowed and shows it` | 200 | yes |
| owner A session | `portal on A's own workspace: read chunks is allowed` | 200 | yes |
| member A session | `portal on A's own workspace: list is allowed and shows it` | 200 | yes |
| member A session | `portal on A's own workspace: read chunks is allowed` | 200 | yes |
| member A session | `portal on A's own workspace: add a chunk is allowed` | 201 | yes |
| member A session | `portal on A's own workspace: DELETE the workspace (owner-only)` | 403 | yes |
| member A session, A's own | `POST /api/v1/corpora (owner-only)` | 403 | yes |
| member A session, A's own | `DELETE /api/v1/corpora/{A} (owner-only)` | 403 | yes |
| member A session, A's own | `POST /api/v1/corpora/{A}/rotate-key (owner-only)` | 403 | yes |
| member A session, A's own | `PUT /api/v1/corpora/{A} (owner-only)` | 403 | yes |
| member A session, A's own | `DELETE /api/v1/documents/{docA}?corpus_id={A} (owner-only)` | 403 | yes |
| member A session, A's own | `POST /api/v1/documents/{docA}/versions/{versionA}/restore?corpus_id={A} (owner-only)` | 403 | yes |
| member A session, A's own | `DELETE /api/v1/golden/{pairA} (owner-only)` | 403 | yes |
| member A session, A's own | `POST /api/v1/corpora/{A}/widget-keys (owner-only)` | 403 | yes |
| member A session, A's own | `POST /api/v1/corpora/{A}/widget-keys/{widgetA}/rotate (owner-only)` | 403 | yes |
| member A session, A's own | `DELETE /api/v1/corpora/{A}/widget-keys/{widgetA} (owner-only)` | 403 | yes |
| member A session, A's own | `POST /api/v1/tenants/me/rotate-key (owner-only)` | 403 | yes |
| member A session, A's own | `POST /api/v1/tenants/me/members (owner-only)` | 403 | yes |
| member A session, A's own | `DELETE /api/v1/corpora/{A}/memories?user_id=u (owner-only)` | 403 | yes |
| member A session, A's own | `DELETE /api/v1/corpora/{A}/memories/purge (owner-only)` | 403 | yes |
| owner A session | `GET /api/v1/admin/tenants/{tenantB}/members` | 403 | yes |
| owner A session | `POST /api/v1/admin/tenants/{tenantB}/invitations` | 403 | yes |
| forged token | `GET /api/v1/corpora with owner A's token re-signed with a wrong key` | 401 | yes |
| forged token | `GET /api/v1/corpora with owner A's token with tid changed to B (original signature)` | 401 | yes |
| forged token | `GET /api/v1/corpora with owner A's token with tid changed to B, re-signed with a wrong key` | 401 | yes |
| forged token | `GET /api/v1/corpora with owner A's token with alg: none` | 401 | yes |
| forged token | `GET /api/v1/corpora with owner A's token expired, signed with a wrong key` | 401 | yes |
| refresh reuse | `refresh owner A once, then present the OLD refresh token` | 401 | yes |
| refresh reuse | `then the NEW refresh token (the reuse revoked the session)` | 401 | yes |
| owner B session | `accept an invitation into A addressed to someone else` | 403 | yes |
| no key | `the same invitation token again after a proper accept` | 404 | yes |
| no key | `POST /api/v1/auth/signup: existing vs fresh address, same status and body` | [202, 202] | yes |
| no key | `POST /api/v1/auth/forgot: existing vs fresh address, same status and body` | [202, 202] | yes |
| no key | `6 wrong-password logins on one address: the 6th is 429 account_locked` | [401, 401, 401, 401, 401, 429] | yes |
| all of the above | `B's config and golden pair unchanged, A still owned by tenant A` | 200 | yes |
| all of the above | `A's document, golden pair and widget key still there, A's key still works` | 200 | yes |
| all of the above | `B's workspace, staged content, chunks and rules unchanged` | 200 | yes |
| all of the above | `B's review still in review, B's document version still the active one` | 200 | yes |
| removed member A (admin removed them) | `GET /api/v1/corpora with the access token issued before` | 401 session_revoked | yes |
| admin key | `suspend without confirm_name: 422` | 422 | yes |
| admin key | `suspend with the name in the wrong case: 422` | 422 | yes |
| admin key | `PUT {"is_active": false} without confirm_name: 422` | 422 | yes |
| admin key | `B still active after the three attempts` | active | yes |
| owner B session, before the suspension | `GET /api/v1/corpora is allowed (the token below is a live one)` | 200 | yes |
| admin key | `POST /api/v1/admin/tenants/{tenantB}/suspend with B's exact name suspends it` | 200 | yes |
| corpus B key, tenant B suspended | `GET /api/v1/documents` | 403 | yes |
| tenant B key, tenant B suspended | `GET /api/v1/tenants/me` | 403 | yes |
| widget key B from its own site, tenant B suspended | `POST /api/v1/widget/chat` | 403 | yes |
| owner B session issued before the suspension | `GET /api/v1/corpora` | 401 | yes |

| Widget check | Status | OK |
|---|---|---|
| config from the allowed site | 200 | yes |
| config from another site | 403 | yes |
| config with no Origin | 403 | yes |
| secret corpus key used as widget key | 401 | yes |
| 8 messages at a 5/min limit | [200, 200, 200, 200, 200, 429, 429, 429] | yes |

## Auth overhead — GET /api/v1/whoami, one at a time

Corpus key (cached): p50 12.5 ms, p95 43.7 ms. Bearer token: p50 9.6 ms, p95 26.3 ms (200 requests each).

## Footprint

| Container | Memory |
|---|---|
| worker | 1.102GiB |
| mailer | 52.69MiB |
| app | 642.1MiB |
| celery-beat | 24.69MiB |
| scraper | 6.594MiB |
| tei | 26.71MiB |
| redis | 11.2MiB |
| qdrant | 120.2MiB |
| dashboard | 3.832MiB |
| postgres | 429.3MiB |
| minio | 98.01MiB |
