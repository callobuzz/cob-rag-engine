# CLAUDE.md

Guidance for AI coding assistants (and humans) working in this repository. Contributor
basics are in [CONTRIBUTING.md](CONTRIBUTING.md); this file is the fuller convention list.

## What This Is

cob-rag-engine — a self-hosted, **multi-tenant** RAG engine with chatbot-as-a-service
(an embeddable website widget) built in. Open source under AGPL-3.0 (commercial licence on request). No
LangChain / LlamaIndex — a custom core with a provider pattern for every external dependency.

**Multi-tenant model:** a corpus is the isolation unit — its own API key (`ck_xxx`), Qdrant
collection, and LLM / prompt / retrieval config. Tenants (`tk_xxx` keys, `db/models/tenant.py`,
`api/routes/tenants.py`) sit above corpora so one client application manages its own set. The
admin key (`RAG_ADMIN_API_KEY`) creates tenants and corpora. Every corpus belongs to a tenant
(`tenant_id` NOT NULL, FK RESTRICT); `tenant_id` on admin create is required (422
`tenant_required` without it), and a tenant key creates corpora for itself. All three authenticate through the `X-API-Key` header. Widget keys (`pk_xxx`,
`db/models/widget_key.py`) are the fourth kind: **publishable**, per corpus, sent as
`X-Widget-Key` by `widget.js` — chat-only, origin-locked, rate-limited (`widget/security.py`).
Dashboard sessions are the fifth: people sign in (`/api/v1/auth/*`) and send
`Authorization: Bearer <access token>` — a JWT plus a rotating refresh token, `accounts/`
(`tokens.py`, `sessions.py`, `service.py`); a super-admin sign-in (`RAG_SUPERADMIN_EMAIL`) is the
admin principal, a workspace sign-in its tenant's (`tenant_members`). Customers sign up
themselves (`accounts/signup.py`: `/auth/signup|verify|resend|forgot`; the sign-up mode in
`engine_settings` decides `active` vs `pending`), and a tenant has a `status`
(`pending`/`active`/`suspended`) changed only by `accounts/lifecycle.py`. So five credential
kinds: admin key (or super-admin session), `tk_`, `ck_`, `pk_` and workspace sessions (owner or
member) — "accounts are for people; keys are for machines".

Master design: `docs/internal/architecture/rag-kernel-design.md` (maintainers only — see below).

## Commands

```bash
# Full stack in Docker
python scripts/generate_secrets.py   # .env from .env.example + generated secrets
docker compose up -d --build         # or: make up / make up-gpu

# Native development
make venv                   # .venv (Python 3.12) with dev extras
make dev-infra              # datastores + TEI only, in Docker
make dev-app                # migrations + FastAPI on 127.0.0.1:8000
make dev-worker             # Celery worker (queues ingestion, periodic and email)
make dev-beat               # Celery beat

# Testing
make test-unit              # Unit tests only — fast, no Docker
make test-integration       # Integration tests — needs Docker
pytest tests/unit/test_chunking.py -v          # Single test file
pytest tests/unit/test_chunking.py::test_name  # Single test function

# Benchmark (needs a running stack)
python benchmarks/run.py --label mine

# Client SDKs
(cd clients/python && pytest)   ;   (cd clients/js && node --test)

# Accounts
make bootstrap              # super admin from RAG_SUPERADMIN_EMAIL (ARGS=--resend|--print-link)

# Quality
make lint                   # ruff check + mypy (strict mode)
make format                 # ruff format + auto-fix

# Database
make migrate                # alembic upgrade head
make migration msg="desc"   # Generate new migration
```

Default `pytest` excludes `integration` and `slow` markers — anything that needs a running stack
must carry `pytestmark = pytest.mark.integration`, or a plain `pytest` fails without one. `tests/conftest.py` disables the
`.env` file for every `Settings()` so tests never depend on a developer's local configuration.

## Architecture

**Provider pattern everywhere.** Every external dependency has an ABC in `base.py` and a
swappable implementation:

| Module | ABC | Implementations |
|---|---|---|
| `parsing/` | `DocumentParserProvider` | `DoclingParserProvider` |
| `embeddings/` | `EmbeddingProvider` | chosen by `RAG_EMBEDDING_PROVIDER` via `embeddings/factory.py`: `BgeM3EmbeddingProvider` (`local`, the `Settings` default), `TEIEmbeddingProvider` (`tei`, what `.env.example` sets — the `tei` compose service on :8081), `LiteLLMEmbeddingProvider` (`litellm` — OpenAI, Cohere, Voyage, Mistral, Gemini, Ollama…), `JinaEmbeddingProvider` (`jina`) |
| `vectordb/` | `VectorStorageProvider` | `QdrantVectorProvider` |
| `storage/` | `ObjectStorageProvider` | chosen by `RAG_STORAGE_PROVIDER` via `storage/factory.py` (`object_storage(settings)`, the only constructor call sites use): `LocalFileStorageProvider` (`local`, the default: a folder on this server, confined to its bucket folder) / `S3ObjectStorageProvider` (`s3`, any S3-compatible service). `python -m callobuzz_rag.storage.copy` moves a bucket between them |
| `enrichment/` | `EnrichmentProvider` | `ExtractiveEnrichmentProvider` |
| `llm/` | LLM generation | LiteLLM (any provider) |
| `accounts/email/` | `EmailProvider` | `SMTPEmailProvider`, `LogEmailProvider` (dev; refused in prod), `DisabledEmailProvider` — chosen by `RAG_EMAIL_PROVIDER` via `accounts/email/factory.py` |

**Ingestion pipeline** (`pipeline/orchestrator.py`): fingerprint → create DB records → store raw
file → parse → chunk → enrich → embed → store vectors → complete. Providers are injected via
the constructor.

**Async everywhere.** SQLAlchemy async, httpx async, `asyncio.to_thread()` for blocking libs
(boto3, Docling). Celery tasks use `asyncio.run()` to bridge sync→async.

**API flow:** `POST /api/v1/ingest` → validate + upload to object storage → create `IngestJob` →
dispatch Celery task → 202. The worker pulls the file and runs the full pipeline.

`POST /api/v1/ingest/records` imports already-text records (`pipeline/records.py`,
`IngestionOrchestrator.ingest_records`): one Document per record keyed by `external_id`,
unchanged records skipped by fingerprint.

**Evaluation** (`evaluation/`): golden sets (`golden_sets`, `golden_pair`) hold expected answers
and/or expected assistant behaviour; `eval_run` has `kind` traffic|golden and every run writes
`eval_run_samples`. Scoring is `scoring.py` (RAGAS per sample, one judge provider and cost ledger
per run), `judge.py` (owner criteria, failure reasons), `stats.py` (bootstrap intervals),
`compare.py` (run comparison), `golden_runner.py` (fast and full runs, atomic claim); behaviour
checks are LLM-free (`conversation/testing/asserts.py`) and fresh answers come only through
`conversation/testing/director_port.py`. Review inbox: `conversation/review.py`.

**Chunker** (`chunking/chunker.py`): structure-aware, 5-step deterministic pipeline — heading
splits → table/code isolation → size normalization → overlap injection → parent-child linking.
Token counting via tiktoken `cl100k_base`.

**Index text and vectors.** Every place that builds vectors for a chunk uses
`chunking/index_text.py` (heading path + text) and, for payloads rebuilt from Postgres,
`pipeline/payload.py` (document metadata lives in `documents.doc_metadata`). Keyword vectors are
BM25 (`retrieval/sparse.py`: stopwords + Snowball, `text_to_sparse_vector` for chunks,
`query_to_sparse_vector` for queries). Hybrid fuses by weighted normalized scores
(`fuse_convex`, 0.75 dense) unless a corpus picks RRF. Changing any of this changes stored
vectors: `python -m callobuzz_rag.retrieval.reindex` (`--dense`, `--rebuild`).

**Benchmarks** (`benchmarks/`): `beir.py` (six BEIR datasets), `fusion_study.py`,
`documents.py` (real files), `ops.py` (isolation for every key kind and owner/member sessions,
the account attacks, widget abuse, sign-in overhead, latency, memory; the session rows need
`RAG_SESSION_SECRET` on the engine), `run.py`. All
LLM-free, through the public API; results and method in `benchmarks/README.md`. Re-run the
relevant suite when changing retrieval, parsing or auth, and keep README, `docs/index.html` and
`docs/benchmarks.html` in sync with the numbers.

**Website widget** (`widget/`, `api/routes/widget.py`, `api/routes/widget_keys.py`): `GET /widget.js`
(Shadow-DOM script, `widget/static/widget.js`), `GET /api/v1/widget/config`, `POST
/api/v1/widget/chat` (SSE: `sources`, `token`, `done`, `error`). These paths bypass the X-API-Key
middleware (`WIDGET_PREFIXES`, part of `PUBLIC_PREFIXES` in `api/auth.py`; `/api/v1/public/` is
the other part, `OPEN_PREFIXES`, which takes no credential at all) and authenticate the `pk_` key, check `Origin`
against the key's `allowed_origins`, then apply Redis rate limits (fail open) before any work.
`WidgetCORSMiddleware` is added last (outermost) so it can answer preflights for customer sites.
Both it and `APIKeyAuthMiddleware` are pure ASGI, never `BaseHTTPMiddleware` (whose cancel scope
cut off a streamed response's cleanup); streams do their database writes before the final event,
through `api.streaming.shielded`.
Chat reuses `_build_orchestrator` from `api/routes/query.py`; a browser can never send X-LLM-*.
The panel footer shows "cob-rag-engine by Callobuzz" as plain text by default, never a link; the operator can hide it in Branding
(`POWERED_TEXT` in `widget.js`, kept equal to `POWERED_BY` by `tests/unit/test_widget_powered_by.py`).

**Conversation layer** (`conversation/`, epics C1-C9 in `docs/internal/plans/epics/conversation/`): every answering surface (`/query`, `/query/stream`, widget chat, conversation messages) asks `surfaces.assistant_for()`; with a published, enabled assistant the `Director` (`director.py`) masks PII (`pii.py`), labels the turn (`rules.py`, then the cached fast model in `labeler.py`), routes it (`routing.py`), gates a grounded answer on dense cosine (`gates.py`, per-model thresholds in `data/gate_defaults.json`) and on a per-sentence fact check (`claims.SentenceGate`), and writes `assistant_turns`. It yields typed events (`envelope.py`) that `to_sse` turns into each surface's stream. ONE `LLMProvider` carries a whole turn (label, translation, retrieval, answer), so its cost ledger is what is metered; a turn with no model call is free. `done.timings` is public stage milliseconds only (`gate_threshold` is its own field). `assistant.enabled=false` leaves every old path untouched. Route unit tests run with the assistant off (autouse `_assistant_off` in `tests/conftest.py`) unless marked `assistant_on`. `assistant.use_draft`/`debug`/`simulate` on a key need `write` permission. Benchmarks: `benchmarks/labels.py`, `gate.py`, `ops.py --assistant-overhead`.

**Accounts** (`accounts/`, epics A0–A8 in `docs/internal/plans/epics/accounts/`): `passwords.py`
(argon2id behind a 4-slot semaphore), `tokens.py` (HS256 access JWT, `RAG_SESSION_SECRET` list),
`sessions.py` (rotating refresh tokens, reuse revokes), `codes.py` (HMAC'd 6-digit codes),
`ratelimit.py` (Redis, fail closed), `service.py` (login/refresh/reset), `bootstrap.py` (super
admin from `RAG_SUPERADMIN_EMAIL`, run by the entrypoint; prints the link to the console by
design), `signup.py`, `lifecycle.py` (tenant status), `cleanup.py` (daily beat task),
`members.py` (teams, invitations), `plans.py` + `caps.py` (four limits), `settings_store.py`,
`audit.py`, `email/`. Tables `users`, `tenant_members`, `sessions`, `email_codes`,
`invitations`, `plans`, `tenant_question_counts`, `engine_settings`, `audit_log` — tenant- or
engine-level, none with corpus affinity. Routes `api/routes/auth.py`, `members.py`, `plans.py`,
`engine_settings.py`, `email_admin.py`, `audit.py` (`GET /api/v1/admin/audit`, operator only,
filters by tenant, action or `prefix.`, actor, 366-day range max). Every audited action is in
`AUDIT_ACTIONS` and has a producer in `tests/unit/test_audit_coverage.py`.

**Engine settings** (`accounts/settings_store.py`, table `engine_settings`, one row): sign-up mode
and branding, edited at runtime by the admin (`GET/PATCH /api/v1/admin/settings`), cached 10 s per
process, served without a key by `GET /api/v1/public/branding` (with the constant `POWERED_BY`,
never a setting). **Audit log** (`accounts/audit.py`, `record_audit`, table `audit_log`): no FKs
by design; the row joins the caller's transaction; actions must be in `AUDIT_ACTIONS`.

**E-mail** (`accounts/email/`, `worker/email_tasks.py`, `api/routes/email_admin.py`): flows call
`enqueue_email(template, to, context, expires_s=...)`, which validates the template and context in
the request, then queues `callobuzz_rag.send_email` on the `email` queue, served by the `mailer`
compose service (threads pool). The worker renders at send time (Jinja2, autoescaped,
`StrictUndefined`) with the current branding; the "Powered by" footer is in `_base.html` /
`_base.txt` from `POWERED_BY`, never context. Transient SMTP errors retry 15→240 s, permanent ones
log the SMTP code only. `send_now` sends in-process (SMTP test endpoint, bootstrap CLI). Secrets
in links go in the `#fragment` (`render.dashboard_link`). The log provider is refused in prod at
API startup and at worker import.

**Usage metering** (`analytics/usage.py`, `api/routes/usage.py`, table `usage_daily`): atomic
upsert per (day, corpus, source) with `source` = `api` | `stream` | `conversation` |
`widget:<key id>`. Recorded in `/query`, `/query/stream`, conversation messages and widget chat;
best-effort, never fails a request. Streaming paths record in their own session
(`record_usage_new_session`) because the request session may be gone. Conversation messages
resolve their LLM like `/query` (X-LLM-* headers → corpus `llm_config` → engine default).
`record_usage` also increments `tenant_question_counts` (monthly cap, `accounts/caps.py`); every
answering route calls `enforce_question_cap` before the cache.

**Billing** (`billing/`, epics BIL1-BIL7 in `docs/internal/plans/epics/billing/`): money is an integer in
minor units (paise, cents) next to a currency (`money.py`); never a float. `pricing.py` (versioned `plan_pricing`
+ the customer's overrides = `EffectivePricing`), `accounts.py` (`billing_accounts`, one per tenant, created
lazily). The wallet is `wallet_ledger`: append-only (a trigger refuses UPDATE, and DELETE unless the account is
already gone), written only by `ledger.append_entries` (locks the account row, idempotent by
`(account_id, idempotency_key)`, never commits); the balance cached on the account is the sum of the ledger, and
`reconcile.py` checks that nightly. A metered request is charged once, **inside `analytics.usage.record_usage`**
(`charging.charge_usage`, in a SAVEPOINT, before the usage upsert): one `usage_charges` row keyed by the request id
in account-currency micro units; a duplicate id skips the usage row and the monthly count too. **A new metered
path must pass a stable `request_id=` to `record_usage`** wherever a retry can repeat the work, and an answering
route calls `billing.context.mark_admin_answer(request)` so the operator testing a customer's knowledge base is
waived. `posting.post_pending` (beat, every `RAG_BILLING_POST_INTERVAL_SECONDS`) turns unposted charges into one
`usage_charge` ledger entry per account with the sub-minor remainder carried (`usage_residue_micro`);
`available = balance - unposted - residue`. `subscriptions.run_due` (hourly) charges the subscription and
included credit per period (keys `sub:<date>`, `inc:<date>`) and expires credit lots (`credit_lots`; only usage
consumes lots). `hooks.after_balance_change` is called after every committed money change. Manual top-ups,
adjustments and refunds are `POST /api/v1/admin/billing/accounts/{tenant_id}/ledger` (audited, typed confirm
above `engine_settings.billing.confirm_above_minor`); a workspace owner reads `GET /api/v1/billing/ledger`
(owner-only, `memo_public` only). Billing is off with `RAG_BILLING_ENABLED=false`. Add-ons (`addons.py`,
`add_on_catalogue`, `add_on_grants`): applying one is a ledger entry (credit pack, or a price paid from the
balance) plus a grant that copies the add-on; the grant id is `uuid5(tenant, idempotency_key)`. **Limits are
`caps.effective_limits` (plan, then the account's `limit_overrides`, plus the grants in force; `None` stays
unlimited); never read `Plan.*` limits directly**, and `question_quota` adds the question packs of that month.
**Enforcement** (`enforcement.py`, `alerts.py`, `hooks.py`): every answering route calls `enforce_billing` after
`enforce_question_cap` and before the query cache (`billing_state`: prepaid `available <= -grace`, postpaid
`<= -credit_limit`, counting unposted usage; BYOK on a free own-key plan, exempt workspaces and the admin
principal never paused; it fails open and never raises). A paused workspace with the assistant on runs the
director with `TurnInput.billing_paused` (rules-only label, no search, no model, the `handoff.messages.paused`
text and the handoff card); with it off the legacy paths answer 402 `billing_paused`, the widget 409
`assistant_disabled`, COB search mode `balance_paused`. A new answering route must call `enforce_billing` right
after its cap, and anything that can spend must keep the paused path free. `hooks.after_balance_change` (own
transaction per account, row lock, audit `billing.paused`/`billing.resumed`, mail and webhooks only after the
commit) drives the transitions and is re-run hourly for every account; `caps._cap_block` applies the overage rule.
**Statements, P&L and the owner page** (`statements.py`, `pnl.py`, `summary.py`, `api/routes/billing_statements.py`,
table `billing_statements`): a statement is the month's ledger entries (by `period`), the usage charges *posted*
in that month grouped by knowledge base, model and source, and the posting remainder at the month's edges
(`residue = Σ posted amount_micro - 10^4 × Σ usage entries`), so `breakdown + rounding_carried == usage total`
exactly; `build_statement` is pure (property-tested), `build_month` reads the rows, `statement_for` returns the
frozen row for a closed month and builds the current one live, and `snapshot_closed_months` (beat on the 1st at
02:30 UTC, after `post_pending`) is idempotent. Membership of a month is the entry's `period` (a subscription
catch-up can land in an earlier month), so the opening balance is the sum of every earlier period. The customer
never sees the markup or the AI cost: nothing in a statement is built from `cost_usd_micro`. P&L (`pnl.py`):
billed = -Σ subscription, usage and negative add-on entries; paid in = top-ups + the *price* of credit packs
paid outside - refunds; adjustments in their own column; AI cost from `usage_daily` (`payer=operator`) at the
fixed rate. The top-up request limit (one an hour) is read from the last `billing.top_up_requested` audit row,
never Redis. The header's `billing` meter is read-only and creates no account.

**Billing invariants and rules** (tested by `tests/unit/test_billing_properties.py`, the CI step "Billing money
gates", `tests/integration/test_billing_month_e2e.py` and `test_billing_tamper.py`). I1: balance = Σ ledger, seq
1..n without gaps, each `balance_after` the running sum. I2: the ledger is append-only (trigger). I3: the account row
is locked before any append. I4: `(account_id, idempotency_key)` is unique, same key with another type or amount
is a 409. I5: `available = balance x 10^4 - owed` (residue + unposted). I6: Σ charges = posted x 10^4 + residue +
unposted. Rules for new code: **a new metered path passes a stable `request_id` to `record_usage`**; **a new
answering route calls `enforce_question_cap` then `enforce_billing`**; **limits are read only through
`caps.effective_limits`**; money is never a float; user-facing text says balance, top-up, statement, plan, workspace,
knowledge base (never ledger, tenant, corpus, minor units).

**Plans and caps** (accounts A6; `accounts/plans.py`, `accounts/caps.py`, `api/routes/plans.py`,
tables `plans`, `tenant_question_counts`, `tenants.plan_id`): four limits per plan, NULL =
unlimited; exactly one default plan (seeded "Standard", unlimited). Effective knowledge-base limit
= `tenants.max_corpora` if set, else the plan's. `check_kb_room` (corpus create/move),
`check_document_room(adds)` on every path that can add an active document (upload, records,
pending-review confirm, version restore, portal ingest — pass what the call adds, 0 for updates),
`check_widget_key_room` (widget-key create). A new add-path must call `check_document_room`.
Unit tests whose mocked session cannot answer the cap queries use the `unlimited_plan` fixture
(`tests/conftest.py`).

**Pipeline profiles and fair use** (PIP1; `tiers/`, tables `pipeline_profiles`,
`tenant_pipeline_settings`, `plans.pipeline_profile_id`): a profile (`tiers/schema.py:ProfileConfig`,
one JSONB, re-validated on read) says how a business's questions are answered; `None` fields follow
the knowledge base, then the engine, so the seeded Standard is a no-op. `tiers/profiles.py:plan_profile`
resolves in one query (operator workspace / system / Internal plan → Premium, else the business's
override, else its plan's, else the default). **`tiers/resolve.py:resolve_with_profile` is the only way an
answering surface or AI helper resolves LLM + retrieval config** (`test_profile_everywhere.py` greps that
no surface calls `resolve_llm` directly; COB, the RAGAS judge and the optimizer's trials are the
documented exceptions). `tiers/apply.py` is the pure overlay: model fields only for operator-paid answers
(a BYOK business keeps its own), knowledge base first unless the profile has `overrides_kb_settings`, the
payer is never changed, `max_answer_tokens` only lowers, `followups=False` only turns chips off. Answers
hold a slot in the priority lanes (`tiers/lanes.py`, Redis keys with expiry and compare-and-delete
release; `tiers/slot.py`: `answer_slot` for the legacy paths, `SlotHold` inside `Director.run_stream`, which
takes the slot after the first frame and never for a paused or learning turn); a timeout is the
director's `paused_by="busy"` reply, 503 `answers_busy` on the legacy API, the widget's one "not
available" sentence on its legacy path. `RAG_ANSWER_SLOTS=0` (default) means no engine-wide pool; a
profile's `max_concurrency` still applies. Redis trouble fails open. Admin API `api/routes/pipelines.py`
(rules in `tiers/admin.py`), audit `pipeline.*`. Unit tests get the Standard profile from the autouse
`tenant_profile` fixture (`tests/unit/conftest.py`); modules named `test_tiers_*`, `test_pipelines_*`
and a few more run the real lookup.

**Fair use** (PIP3; `fairuse/`, tables `fair_use_counters`, `fair_use_alerts`, `plans.fair_use_limits` /
`fair_use_policy`, `tenant_pipeline_settings.fair_use_overrides` / `policy_overrides` / `state`):
**every answering route calls `fairuse/gate.py:answer_gate`** (which runs `enforce_question_cap` and
`enforce_billing` inside, so the cap's 429 body, the admin skip and BIL4 are unchanged) and resolves
through `resolve_with_profile` with `decision.profile` (`routes/query.py:gate_and_resolve`). A new
answering route must call it, pass a stable `request_id` to `record_usage`, and treat the decision:
allow / downgrade answer, pause is the director's pause reply (`paused_by`, `pause_args(decision)`) or
`refuse_paused` on a legacy path (402 balance, 429 `fair_use_limit`), never an error for a visitor.
`decision.py` is pure (`evaluate` makes breaches near/soft/hard, `decide` is the table in its
docstring, property-tested); `limits.py` (`effective_fair_use`: plan, then the business's overrides, absent
= follow, null = unlimited; exempt = operator, system workspace, Internal plan), `usage.py` reads only the
limited metrics (Redis windows for minute/hour fail open, `usage_daily` for day/month, spend fail
closed through a savepoint). A billing downgrade is written waived (`billing/context.py:mark_waived`,
`usage_charges.waived = 'downgraded'`). Alerts, the stored state and the decision counters are written
by a background task the gate schedules only when something happened (`alerts.py`, claim by unique
index so a notice goes out once per crossing). The website fetch budget is an httpx request hook of the
guarded client (`fairuse/budget.py`, `website/fetch.py`), pages per day bound `room` in
`website/learn.py`, `website/resume.py` restarts reads a daily limit cut short, `rails.py` holds the
engine spend ceiling and the crawl slots. Uploads: `accounts/caps.py:check_upload_room` in `/ingest`.
COB: only `cob_questions_per_day` (`cob_blocked`, search-mode reason `fair_use`). Owners never see AI
spend amounts (D13). Unit tests get "no limits" from the autouse `fair_use` fixture
(`tests/unit/conftest.py`); modules named `test_fairuse_limits`, `test_fairuse_usage`,
`test_fair_use_routes`, `test_scope_pipeline_*` run the real lookup.

**The free local model** (PIP2; `llm/local.py`, compose service `local-llm` behind its profile, Ollama,
`scripts/entrypoint-local-llm.sh` pulls `RAG_LOCAL_LLM_MODEL` at first start): the Free profile's
`answer_route="local"` points a whole turn at it (`tiers/apply.py:finalize_llm`, the `# PIP2 local`
block): provider `ollama_chat`, base URL `RAG_LOCAL_LLM_URL`, the non-secret placeholder key `local`
(never the engine's key; `has_credentials` stays true), payer operator, thinking off, one route for the
answer, the fast model and the language map (only models the server lists; nothing is pulled in the
answer path). **Readiness is cached, never fetched in the answer path:** `local_status` probes
`/api/tags` and a lifespan task refreshes it (15 s ready, 5 s otherwise); `local_ready(settings)` reads
that cache and counts one older than a minute as not ready, so `EffectiveProfile.available=False` and a
downgrade pauses instead (D14). The first time it turns ready the model is loaded into memory once.
`llm/cost.py` prices provider `ollama_chat` at 0, known. The pool `local` in the lanes reads
`RAG_LOCAL_LLM_SLOTS`. Admin routes `api/routes/local_llm.py` (status is a live check; the test route
is once per 10 s), `features.local_llm` on `/info`. Benchmarks: `benchmarks/local_llm.py` calls only
`ollama_chat` to the given address (a guard refuses anything else), the passing rule and the file are in
`core/local_benchmarks.py` and the table rides on `/models/answer-benchmarks` as `local`; the setup wizard
reads its `default`. A cold worker process has no cache: `local_ready` is false there (the refresher runs
in the API process).

**Answer speed and fair use in the dashboard** (PIP4; `features/pipelines/`, `features/fair-use/`, `features/customers/tabs/Limits.tsx`,
`components/shell/FairUseBanner.tsx`, strings in `lib/strings/pipelines.ts`, mocks `test/msw/handlers/pipelines.ts` and
`e2e/fixtures/mock-pipelines.ts`): deployer screens only for profiles, plan and business limits and the
engine rails (`/admin/pipelines`, `?tab=limits`, Engine page cards); owners read `GET /fair-use` (banner,
Plan & billing card, Home tile). Rules: **AI spend is typed in dollars and sent as integer micro, never a
float** (`fair-use/limits.ts:usdToMicro`); a plan's `fair_use_limits` / `fair_use_policy` are replaced as
a whole by the engine, so the plan form sends a changed one in full and an untouched one not at all; a
business override goes in `limits`, `policy` and `clear`, only what changed; **owners never see an AI
amount** (spend is a share; `FairUseMeters.OwnerMetricMeter`); the balance banner is `BillingBanner` unless
`GET /fair-use` says `billing_block="downgrade_profile"`, then `FairUseBanner` speaks (answers go on, slower),
and `billing_block` already accounts for an unavailable local model (it reports a pause). A new fair-use
or profile string goes in `lib/strings/pipelines.ts` (no "corpus", "tenant", "lane", "slot", "LiteLLM" for
owners); a new error code needs its message in `lib/api/error-messages.ts`. `benchmarks/fair_use.py` is the
final test (gate overhead, lanes, throttle to pause, local model end to end, website learning).

**Leads** (conversation layer C4; `conversation/leads/`): contact data lives only in `leads.fields_encrypted` (Fernet) with keyed hashes for exact search (`crypto.py`, one hash per configured key so search survives a rotation); `lead_consents` keeps what was agreed; validation in `validation.py`, notices in `consent.py` (texts are `_NOTICE` in `schema.py`, filled with `str.replace`, never `format`), withdrawal tokens in `tokens.py`. The lead offer and the conversational capture register with the offer registry when `conversation/__init__` imports `leads` (so `provider.py` imports `api.schemas.assistant` lazily). Notifications run in the worker (`worker/conversation_tasks.py`: `notify_lead`, one `deliver_corpus_webhook` task per webhook rebuilt from `lead_id`, `purge_expired_leads`; `leads.notified_at` makes a retry send nothing twice). `lead.created` and `handoff.requested` go only to `webhook_config.corpus_id` webhooks (`CORPUS_ONLY_EVENTS`); URLs pass `core/url_guard.py`. A lead's widget chat is erased with it (`service.erase_lead`, `retention.py`). Routes: `api/routes/leads.py`, `corpus_webhooks.py`.

**Handoff** (conversation layer C5; `conversation/handoff/`, table `handoffs`): office hours from the business card (`office_hours.py` adds `next_open`; whether it is open now is `signals.office_state`), `wa.me` / `tel:` / `mailto:` channels in `channels.py` (normalised, an unusable value drops the channel), triggers in `triggers.py` (counters are projected through C1's `after_turn` because offers are collected before the turn is counted), the card provider registered with the offer registry, a widget tap recorded by `POST /widget/handoff` (own rate bucket, availability decides what is offered, not what is recorded), notified in the worker (`notify_handoff`, two phases so a webhook retry holds no connection; `handoffs.notified_at` makes a retry send nothing twice), `handoff.requested` only to KB-scoped webhooks, `summary_encrypted` rotated in `core/reencrypt.py`. Routes `api/routes/handoffs.py` (list `read`, detail and update `write`, nothing owner-only).

**Polish** (conversation layer C9): `conversation/qualification.py` is pure (`qualify`, `notify_now`) and is called by `create_lead`; the digest is `conversation/digest.py` run hourly by `send_lead_digests` (Redis claim per KB and day, fail closed, tenant must be active). `conversation/improve.py` backs `POST .../assistant/rules/improve` (suggestion only, metered as usage source `assistant`, `write` permission). ONE page glob, `conversation/page_glob.py`, serves rules, greetings and lead triggers and is mirrored in `widget/static/widget-core.js`; both read `tests/fixtures/conversation/page_globs.json`, so change them together. `widget-core.js` also holds the widget's chrome strings (English, Hindi, Hinglish; `DEFAULTS_EN` must equal `_DEFAULTS` in `api/routes/widget.py`, pinned by `test_widget_defaults_in_sync.py`).

**Demo mode** (L5a; `demo/`, `api/routes/demo.py`, `RAG_DEMO_*`, `RAG_FRAME_ANCESTORS`): `RAG_DEMO_MODE` (off by default) makes a public demo. **`demo/guard.py:refusal` derives what is refused from `ROUTE_OPS`** (every `delete` and `manage` route, every `DELETE`, plus the short `DEMO_REFUSED` table), so a new destructive or secret route is refused by its own row; a destructive route classified `write` needs a `DEMO_REFUSED` row. The exact admin API key is the only exempt credential (the reset uses it); a super-admin session is guarded like everyone's. The pure-ASGI `DemoGuardMiddleware` sits inside the auth layer and is installed only in demo mode; `llm_config` on knowledge-base create/update is refused through `refuse_hidden_fields`. **A new outbound sender (e-mail, webhook, any call to a customer's service) must check `demo_mode`** (`settings.demo_mode`, or `demo.mode.is_demo()` where no settings object is at hand; building `Settings` takes about 60 ms, so never per call). **A new direct `litellm.acompletion` must call `demo.budget.check_model_call`** (the Redis daily ceiling, fail closed; `LiteLLMProvider._send` already does). `demo/reset.py:run_reset` rebuilds the seed (`demo/data/clinic/`, validated by `demo/seed_data.py`) through the engine's own routes with the admin key; only the two user rows and the owner's membership are written directly (`demo/accounts.py`). It runs in the API process (`demo/scheduler.py`, Redis lock and a claim per night) and from `python -m callobuzz_rag.demo`. Public branding carries `demo` (the sign-ins, public by design) and turns sign-up off. `RAG_FRAME_ANCESTORS` is read only by the dashboard container (`40-cob-config.sh`, `self` and `none` written without quotes) and fills `__FRAME_ANCESTORS__` in every CSP.
**Demo answer limits** (`demo/limits.py`, `demo/day.py`; `RAG_DEMO_ANSWERS_PER_DAY`, `RAG_DEMO_ANSWERS_PER_VISITOR_PER_DAY`): the demo's day starts at `RAG_DEMO_RESET_AT_UTC` (`day.day_key`), and every demo counter (these two and the model-call backstop) is keyed by it, so the limits start again when the data resets. `fairuse/gate.py:answer_gate` calls `limits.check` for every answering route (not COB or eval): it only READS the visitor's and the demo's counters and, over a limit or with Redis down (fails closed), returns a pause with `paused_by="demo_limit"`; the director turns that into one fixed polite message (`limits.notice`: reset time in UTC and IST, no store link, no model, no search, not metered), the legacy paths into `demo_limit_error` (429 `demo_limit`, 503 `demo_unavailable`), and `done.pause_reason` says `fair_use` to callers. It also opens a `DemoTurnScope` (a context variable): the FIRST model call of the turn (`budget.check_model_call`) takes one answer from both counters (increment, undone when over), so small talk the rules answer, cache hits and the human path never count; the free local model is not counted. The visitor is `limits.visitor_hash` (HMAC of the `client_ip` the widget's trusted-proxy rules give, plus the signed-in user id; never a raw address); only the exact admin key is exempt. A new answering surface outside `answer_gate` must call `limits.check` (COB does in `CobOrchestrator._demo_gate` and answers `mode="demo_limit"`) and map a `DemoBudgetExceeded`/`DemoBudgetUnavailable` that can still escape with `limits.polite_text` (a global handler in `api/errors.py` covers routes; the director covers the stream). Public branding `demo.answers` and `GET /admin/demo` carry the numbers only.

**Licence** (OS1-OS4): AGPL-3.0 for the engine and the dashboard; MIT for `clients/js`, `clients/python` and `widget/static/*.js` (D1); every contribution under `CLA.md`; a commercial licence on request at `core.product.COMMERCIAL_LICENSE_URL` (D4). Every feature works for everyone: there is no licence check, no activation step and no call home. A new file in `clients/` or `widget/static/` carries the MIT header (`test_licence_files.py`). A new dependency must pass `scripts/dependency_audit.py` (no GPL family: it would block the commercial licence). `tests/unit/test_public_repo.py` keeps env files, `docs/internal/`, scratch files, store links and store words out of the tracked tree.

**Value report** (TV4; `business/value.py`, `value_report.py`, `value_freeze.py`, `value_defaults.py`, `api/routes/value.py`, `worker/value_tasks.py`, tables `value_settings`, `value_reports`): `estimate` is pure and integer-only (`qualified leads x lead value + handoffs x handoff value + answered x minutes_per_answer x staff hour // 60`; **never a float**) and always returns its formula lines, so every screen says "estimated, based on your settings" and nothing says ROI or promises a result. `fetch_inputs` reads one workspace and one window (all its knowledge bases; previews and spam out; definitions pinned in the trust README); after-hours answers come from `assistant_turns.office_open` (written by `persist_turn` from the turn's `OfficeState`; NULL = unknown, never guessed for the past). Months follow the business card's time zone (`month_bounds`). A closed month is **frozen** in `value_reports` by the hourly beat `freeze-value-reports` (only on the 1st to 3rd in India; one transaction per workspace; `emailed_at` is stamped after the e-mails were queued, so a queue failure retries); a frozen month's data is never recomputed. Settings are one row per workspace created by the first `PUT` (owner-only; currency from the billing account, else the plan's pricing, else the engine default); suggestions per `identity.business_type` are data in `business/data/value_defaults.json`. `trust.value_report_enabled=false` stops the e-mail and hides the Home card (`HomeOut.value`), `trust.value_report_cc_deployer` copies `branding.support_email`.

**No surprises** (TV3; `billing/notice.py`, `renewal.py`, `spend_cap.py`, `price_sheet.py`, `summary.py` trust fields, `support/`, `worker/trust_tasks.py`): **a price raise goes through `billing/notice.py` (`save_with_notice`, `hold_overrides`, `hold_plan_move`); never flip `is_current` or write `plan_pricing`/`pending_change` by hand.** A scheduled version has `plan_pricing.effective_at` in the future; `pricing_in_force` / **`effective_pricing(at=)` reads the version in force at the workspace's period start** (a subscription charge uses the pricing at its own charge day), so a raise never lands inside an announced period. `activate_due` and the pending-change pass run hourly; `renewal_pass` at 09:00 IST (notice rows are idempotent in `billing_notices`, written after the enqueue). The spending cap is `billing_accounts.spend_cap_minor`: `fairuse` metric `spend_cap_per_month` (row 6 of `decide`, reason `spend_cap`, downgrade to local else pause; a human request is never refused), alerts at 80/100% to owners only, audit `billing.spend_cap_reached`. Support requests (`support/service.py`) are tenant-scoped (another workspace's id is a 404), notify the provider and the business by e-mail (context key `request_subject`, because `subject` is reserved), and carry a context snapshot built by `support/context.py`. The price list is plans and add-ons only, never the provider's costs or markup. New audit actions have producers in `tests/unit/test_audit_coverage.py`.

**Human path** (TV1; `conversation/human.py`, `conversation/loop_guard.py`): `is_human_request(text)` asks the rules data only (`data/rules/human_request.json`, EN/HI/Hinglish; never a model; abuse wins). **A new answering surface checks `conversation.human.is_human_request` before the cap, billing and fair-use gates** (`surfaces.is_human_turn(runtime, text)`) and runs the turn with `human_only=True` (`TurnInput.model_free`: rules label, no search, no model, no lane slot, not metered, not a counted question); the widget spends the handoff bucket (`_spend_handoff_bucket`) instead of the chat bucket. `decide_route` answers it before `stopped`. `HandoffCardProvider.build` never returns None for `explicit_request`/`loop`/`misses` when something usable exists (contact-line card), and the director says `NO_CHANNEL_TEXT` when nothing does. Misses: `ConversationState.miss_count` (a `NO_ANSWER`, a failed gate or a decline that is not the stop reply), `HandoffTriggers.offer_after_misses` (0 to 5, default 1; a defaulted field, no schema version bump), reason `misses` first among the soft triggers. Loop guard: `note_reply` after each non-small-talk reply, two equal fingerprints set `repeat_count`, the next turn is `_loop_decision` (card, else contact line) and `reset`. The widget header button: `human_button` in `/widget/config` (`surfaces.widget_human_button`: assistant on, `has_human_path`, `handoff.show_human_button` or not `trust.allow_hide_human_button`), `humanButtonHtml`/`humanAsk` in `widget-core.js`, the ask text `HANDOFF_ASKS` in `widget.js` (equal to `human.HUMAN_ASK`, pinned by `tests/unit/test_human_request_rules.py`). **References** (TV2; `conversation/handoff/reference.py`, `prefill.py`): one `XX-XXXX` code per conversation (`conversations.ref_code`, partial unique index per knowledge base, alphabet `23456789ABCDEFGHJKMNPQRSTVWXYZ`, `normalise_ref` refuses look-alikes instead of guessing). `reserve_ref` (own session, never blocks the turn, `None` on any failure) is called by the director just before offers are collected when a handoff card can be shown on a real, non-preview conversation (`_card_can_be_offered`) and passes the code in `OfferContext.ref_code`; `assign_ref` does the same inside a caller's transaction (`create_lead`, SAVEPOINT per attempt). The `wa.me` text is `prefill.whatsapp_prefill(ref, ConversationState.recent_questions, lang)`: the visitor's own masked questions, **rules only, never a model**, at most 300 characters. **Owner-facing text without the visitor's consent has contacts hidden** (`pii.mask_contacts`; consent = a lead exists in the conversation, any doubt hides); anything new that carries a reference or summary to the owner must use `refs_for`/`ref_code_of` and the same rule. The consent notice is v2 (`schema._NOTICE`; `_NOTICE_V1` frozen; `consent.notice_for` shows an unedited v1 as v2 and `notice_version_for` reports it, owner-edited text is never rewritten; the dashboard's `consentNotice.ts` is pinned equal by `tests/unit/test_consent_notice_v2.py`). `GET /api/v1/handoffs/lookup` is declared before `/{handoff_id}`.

**Disclosure and freshness** (TV5; `conversation/freshness.py`, `surfaces.widget_disclosure`/`disclosure_line`, `business/home.py` nudges, `onboarding/service.py` `files_summary`): the widget config carries `ai_badge`, `disclosure_line` and `disclosure_required`; **`trust.require_ai_disclosure` (default on) overrides the owner's `identity.ai_disclosure`, and the owner's wording is used only while it contains the whole word "AI"/"एआई" (`disclosure_wording_editable`)**; the badge is chrome (`AI_BADGE`, `widget-core.js` `aiBadge`), never editable, and an unreadable setting keeps it shown. "Updated" = `document_versions.created_at` of the chunk's own version (loaded with the chunk, no query), turned into the business card's local date by `freshness.updated_on`: only `start_retrieval` passes `updated_tz` (orchestrator -> `ContextAssembler.assemble`), so `format_citation(..., updated=None)` and every other path stay byte-identical; `source_of` carries `updated_at` and `widget_source` adds the key only when known. The compiled prompt has one grounded rule 6 about the `updated` date (golden files). The first-run wizard has **four steps** (`onboarding_states.step` 1..4: website, brochures, install, try; `phase17_002` shifted the stored ones): a new step number or a changed meaning needs a migration of the stored rows. Home `nudges` (`no_human_path`, `add_brochures`, `set_values`) cost exactly one extra query and never fail the page. The leads CSV export is a data route (not in `ROUTE_GATES`) and stays owner-only.

**Assistant configuration** (conversation layer C2; `conversation/schema.py`, `store.py`,
`compiler.py`, `templates.py`, `language.py`, `translate.py`, `business_card.py`; table
`assistant_configs`, routes `api/routes/assistant.py`): one published version plus at most one
draft per knowledge base, versioned, `draft_revision` guards edits (409), publish / restore /
enable are owner-only (`require_owner`; `write` for keys in `ROUTE_OPS`). On for every KB
(`phase12_001` and `create_corpus` publish the "Customer support" template; `enabled=false` =
raw grounded answers). `compile_prompt` fixes the prompt order (golden files in
`tests/golden/expected/assistant_prompts`; regenerate with `UPDATE_GOLDEN=1` and read them);
templates are data in `conversation/templates/`. The business card is the system record
`system:business-card` (`business_card.py`): record ids starting `system:` are reserved, excluded
from the plan's document cap and from boilerplate stripping, and refused by the document routes
(409 `system_document`). `get_runtime_config` is what the director (C1) reads on every turn; its
cost capture, `extra_queries` and `translate_for_retrieval` must use the request's own LLM
provider. Request models inherit `core.schemas.StrictRequest`; new routes need a `ROUTE_OPS` row.

**COB, the built-in assistant** (`cob/`, `api/routes/cob.py`, epics in
`docs/internal/plans/epics/cob/`): the engine answers questions about itself. `cob/system.py` keeps
one hidden *system* workspace and knowledge base (`is_system`, protected by 409
`system_resource_protected`); `cob/sync.py` ingests `docs/cob/manifest.yaml`'s sources into it as
`system:cob/<page>#<section>` records, each with an `audience` (`admin` | `customer` | `public`).
`CobOrchestrator` (`cob/orchestrator.py`) runs a turn in two steps: `prepare` (the conversation,
the monthly cap, whose key pays: `llm_choice.py`) and `stream`, which reuses the conversation
director (`surfaces.build_director(..., llm_config_override, retrieval_filters)`) with the fixed
config in `cob/data/cob_assistant.json` and **always** sets `metadata_filters={"audience":
allowed_audiences(role)}` from the session's role (`caller_from_request`; keys are refused with
`session_required`); nothing in a request can widen it. COB re-shapes the director's `meta`,
`sources` and `done` events and adds `ui` blocks (`cob_link`, `cob_search_results`). At the
monthly question cap, or with no LLM key, `search_mode.py` answers with retrieval only and no
model call. A non-owner question about server things ends in the "managed by your platform
administrator" reply when a dense probe of the admin-only records matches (`audience.py`). Chats are
`conversations` rows with `kind='cob'`, owned by `user_id`, scoped to `scope_tenant_id`, kept
`RAG_COB_HISTORY_DAYS` days (`accounts/cleanup.py`); every assistant-kind conversation route and
`surfaces.conversation_context` filters on `kind`. Metering is `source=cob` on the asking
workspace (the `usage_daily` key includes `tenant_id`); only a turn that called the answer model
counts as a question.

**COB actions** (`cob/proposals.py`, `cob/tools/actions/`, `cob_action_proposals`): the 18 action
tools (`ActionBase`: `args_model` = the person's words, `exec_model` = resolved ids) propose a
`ProposalDraft`; the orchestrator stores a row and streams a `cob_action_card`. Only
`POST /api/v1/cob/actions/{id}/confirm` executes: one `UPDATE ... WHERE status='pending' AND
expires_at > now` claims the row (single use, 10 min, user + workspace + role re-checked), a
destructive card needs the current name typed, then `tool.execute` calls the tool's declared routes
through `InternalClient` with the caller's own token (`ToolClient` refuses any other route), writes
`cob.action` and a chat message in one transaction, and returns the route's own status. Never store
a secret from a response (`ActionOutcome.secret` goes to the caller only). A new action tool needs
rules in `router_rules.json`, strings in `tool_strings.json` (en, hi, hi-Latn), `ROUTE_OPS` rows for
its routes and passes `tests/unit/test_cob_action_parity.py` (roles, destructive and owner-only flags
follow `ROUTE_OPS` and `OWNER_ONLY`).

**Public COB** (`cob/public.py`, `api/routes/cob_public.py`): `POST /api/v1/public/cob/chat` (under
`OPEN_PREFIXES`, no credential) runs the same orchestrator with `CobTurn(public=True)` and the
`PUBLIC` role: filter fixed to `["public"]`, no router or tools, `TurnInput.persist=False` and no
conversation (nothing but `usage_source=cob_public` metering is written), client `history` masked by
`conversation/pii.py`. `PublicCobLimiter` (Redis, IP hashed) **fails closed** (503
`cob_public_unavailable`), unlike the widget; it is on only while `RAG_COB_ENABLED`,
`RAG_COB_PUBLIC_ENABLED` and the engine setting `cob_public_sign_in` are (`cob_public` in public
branding). A new COB route that must work signed out belongs in `PUBLIC_ROUTES`, never on a session.

**WhatsApp channel** (`channels/`, `channels/whatsapp/`, `api/routes/whatsapp.py`, `whatsapp_public.py`, epic TV6 in `docs/internal/plans/epics/trust/TV6-whatsapp.md`, migration `phase17_003`): the assistant answers on a business's own WhatsApp Business number through the SAME director as every other surface (`surfaces.make_turn`, `director_for_worker`), so the labels, gates, fact check and persistence are not copied. `channels/base.py` is the adapter contract and `channels/policy.py` the shared gates; WhatsApp is the first adapter. Flow: `POST /api/v1/public/whatsapp/webhook` (public; verify token on GET, `X-Hub-Signature-256` over the raw bytes with the deployer's app secret on POST) stores `channel_events` (encrypted payload, unique by Meta's message id) and returns 200; the worker (`worker/whatsapp_tasks.py`, `channels/whatsapp/dispatch.py`) claims one in-flight event per chat under the `channel_threads` row lock and runs `inbound.py`: the human check first (a request for a person passes before every gate), then `enforce_question_cap`, `enforce_billing`, fair use (`answer_gate_for`), with a stable `request_id` for `record_usage`; STOP/START (`optout.py`) are rules. `render.py` splits to 4,096 characters and turns Markdown into WhatsApp's formatting; `outbound.py` is the only sender and `graph.py` the only HTTP client: it talks to `graph.facebook.com` over https at `RAG_WHATSAPP_GRAPH_VERSION` and never logs a token. A conversation is one per chat per 24-hour window; a dashboard reply or a coexistence echo (`smb_message_echoes`) pauses the assistant (`human_pause_hours`); `documents.py` sends only the owner's ticked PDFs/JPGs/PNGs; `followups.py` sends one approved template, only with an opt-in; `fees.py` charges Meta's fee once per message through `billing.charging.charge_pass_through` (rates are engine settings, zero by default, nothing hard-coded). Tokens, phone numbers and payloads are Fernet-encrypted with keyed hashes for lookup and rotated in `core/reencrypt.py`. `signup.py` and `service.py` connect and disconnect (a disconnected number is tombstoned so chats stay readable). The plan flag is `plans.channels` (`whatsapp`, `whatsapp_fees`), read only through `accounts/plans.py`; the page and routes are hidden/refused when it is off. Never run a test against Meta: use `tests/unit/whatsapp_fixtures.py` (FakeGraph). Dashboard: `features/whatsapp/` (the Facebook SDK is added only on `/whatsapp`; the `message` event is read only from `https://www.facebook.com`), `lib/strings/whatsapp.ts`.

**Payments** (P1; `billing/payments/`, `billing/invoicing/{gst,gstin}.py`, `core/company.py`, tables `payment_provider_settings`, `billing_profiles`, `payment_orders`, `payments`, `payment_events`, `payment_refunds`, migration `phase19_001`, epic in `docs/internal/plans/epics/saas/P1-razorpay-payments.md`): **money arrives only through `capture.apply_captured`**: one captured payment is one ledger entry keyed `rzp:<provider_payment_id>` (`TOP_UP` of the *taxable* value; a credit pack's own `ADD_ON_PURCHASE +(credit+bonus)` is that entry; any other add-on is then bought from the balance, `apply_paid_add_on`, funded so it is never refused), in one transaction under the order row lock, idempotent under any race (checkout verify, webhook worker, `events.sweep`, nightly `recon`), never crediting a payment that does not match its order (stored `mismatch`, operator mailed). **The webhook route only verifies the HMAC over the raw body, stores the event by `x-razorpay-event-id` (payload Fernet-encrypted) and returns 200; `events.process` fetches the true state from the provider, so no payload amount is ever trusted.** `RazorpayProvider` is the only Razorpay HTTP client (`_request` takes paths, never URLs; fixed base `https://api.razorpay.com/v1`; no redirects; no key in a message); tests use `FakePaymentProvider` (a `RazorpayProvider` subclass, so signing is the real code) injected through `app.state.payment_provider_override`. Keys live Fernet-encrypted in `payment_provider_settings` (never `engine_settings`), env wins, rotated in `core/reencrypt.py`. GST is pure integer math (`invoicing/gst.py`, `half_up`, `refund_split`; never a float) from the seller (`engine_settings.company`/`.invoicing`) and the workspace's `billing_profiles`; `payment_orders`, `payments`, `payment_refunds`, `payment_events` carry `tenant_id`/`account_id` with **no foreign key** (financial records outlive a workspace). Refunds (`refunds.py`) write `REFUND -taxable` keyed by OUR refund row and commit before calling the provider; a refusal reverses with `ADJUSTMENT +taxable` (`refund-failed:<id>`); lock order is account row, refund, payment. R6 (`reconcile.check_settlements`, `payments/recon.py`) checks Razorpay's settlement report nightly and copies fees onto payments; the P&L carries the gateway fee. The dashboard's `/pay` is the only page that loads Razorpay's script (nginx `location = /pay`, reached by a full page load via `features/payments/checkout.ts:pageLoad`). Payments and invoices sit behind `billing_enabled()`; routes answer 404 `payments_disabled` without a provider; owner routes are owner-only; fees never reach an owner view.

**Plans for a hosted service** (P5; `billing/byok.py`, `service_orders.py`, `addon_renewals.py`, `seed_saas.py` + `data/saas_seed.json`, `db/models/service_order.py`, routes `billing_shop.py` and `service_orders.py`, migration `phase19_004`, epic in `docs/internal/plans/epics/saas/P5-saas-plans.md`): **whether a workspace may use its own AI key is `billing/byok.py` only** (`plans.byok_allowed` OR an active `feature_unlock` grant with `feature = 'byok'`; the operator, system and Internal-plan workspaces are always allowed). The answer path never calls it: `tiers/profiles._plan_profile` selects `Plan.byok_allowed` and an `EXISTS` over active grants in the same one statement that finds the profile, carried on `EffectiveProfile.byok_allowed` (and through a fair-use downgrade: `fairuse/gate.py` copies it to the target), and `tiers/resolve.resolve_with_profile` applies `usable_llm_config` / `usable_headers` BEFORE it resolves anything, so the payer becomes `operator` and every gate, meter and charge follows. `payer_hint` takes a required `byok_allowed`. **A new reader of a stored LLM key must pass `tests/unit/test_byok_everywhere.py`** (it lists every file that may name `api_key_encrypted`). Saving a key is refused by `byok.refuse_key_save` (403 `byok_not_in_plan`); removing one never is; the stored key is never changed. Add-on kinds `feature_unlock` and `service` (`renews`, `sold_to_owners`, `placeholder`, `sac_code`, `includes_support_months`); a `service` grant opens exactly one `service_orders` row and a support request in the same transaction (`service_orders.open_for_grant`, any payment kind); the new-order mail to the provider is claimed once (`provider_notified_at`) by the purchase route and the hourly schedule. **Renewals are `addon_renewals.renew_due` only** (key `addon:<grant>:<period start>`, each missed period once, `addons.past_the_pause_line` is the one pause rule shared with the shop, a balance that cannot cover it sets `cancelled_at` and mails `add_on_ended`, never a pause; billing off extends without a charge). The shop lists only `sold_to_owners` items; an online order for anything else is 409 `add_on_not_sellable`. The seed command never changes an existing plan, pricing or add-on.

**Self-service plan choice** (P6; `billing/plan_change.py`, `api/routes/billing_plans.py`, `plans.self_service`,
`tenants.requested_plan_id`, migration `phase20_002`): **an owner's plan move goes through `plan_change.change_plan`
only** (the operator's `POST /admin/tenants/{id}/plan` is unchanged and cancels an owner's pending move). Only
`self_service` plans can be chosen. Up = at once, `prorate` (integer floor by days, never negative) of the fee
difference charged as ONE `subscription_charge` keyed `plan-change:<tenant>:<plan>:<period start>` plus the
credit share (`plan-change-credit:...`); a workspace with no schedule pays its first period under `sub:<day>` /
`inc:<day>`, the keys `run_due` would use. A balance that cannot cover it (`covers`, the shop's pause line) stores
`pending_change = {plan_id, by: "owner", awaiting_payment, quoted_minor, expires_at}` with NO `pending_change_at`,
applied by `settle_awaiting`, which `hooks.after_balance_change` runs first (so any top-up settles it; 7 days or
the period end). Down = TV3's pending change at `ist_midnight(renewal)` (`by: "owner"`), applied by
`notice.apply_due_account_changes`, no refund. A pending change without `by: "owner"` is the deployer's and blocks
the owner (409 `plan_change_pending_from_provider`). Sign-up: `?plan=` resolves through `resolve_ref` (anything
else is the default plan, silently), rides in the verify code's payload, applies via `apply_signup_choice` (a fee
waits for payment, never charged) or, pending approval, in `tenants.requested_plan_id` until `lifecycle.activate`.
Billing off: fee plans 409 `plan_change_needs_billing`. Dashboard: `features/plan-choice/`, strings
`lib/strings/plan-choice.ts`; landing buttons `landing/pricing.py:_cta`.

**Tax invoices** (P2; `billing/invoicing/{numbering,documents,pdf,words,export,display,store}.py`, bundled Noto Sans in `invoicing/fonts/`, tables `tax_documents` and `tax_document_counters`, migration `phase19_003`, epic in `docs/internal/plans/epics/saas/P2-gst-invoices.md`): **an invoice is issued only in `apply_captured`'s transaction through `documents.issue_invoice`, and a credit note only in the refund's own transaction through `issue_credit_note` (the seams in `billing/payments/documents.py`); numbers come only from `numbering.next_number`** (a row-locked counter per series, prefix and Indian financial year, so a rollback returns the number and there are no gaps; `PREFIX/YY-YY/NNNNN`, at most 16 characters). **`tax_documents` are frozen and have no tenant foreign key** (a trigger refuses every UPDATE except filling `pdf_key`, `pdf_sha256`, `emailed_at` once, and every DELETE; never edit a document, a correction is a credit note). The seller and buyer are snapshots copied at issue. The PDF is written after the commit by `callobuzz_rag.render_tax_document` and retried by `callobuzz_rag.sweep_tax_documents` (`store.py`; reportlab, `invariant=1` so the stored SHA-256 reproduces; `billing/` is never collected by `storage/gc.py`). The wallet is never touched by a document. `display.inclusive_total` mirrors `lib/format/gst.ts` (shared `tests/fixtures/gst_vectors.json`). R7 (`reconcile.check_documents`) reports a missing invoice or credit note, never fixes it. Credit notes stop 30 November after the invoice's financial year (`credit_note_skipped`).

**Landing site** (LP1-LP4; `landing/`, `api/routes/landing.py` public HTML, `api/routes/landing_admin.py` editor, tables `landing_sites`, `landing_versions`, `landing_assets`, migration `phase20_001`, epics in `docs/internal/plans/epics/landing/`): a public website for the deployer's brand, **off by default** (`landing_sites.enabled`). **One row per install** (`id = 1`, created lazily by `store.ensure_site`, never by the migration); the draft is one validated JSON (`schema.LandingContent`, `StrictRequest` models) saved against `draft_revision` (409 `landing_revision_conflict`), a publish freezes it with its theme as a numbered `landing_versions` row, and every switched-on language must be complete at publish (409 `landing_translation_missing` with `paths`). **`schema.py` is the only place text limits live** (`text_limits()` goes to the editor in `GET /admin/landing` `limits`, with `upload.video_mb` and `upload.image_mb`; the dashboard never repeats a limit). Colours come from one brand colour (`theme.derive_theme`: OKLCH ramp, measured contrast, light and dark; the dashboard previews through `POST /admin/landing/theme/preview`, there is no second implementation). Pages are Jinja (autoescaped, never `|safe`), the small JS and CSS come from the Vite build in `services/dashboard/landing/` (committed under `landing/static/build/`, regenerated only in a finish task, size budgets in `test_landing_budgets.py`). **The canonical address, sitemap and links are built from the site's address or `RAG_PUBLIC_URL`, never from `Host`**; `website/service.py` skips the engine's own origin. **Every landing path is in the Caddy matcher and in `ENGINE_ROOT_PATHS` (`test_deploy_files.py`)** (the dashboard is also at `/app`); a site that is off answers 404 with `X-Cob-Landing: off` so the proxy falls back to the dashboard. `copy_rules.py` flags words such as ROI, guaranteed and 100% accurate (a test over the shipped copy). Editor routes are operator only (`require_admin`, `manage`; workspace sessions and `tk_`/`ck_` keys get 403, `test_scope_landing_routes.py`). **"Ask it yourself"** (`assistant_link.py`) reuses an active key of an *operator* knowledge base that already allows the site's origin, else creates one named "Public website" through `cob.tools.http.InternalClient` with the caller's own credential (so caps and audit run), and never edits another key; disconnecting only clears the link. Generating the hero conversation (`generate.py`) is one metered model call (`usage` source `landing`), a draft only. The preview is a signed 15-minute link to the draft (`preview.py`), `noindex`, framed only by the dashboard. Contact enquiries land in the operator workspace's support inbox ("From your website"). Dashboard: `features/landing/` (one page, a local draft context `DraftProvider`, forms drawn from the lists in `sections.ts` and `pageSpecs.ts`, `fontPairings.ts` pinned to `landing/fonts.py` by `test_landing_editor_fonts.py`), strings `lib/strings/landing.ts`, MSW `test/msw/handlers/landing.ts`, mock engine `e2e/fixtures/mock-landing.ts`; the sample-conversation presets and the generate routes are LP2's and are typed by hand in `features/landing/api.ts` until the generated schema carries them.

**Legal pages** (P3; `legal/`, `api/routes/legal*.py`, tables `legal_documents`, `legal_acceptances`, migration `phase19_002`, epic in `docs/internal/plans/epics/saas/P3-legal-pages.md`): eight deployer-editable Markdown pages (`legal/slugs.py` `SLUGS`; templates in `legal/templates/*.md`, each starting with a "have your lawyer review" marker). A slug has at most one draft row (`version IS NULL`) and any number of **published rows that a trigger (`legal_documents_frozen`) refuses to update or delete**, so an acceptance points at the exact text seen; `service.publish` fills the placeholders (`render.fill`, `str.replace` only, `PLACEHOLDERS` in `legal/render.py`; values from branding, `company` and `legal` engine settings in `service.placeholder_values`) and renders the HTML once. **`render.to_html` is the only Markdown renderer for public pages** (markdown-it-py `commonmark` + tables, `html=False`, a short link-scheme list, `rel="nofollow noopener"`); `GET /legal/<slug>` (`api/routes/legal.py`, outside `/api`, Caddy `/legal/*`) serves it with a locked CSP and `api/routes/legal_public.py` serves JSON. Publishing needs `confirm_reviewed` and no missing placeholder. **Sign-up requires `accept_legal` only when Terms and Privacy are both published** (`service.required_versions`, `check_acceptance`: 422 `legal_acceptance_required`, 409 `legal_version_changed`); the versions ride in the verify-code payload and `record_acceptances` writes the rows at `verify`. The widget's privacy link and the lead consent `{privacy_link}` use the business's `consent.privacy_policy_url`, else the operator's published privacy page (`legal/privacy_link.py`, a 30 s per-process cache refreshed in `_authorize_widget`). Admin routes are `manage` (demo guard: acceptances are a private read); `legal` links ride on `GET /public/branding`.

**Keyword search**: sparse vectors carry BM25 term weights (`retrieval/sparse.py`); Qdrant
applies IDF at query time (`Modifier.IDF`, `_SPARSE_CONFIG` in `vectordb/qdrant.py`, existing
collections upgraded in place). See "Index text and vectors" above.

**Embedding model migration** (`migration/`, `api/routes/migrations.py`): per-corpus
`embedding_config`, drift detection (`detect_embedding_drift` beat task) creating
`pending_approval` rows in `corpus_migrations`, blue-green re-embed into a new Qdrant
collection, verify, rollback. Known limit: a TEI container serves exactly one model, so a corpus
asking for a different TEI model than `RAG_TEI_MODEL_ID` still gets that container's vectors.

**Website learning** (`website/`, `worker/website_tasks.py`, `api/routes/website.py`, epics BIZ1 and BIZ2, in
`docs/internal/plans/epics/business/`): the engine reads a business's own site. A site is learned only
when its origin is an allowed site of an ACTIVE widget key of the knowledge base (`service.allowed_sites`;
never `*`, an IP or `localhost`); one `website_sources` row per knowledge base and site scope (the host
minus `www.` plus its subdomains, `urls.site_scope`, decision D1). `netguard.py` is a port of the scraper's
SSRF guard (a drift test compares the constants with `services/scraper/scraper/crawl/netguard.py`) plus
`check_hop`, awaited on every redirect hop so `fetch.py` refuses a hop that leaves the site and obeys
robots on it. `discover.Frontier` is best first by `rank.score`; `learn.py` runs a discovery
(`learn_source`) or just some URLs (`learn_urls`, what BIZ2's checks call) with all collaborators in
`LearnDeps`, so it is tested without network. Pages become `TextRecord`s with STABLE metadata only (D3:
ETag, hash, last seen live on `website_pages`, or an unchanged page would be re-embedded every visit),
ten per `IngestJob(mode="website")` through `worker.tasks._run_records_pipeline`, after
`ingest.document_room` (the plan's document limit) so pages beyond it are `over_limit`. Runs are recorded
in `website_runs` with a heartbeat (stale after 15 minutes) and a partial unique index allows one open
discovery run per site. A widget-key create, edit or delete calls `service.on_origins_changed` (after its
own commit, never failing the request). Pages that need a browser go to the scraper's
`POST /api/v1/render` (own token). Decisions D1-D3, D6, D7 are in the epic README.
**Freshness (BIZ2):** `freshness.py` holds the interval rules (`next_interval`, `jitter`, `first_check`);
`check.py` is one check of a page (`check_pages`, built on `learn._Run`: sitemap `lastmod` read once per
run and conditionally, then a conditional GET, then the hash of the extracted text; only different text is
ingested again) and the beat job `schedule_checks`, which moves `next_check_at` forward and commits
BEFORE dispatching (a lost task cannot cause a storm; `FOR UPDATE SKIP LOCKED`, at most 200 pages per site
per tick), closes runs silent for 15 minutes and restarts sites that never ran. Removal (D8) needs two
404/410 answers at least an hour apart or a policy reason (`noindex`, robots, off-site redirect); a page
absent from the sitemap only stops counting as a sitemap page, and `archive.py` clears the document's
fingerprint so a page that returns is imported again. `push.py` owns the learn key (`lk_`, SHA-256 only,
shown once, D4) and the public push endpoint's limits (Redis `INCRBY` per key, `SET NX` per URL, fail
closed with 503); `core/logging.py:LearnKeyRedactor` masks the key in access logs because it is in the
URL path. `sensor.py` handles the widget's `{page_url, fingerprint}` report (D9: widget codes are compared
only with earlier widget codes; a change only queues a server read; one per page per
`RAG_WEBSITE_SENSOR_MIN_INTERVAL_SECONDS`, 60 per site per hour; always 204). `widget/static/sensor.js`
is joined into `/widget.js` before `widget.js`.

**Simple mode** (`api/features.py`, `business/home.py`, `api/routes/home.py`, `api/routes/articles.py`,
`pipeline/records_queue.py`, epic BIZ3): a business's dashboard is seven plain menu items with the technical
pages under a collapsed Advanced group, and the deployer decides per plan what a business sees
(`plans.dashboard_features`; every flag means "shown" except `widget_code_only`, D10). `ROUTE_GATES` and
`FIELD_GATES` list the configuration routes and body fields a hidden feature refuses with 403
`feature_not_in_plan`; the auth middleware's session branch applies them with `gate_session` (the plan
flags ride on the session's single SELECT, so there is still one query) and the routes call
`refuse_hidden_fields`. Only tenant SESSIONS of a non-operator workspace are gated (D11): `tk_`/`ck_`/`pk_`
keys, the admin principal and the operator workspace never are, and data routes are never in the tables.
**A new configuration route must be added to `ROUTE_GATES` / `FIELD_GATES` (or be a data route);
`tests/unit/test_features_module.py` checks every entry names a real route.** `GET /home` summarises the
workspace's main knowledge base (its oldest, D13) with "today" in the assistant card's time zone. Articles
and FAQ are `knowledge_articles` rows (the owner's text, editable) learned as records `article:<id>` /
`faq:<id>` through `records_queue.queue_records`, the one add-path shared with `POST /ingest/records`
(`check_document_room` once, one job). Dashboard: `lib/features.ts` (`useDashboardFeatures`), `lib/nav.ts`
(`tenant` placement per page), `components/shell/AdvancedNav.tsx`, `features/home`, `features/knowledge`,
`features/simple-assistant`; a business lands on Home at `/`, the operator on Overview.

**First run** (`onboarding/service.py`, `api/routes/onboarding.py`, `api/routes/avatars.py`,
`assets/avatars.py`, `website/suggest.py`, `conversation/learning.py`, epic BIZ4, migration `phase15_002`):
`POST /onboarding/website` runs its steps (knowledge base, assistant draft and publish, widget key,
learning) through `cob.tools.http.InternalClient` with the caller's own `Authorization` or `X-API-Key`
(D14), so owner rules, caps, audit and strict bodies run as on the dashboard; **onboarding never writes a
corpus, assistant or key row itself**. The `onboarding_states` row is locked `FOR UPDATE`, each step
stores the id it made, and a failing step raises the inner route's own status and code (`_raise_for`) after
committing what succeeded. First seen: `GET /widget/config` calls `onboarding.service.note_widget_load`
(best effort, never fails the config): one upsert per (key, origin) at most an hour apart into
`widget_origin_seen`, refused origins kept too (20 per key), and learning (`trigger="widget"`) starts only
for the first allowed sighting of a site that is **literally** in the key's `allowed_origins`, never via `*`
or the Origin header alone. Still learning (D15): `TurnInput.learning` (set in `conversation/surfaces.py` by
`learning.kb_is_learning`, one query) makes `director._learning_decision` turn model and search routes
into the "still learning" handoff reply; `TurnInput.model_free` is `billing_paused or learning` and
guards the label, search and model calls; billing's pause wins. Avatars (D16): `assets/avatars.reencode`
decodes PNG/JPEG/WebP only (never SVG), checks the declared size before decoding, crops and re-saves a
256 px PNG at `corpus/{id}/avatars/<id>.png`; the public route serves only that, with `nosniff` and a
sandboxing CSP; `Identity.avatar_url` accepts the engine's own `/api/v1/public/avatars/<uuid>.png` path
(`conversation/schema.py`) and `GET /widget/config` makes it absolute. Dashboard: `features/onboarding`,
route `/welcome`, `lib/avatar.ts` (an uploaded picture is a path that needs the engine's address).

**Deploy** (L4; `deploy/`, `docs/requirements.html`): `deploy/install.sh` (fresh Ubuntu 22.04/24.04 or
Debian 12; checks, Docker from the official repository, two questions, the setup wizard inside
`python:3.12-slim`, stack, health, sign-in link, nightly backup cron), `upgrade.sh` (backup, build beside
the old release, wizard on the existing settings file, `alembic upgrade head`, switch, health;
`--rollback` switches back and restores the pre-upgrade backup), `backup.sh` / `restore.sh`, shared
functions in `deploy/lib/common.sh` (pure ones are tested). Every script has `--dry-run` ("would: ..."
lines, writes nothing) and the exit codes 0 done, 1 failed, 2 bad arguments, 3 a check failed, 4 refused.
Layout on a server: `/opt/cob-rag/{.env,current -> releases/<version>,releases/,backups/,state/}`; compose
project `cob-rag` is pinned by the file so volumes survive every release. **One domain**
(`RAG_SITE_DOMAIN`, compose profile `proxy`, `deploy/caddy/Caddyfile`): `/api/*`, `/widget.js`,
`/privacy/withdraw` and `/privacy/withdraw.js` go to the engine, `/scraper/*` (prefix stripped) to the
scraper, everything else to the dashboard. **A new engine path outside `/api/` must be added to
`deploy/caddy/Caddyfile`** (`tests/unit/test_deploy_files.py` fails otherwise), and no dashboard route may
start with `api` or `scraper`. Only the `caddy` service is published beyond `RAG_BIND_ADDR`. The wizard
(`scripts/setup.py:apply_site_domain`) derives the public addresses, CORS lists, proxy trust and
`RAG_ENV=prod` from `RAG_SITE_DOMAIN`; its domain rule mirrors `core.install.is_dev_domain` (a test
keeps them equal). Backups are a folder per run with `manifest.json` and `SHA256SUMS`; the search index
is `python -m callobuzz_rag.vectordb.snapshots create|restore DIR` (Qdrant snapshots, checksum-verified);
Valkey is not backed up. Shell scripts are tested in throwaway `ubuntu:24.04` containers
(`tests/integration/test_deploy_scripts.py`, `test_caddy_config.py`, shellcheck through Docker, CI job
"Deploy scripts"); nothing in a test touches this computer's `/opt`, the cob-rag stack or the internet.
Shell files must stay LF (`.gitattributes`).

## Configuration and deployment

- All env vars prefixed `RAG_`, loaded by `pydantic-settings` in `core/config.py`. Secrets use
  `SecretStr`. A new setting goes in `core/config.py` **and** `.env.example`; `docker-compose.yml`
  does not mirror settings — app/worker/mailer/beat receive the whole `.env` via `env_file:`.
- **One `.env`, at the repo root.** Compose auto-loads it for interpolation; `_project_env_file()`
  in `core/config.py` finds it by walking up to `pyproject.toml`, so a native run reads it
  regardless of the working directory.
- Host-side addresses in `.env` are `127.0.0.1:<published port>` (never `localhost` — on Windows
  it resolves to `::1` and Docker Desktop resets the connection). Containers get service names
  from the compose `environment:` block, which beats `env_file:`; the `*_CONTAINER` variables
  repoint them.
- Datastore services keep `environment:` blocks because they **rename** variables
  (`RAG_DB_PASSWORD` → `POSTGRES_PASSWORD`) and carry the `${VAR:?}` guards.
- GPU is the `docker-compose.gpu.yml` overlay, never a default: Compose refuses to start a
  service reserving a device the host lacks.
- Optional services are profiles: `tei` (embeddings), `observability` (Langfuse),
  `transcription` (Whisper), `proxy` (Caddy, one public address; L4 install).

## Key Conventions

- **Errors:** all custom exceptions inherit `RAGKernelError` in `core/exceptions.py`.
- **HTTP errors with a machine code:** `APIError` / `error_response` in `api/errors.py`, body
  `{detail, code}` (`detail` stays a string; plain `HTTPException` still renders `{detail}`).
- **DB models:** SQLAlchemy 2.0 `Mapped[]` style in `db/models/`, all imported in
  `db/models/__init__.py` for Alembic autodiscovery. Migrations live in `alembic/versions/`.
- **Deferred imports:** heavy modules (Docling, Celery tasks, ML models) are imported inside
  functions.
- **Idempotent startup:** collection creation and config seeding are no-ops if they exist.
- **Factory pattern:** `create_app()` for FastAPI, `chunker_from_settings()` for the chunker,
  `_create_providers()` in the worker.

## Code Style

- Python 3.12+ features: `StrEnum`, `list[T]` type hints (no `from __future__`)
- Line length 100, double quotes, space indentation
- Ruff rules: E, W, F, I, N, UP, B, SIM, TC, RUF
- mypy strict mode with the `pydantic.mypy` plugin
- The ruff ignore list has specific reasons — TC003 (SQLAlchemy runtime annotations), F821
  (`Mapped[]` forward refs), B008 (FastAPI `Depends`)

## Ports (local)

API 8000, scraper 8001, dashboard 8090, PostgreSQL **5433**, Valkey (a Redis-compatible store, the `valkey` compose service with alias `redis`, `RAG_REDIS_*` names) **6380**, Qdrant 6333/6334,
TEI 8081, Langfuse **3002**, Whisper 9010, local model (Ollama,
`local-llm` profile) 11434. All bound to
`127.0.0.1` via `RAG_BIND_ADDR`. The `mailer` service (Celery on the `email` queue) publishes no
port.

## Architecture Enforcement Rules

### Auth & Multi-Tenancy
1. **Every non-public route MUST have an auth dependency** — `require_admin()`,
   `get_authenticated_corpus()`, `get_authenticated_tenant()`, `require_admin_or_tenant()`,
   `resolve_scope()`, or for a route acting on one corpus with any key kind `scoped_corpus()`
   (`?corpus_id=`) / `path_corpus()` (`{corpus_id}` in the path) (`api/deps.py`). Scoping
   lives in `authorized_corpus_ids()`: admin any, tenant its own corpora (else 403), corpus
   key only itself (else 403). Content Portal routes use `authorize_workspace` /
   `scoped_workspace` (`content_portal/routes/_access.py`) or `scoped_corpus` / `path_corpus`
   — never `get_authenticated_corpus`.
   **Session principals resolve to the same `is_admin`/`tenant` state as keys** (the
   middleware's Bearer branch) — never branch on `auth_kind` for data scoping.
   **Tenant status (`pending`/`active`/`suspended`) is enforced in `APIKeyAuthMiddleware` for
   every key kind and session, and in `_authorize_widget`; never add a second status check in a
   route.** Status changes go through `accounts/lifecycle.py` (transitions, typed `confirm_name`
   for suspend and delete, audit, mail after commit); deleting corpora goes through
   `api/services/corpus_deletion.py` (rows first, vectors and files after the commit).
   **Owner-only routes use `require_owner()`** (refuses member sessions only; a `tk_` key acts
   as an owner; `require_owner_for(request, field)` for a body field such as `llm_config`); the
   list is in `docs/internal/plans/epics/accounts/A5-teams.md` and in `OWNER_ONLY` in
   `tests/unit/scope_harness.py` — a new destructive or key-managing route must be added to both.
   Team rules (invitations, roles, last owner under row lock) live in `accounts/members.py`;
   invitation tokens travel only in a `#fragment` link and POST bodies, never a URL path/query.
2. **All data tables with corpus affinity MUST have a `corpus_id` FK** with `ondelete="CASCADE"`.
3. **Never expose plaintext API keys in responses** — shown only on creation, stored as bcrypt.
   **Passwords are argon2id** (`accounts/passwords.py`), API keys bcrypt — never mix them.
4. **LLM API keys encrypted at rest** — Fernet (MultiFernet, rotatable) via `core/secrets.py`;
   `python -m callobuzz_rag.core.reencrypt` rewrites stored tokens under the primary key.

### Code Patterns
5. **No `print()` in production code** — use `logging.getLogger(__name__)`.
6. **All custom exceptions inherit `RAGKernelError`.**
7. **Heavy imports deferred.**
8. **All env vars prefixed `RAG_`.**
9. **Secrets use `SecretStr`.**

### Database
10. **SQLAlchemy 2.0 `Mapped[]` style** — no legacy `Column()` declarations.
11. **All models in `db/models/__init__.py`.**
12. **Async everywhere** — `await session.execute()`, never sync DB calls.

### Business layer (BIZ1-BIZ5)
Modules: `website/` (reading and freshness), `onboarding/` (first run), `assets/` (avatars), `business/` (Home, and the value report, `business/value*.py`).
13. **A new configuration route is added to `api/features.py` `ROUTE_GATES`/`FIELD_GATES` or is data** (documents, leads, conversations, usage, queries); `tests/unit/test_features_module.py` checks the tables.
14. **Website fetches go through `website/netguard.py`; never a bare `httpx.AsyncClient`.** Every hop is checked for address and scope.
15. **COB's `website_status` reads the website routes with the caller's credential**; no tool reads the database.
16. **A new channel implements `channels/base.ChannelAdapter` and answers through the director with the human check first**, then the cap, billing and fair-use gates and a stable `request_id`; it never calls a provider outside its own `graph`-style client, never logs a token or message payload, and registers its plan flag in `accounts/plans.py` and its routes in `ROUTE_OPS`/`PUBLIC_ROUTES`.

## Key Documentation

Design notes, plans, epics and audits live in `docs/internal/`, which is **gitignored**: it exists
only on maintainers' machines and is not part of the public repository. Public, polished docs go
directly under `docs/`. Never commit anything from `docs/internal/`.

| Doc | Purpose |
|---|---|
| `docs/internal/architecture/rag-kernel-design.md` | Master architecture (layers, data model, tech choices) |
| `docs/internal/architecture/phase2-query-retrieval-design.md` | Query & retrieval design |
| `docs/internal/architecture/phase3-learning-observability-design.md` | Langfuse, RAGAS, evaluation, optimization |
| `docs/internal/architecture/phase4-multi-corpus-design.md` | Multi-corpus engine, auth, config resolution |
| `docs/internal/architecture/web-scraper-layer-design.md` | Web scraper service |
| `docs/internal/plans/2026-03-09-content-portal-layer-design.md` | Content Portal (AI structuring, chunk editor, review) |
| `docs/internal/plans/2026-03-09-phase7-conversational-agentic-design.md` | Conversations, memory, agentic RAG |
| `docs/internal/plans/2026-06-05-tenant-layer-prd.md` | Tenant layer design rationale |
| `docs/internal/plans/2026-03-09-competitive-feature-gaps.md` | Roadmap: gap analysis vs RAGFlow/R2R (GraphRAG, multimodal, connectors) |
| `docs/internal/plans/2026-09-30-accounts-signup-design.md` | Accounts: sign-up, sessions, teams, plans, branding, audit |
| `docs/internal/plans/epics/accounts/` | Accounts epics A0–A8, their audit and implementation notes |
| `docs/internal/plans/epics/` | Per-epic specs, numbered by phase |
| `docs/internal/guides/STARTUP_GUIDE.md` | Step-by-step first run |
| `docs/index.html` (public) | Landing page for GitHub Pages; diagrams in `docs/assets/` |
| `benchmarks/README.md` (public) | Retrieval benchmark method and results |
| `docs/internal/diagrams/` | Mermaid diagrams — pipeline, provider pattern, data model |

## Status

Phases 1–7 are complete in code: core ingestion, query & retrieval, learning & observability,
multi-corpus engine, tenant config & model marketplace, re-ingestion & versioning, the Content
Portal, conversational & agentic RAG, the tenant layer, embedding migration, the website chat
widget, usage metering, client SDKs and the retrieval benchmark. The accounts layer (A0–A8) is
complete too: scoped Content Portal, tenant-required corpora, e-mail, users and sessions, sign-up
and tenant status, teams, plans and caps, settings and branding, the audit log and its read API.
Next: the new dashboard (`services/dashboard/`), then the competitive-gaps roadmap above.

## Dashboard UI

`services/chat/index.html` — standalone SPA served by the `dashboard` compose service on :8090,
configured by `services/chat/config.js`. `widget-demo.html` is the widget preview site. Tabs:
**Admin** (tenants, corpus CRUD with tenant + LLM provider, key rotation, Website Widget panel,
Usage),
**Upload** (drag-and-drop ingestion), **Scraper** (scrape jobs with review mode), **Chat** (test
queries with cost tracking).

The new dashboard lives in `services/dashboard/` (React, pnpm). `src/features/assistant/` is the
Knowledge base, Assistant tab and its preview console: one zod-validated form per configuration
section saving through `useDraftSection` (whole config + `base_revision` to `PUT …/assistant/draft`),
the preview chat reusing the playground's stream with `assistant` options.
