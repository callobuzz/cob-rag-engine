export type Json = Record<string, any>;

export interface LlmOverride {
  provider?: string;
  model?: string;
  apiKey?: string;
  baseUrl?: string;
}

/** The conversation layer's options for one question (they act only when the assistant is on). */
export interface AssistantOptions {
  /** Continue a stored conversation of this knowledge base. */
  conversationId?: string;
  pageUrl?: string;
  /** Answer with the current draft. Needs an API key with write permission. */
  useDraft?: boolean;
  /** Include the turn's trace. Owner only; needs write permission. */
  debug?: boolean;
  /** Pretend office hours, page or language. Only with `useDraft` or `debug`. */
  simulate?: { officeHours?: "open" | "closed"; pageUrl?: string; language?: string };
}

export interface QueryOptions {
  corpusId?: string;
  systemPrompt?: string;
  options?: Json;
  llm?: LlmOverride;
  assistant?: AssistantOptions;
}

export type QueryStreamEvent =
  | { event: "stage"; data: { stage: string; elapsed_ms: number } }
  | { event: "meta"; data: { query_id: string; conversation_id: string | null; session: string | null; label: string; route: string; language: string; script: string; assistant_version: number | null; label_source?: string } }
  | { event: "sources"; data: { query_id: string; sources: Json[] } }
  | { event: "token"; data: { content: string } }
  | { event: "quick_replies"; data: { items: { label: string; message: string }[] } }
  | { event: "ui"; data: Json }
  | { event: "trace"; data: Json }
  | { event: "done"; data: {
      query_id: string; model: string | null; latency_ms: number;
      usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number; reasoning_tokens?: number; cost_usd?: number | null; cost_known?: boolean; model_calls?: number;
        /** The thinking the answer model ran at: off, minimal, low, medium, high, on or provider-default. */
        thinking_effective?: string };
      /** Milliseconds per stage: understand, search, rerank, check, write. */
      timings?: Record<string, number>;
      stage_timings_ms?: Record<string, number>;
      retrieval_ms?: number | null;
      processed_query?: string | null;
      thinking_ms?: number;
      reasoning_tokens?: number;
      /** Assistant on: */
      label?: string; route?: string; language?: string; cta?: Json | null; claims_removed?: number;
      /** The search-confidence threshold the turn was gated on. */
      gate_threshold?: number;
    } }
  | { event: "error"; data: { detail: string; code?: string } };

/** read: search, ask, list, get. write: read plus add and change content. full: write plus delete and key management. */
export type KeyPermission = "read" | "write" | "full";

export class CobRagError extends Error {
  status: number;
  detail: unknown;
  /** The engine's machine-readable error code ("key_permission_denied", "confirmation_required", ...). */
  code?: string;
}

export class CobRag {
  constructor(opts?: { baseUrl?: string; apiKey?: string; fetch?: typeof fetch });
  health(): Promise<Json>;
  /** Readiness: resolves when Postgres, Redis and Qdrant answer; rejects on 503. */
  healthReady(): Promise<Json>;
  /** Key kind, tenant/corpus, and `auth` / `role` / `user` (user: sign-ins only). */
  whoami(): Promise<Json>;
  info(): Promise<Json>;
  fastModelBenchmarks(): Promise<Json>;
  answerModelBenchmarks(): Promise<Json>;
  benchmarkSummary(): Promise<Json>;
  ownTenant(): Promise<Json>;
  rotateOwnTenantKey(): Promise<Json>;
  createTenant(name: string, fields?: Json): Promise<Json>;
  listTenants(opts?: {
    status?: "pending" | "active" | "suspended";
    search?: string;
    /** false: customers only (without the operator's own workspace). */
    isOperator?: boolean;
    offset?: number;
    limit?: number;
  }): Promise<Json[]>;
  /** Activate a pending sign-up, on `planId` (default: the plan it has). A string is the note. */
  activateTenant(id: string, opts?: { planId?: string; note?: string } | string): Promise<Json>;
  /** Every plan, the default first, each with its `tenant_count`. */
  listPlans(opts?: { includeInactive?: boolean }): Promise<Json[]>;
  /** Limits omitted or null are unlimited. */
  createPlan(name: string, fields?: Json): Promise<Json>;
  /** One plan, with its `tenant_count`. */
  getPlan(planId: string): Promise<Json>;
  /** Only the fields given change; a limit given as null becomes unlimited. */
  updatePlan(planId: string, fields: Json): Promise<Json>;
  /** Refused (409) while tenants use it or while it is the default. */
  deletePlan(planId: string): Promise<null>;
  setTenantPlan(tenantId: string, planId: string): Promise<Json>;
  /** Admin: a tenant's plan, effective limits and usage this month. */
  tenantPlan(tenantId: string): Promise<Json>;
  /** This tenant's plan, limits and usage: `{plan, limits, usage, resets_at}`. */
  myPlan(): Promise<Json>;

  /** Every pipeline profile, the default first: `{items, default_id}`. */
  listPipelineProfiles(): Promise<Json>;
  /** A new profile. 422 `invalid_profile` names the rule when the settings do not fit. */
  createPipelineProfile(name: string, label: string, options?: { description?: string; config?: Json }): Promise<Json>;
  getPipelineProfile(profileId: string): Promise<Json>;
  /** Only the fields given change; `config` replaces the whole config. */
  updatePipelineProfile(profileId: string, fields: Json): Promise<Json>;
  /** Refused (409) while a plan or business uses it, or while it is the default. */
  deletePipelineProfile(profileId: string): Promise<null>;
  /** Which profile a business answers with, and where it comes from. */
  getTenantPipeline(tenantId: string): Promise<Json>;
  /** Give a business a profile of its own; null goes back to its plan's. */
  setTenantPipeline(tenantId: string, profileId: string | null): Promise<Json>;
  /** Businesses with their fair-use state (`ok`, `near`, `throttled`, `stopped`): `{items, total}`. */
  listFairUse(options?: { q?: string; state?: string; offset?: number; limit?: number }): Promise<Json>;
  /** One business's effective limits and policy, how much of each is used, its state. */
  getFairUse(tenantId: string): Promise<Json>;
  /** Give a business limits or a policy of its own (a limit of `null` is unlimited); `clear` goes back to the plan. */
  setFairUse(tenantId: string, options?: { limits?: Json; policy?: Json; clear?: string[] }): Promise<Json>;
  /** This workspace's own read-only view (workspace sign-in or tenant key); AI usage as a share only. */
  fairUse(): Promise<Json>;
  /** Leads the key may see, newest first. `q` finds a phone number or e-mail exactly. */
  listLeads(opts?: { corpusId?: string; status?: string; trigger?: string; dateFrom?: string; dateTo?: string; q?: string; pageUrl?: string; offset?: number; limit?: number }): Promise<Json>;
  /** One lead with its summary, notes, consent record and chat transcript. */
  getLead(id: string): Promise<Json>;
  updateLead(id: string, fields: { status?: string; assignee_user_id?: string | null; notes?: string | null; qualification?: string | null }): Promise<Json>;
  /** Handoffs the key may see, newest first. */
  listHandoffs(opts?: { corpusId?: string; status?: "new" | "handled"; reason?: string; channel?: string; from?: string; to?: string; offset?: number; limit?: number }): Promise<Json>;
  /** One handoff with its summary, the masked chat and the lead of a callback request. */
  getHandoff(id: string): Promise<Json>;
  /** Mark a handoff "handled" (or "new" again). */
  updateHandoff(id: string, status: "new" | "handled"): Promise<Json>;
  /** Find the chat a visitor quoted a reference for ("SD-4F7K", any case). */
  lookupReference(ref: string): Promise<Json>; // TV2 reference
  /** The knowledge base's WhatsApp number: status, settings, who pays Meta (never a token). */
  whatsapp(corpusId: string): Promise<Json>; // TV6
  /** Connect a number by hand (admin key): ids, a permanent token, and who pays Meta. */
  setWhatsappManual(corpusId: string, body: { phoneNumberId: string; wabaId: string; accessToken: string; metaBilling: "business" | "deployer" }): Promise<Json>;
  /** `enabled`, `human_pause_hours` (1 to 72) and `follow_up`; only the fields given change. */
  updateWhatsapp(corpusId: string, fields: { enabled?: boolean; human_pause_hours?: number; follow_up?: { enabled?: boolean; template_name?: string; language?: string; after_hours?: number } }): Promise<Json>;
  /** The documents customers may be sent on WhatsApp (at most 20 PDFs, JPGs or PNGs). */
  setWhatsappDocuments(corpusId: string, documentIds: string[]): Promise<Json>;
  /** Disconnect the number: its token is deleted, the chats stay readable. */
  disconnectWhatsapp(corpusId: string): Promise<null>;
  /** The WhatsApp chats, most recent customer message first (names masked). */
  whatsappThreads(corpusId: string, opts?: { unread?: boolean; offset?: number; limit?: number }): Promise<Json>;
  /** One chat: the number in full and the last 100 messages. Opening it marks it read. */
  whatsappThread(corpusId: string, threadId: string): Promise<Json>;
  /** Answer a customer (text, up to 4,096 characters, within 24 hours of their last message). */
  whatsappReply(corpusId: string, threadId: string, text: string): Promise<Json>;
  /** Keep the assistant quiet in one chat (true), or let it answer again (false). */
  updateWhatsappThread(corpusId: string, threadId: string, body: { botPaused: boolean }): Promise<Json>;
  /** Erase a lead and the website chat it came from (owners only). */
  deleteLead(id: string): Promise<null>;
  /** The matching leads as CSV text (owners only; the export is audited). */
  exportLeads(opts?: { corpusId?: string; [filter: string]: string | undefined }): Promise<string>;
  listCorpusWebhooks(corpusId: string): Promise<Json[]>;
  /** `events`: "lead.created" and/or "handoff.requested". https and a public host only. */
  createCorpusWebhook(corpusId: string, body: { url: string; events: string[]; secret: string }): Promise<Json>;
  updateCorpusWebhook(corpusId: string, webhookId: string, body: { enabled: boolean }): Promise<Json>;
  deleteCorpusWebhook(corpusId: string, webhookId: string): Promise<null>;
  /** What the public withdrawal page does with the token from its link (no key needed). */
  withdrawLeadConsent(token: string): Promise<Json>;
  /** The published assistant and the draft being edited (either may be null). */
  getAssistant(corpusId: string): Promise<Json>;
  /** Replace the draft; `baseRevision` is the draft_revision you edited. */
  putAssistantDraft(corpusId: string, config: Json, opts?: { baseRevision?: number }): Promise<Json>;
  /** Merge a JSON merge patch (RFC 7396) into the draft. */
  patchAssistantDraft(corpusId: string, patch: Json, opts?: { baseRevision?: number }): Promise<Json>;
  assistantDraftFromTemplate(corpusId: string, template: string, opts?: { keepIdentity?: boolean; baseRevision?: number }): Promise<Json>;
  discardAssistantDraft(corpusId: string): Promise<null>;
  /** Publish the draft as the next version (owner keys). */
  publishAssistant(corpusId: string, opts: { baseRevision: number; note?: string }): Promise<Json>;
  assistantVersions(corpusId: string, opts?: { offset?: number; limit?: number }): Promise<Json>;
  assistantVersion(corpusId: string, version: number): Promise<Json>;
  /** Publish an older version again as a new version. */
  restoreAssistantVersion(corpusId: string, version: number): Promise<Json>;
  setAssistantEnabled(corpusId: string, enabled: boolean): Promise<Json>;
  /** Outcome analytics for one knowledge base, or for all the key can see (null). */
  assistantAnalytics(corpusId: string | null, opts?: { from?: string; to?: string }): Promise<Json>;
  /** The prompt the model would receive for a sample turn (no model call). */
  compileAssistant(corpusId: string, opts?: { config?: Json; sample?: Json }): Promise<Json>;
  improveAssistantRule(
    corpusId: string,
    opts: { instruction: string; type: string; ruleId?: string },
  ): Promise<Json>;
  assistantTemplates(): Promise<Json[]>;
  assistantTemplate(key: string): Promise<Json>;
  /** Start a guided setup for a knowledge base (older unfinished ones are abandoned). */
  startSetup(corpusId: string, opts?: { businessType?: string; websiteUrl?: string }): Promise<Json>;
  /** One setup session: status, proposal, confirmed profile, answers, provenance. */
  getSetup(corpusId: string, sessionId: string): Promise<Json>;
  /** Send crawled pages; returns the proposal. */
  proposeSetup(corpusId: string, sessionId: string, pages: { url: string; title?: string; text: string }[], opts?: { crawlJobId?: string }): Promise<Json>;
  /** The interview questions (prefilled) and the business profile to confirm. */
  setupQuestions(corpusId: string, sessionId: string): Promise<Json>;
  /** Save the answers and the confirmed profile. */
  answerSetup(corpusId: string, sessionId: string, answers: Json, opts?: { businessType?: string; profile?: Json; partial?: boolean }): Promise<Json>;
  /** Write the draft assistant; wording "template" makes no model call. */
  generateSetup(corpusId: string, sessionId: string, opts?: { wording?: "ai" | "template"; baseRevision?: number }): Promise<Json>;
  /** Create the setup's test set and start its run. */
  runSetupTests(corpusId: string, sessionId: string): Promise<Json>;
  /** Questions the documents could not answer. */
  setupGaps(corpusId: string, sessionId: string): Promise<Json>;
  /** Publish the generated draft. */
  applySetup(corpusId: string, sessionId: string, baseRevision: number, opts?: { note?: string }): Promise<Json>;
  /** The knowledge base's test sets, each with its question count and last run. */
  listGoldenSets(corpusId: string): Promise<Json[]>;
  /** Each pair has `query` and an expected_answer and/or an expected_behaviour. */
  createGoldenSet(corpusId: string, name: string, pairs?: Json[]): Promise<Json>;
  goldenSetFromTemplate(corpusId: string, template?: string | null): Promise<Json>;
  getGoldenSet(corpusId: string, setId: string): Promise<Json>;
  renameGoldenSet(corpusId: string, setId: string, name: string): Promise<Json>;
  /** Needs a `full` key; the default set cannot be deleted. */
  deleteGoldenSet(corpusId: string, setId: string): Promise<null>;
  addGoldenPairs(corpusId: string, setId: string, pairs: Json[]): Promise<Json[]>;
  listEvalCriteria(corpusId: string): Promise<Json[]>;
  createEvalCriterion(corpusId: string, name: string, definition: string, threshold?: number): Promise<Json>;
  updateEvalCriterion(corpusId: string, criterionId: string, fields: { definition?: string; threshold?: number; enabled?: boolean }): Promise<Json>;
  deleteEvalCriterion(corpusId: string, criterionId: string): Promise<null>;
  getEvalSettings(corpusId: string): Promise<Json>;
  /** The judge key is stored encrypted and never returned. */
  setEvalSettings(corpusId: string, fields: { judge_provider?: string; judge_model?: string; judge_api_key?: string; judge_base_url?: string; thresholds?: Record<string, number> }): Promise<Json>;
  estimateEvalRun(corpusId: string, setId: string, opts?: { mode?: "fast" | "full"; settings?: Json }): Promise<Json>;
  /** A full run needs `confirmCostUsd` at least the estimate's total_usd. */
  startEvalRun(corpusId: string, setId: string, opts?: { mode?: "fast" | "full"; name?: string; settings?: Json; confirmCostUsd?: number }): Promise<Json>;
  listEvalRuns(corpusId: string, opts?: { setId?: string; kind?: "traffic" | "golden" }): Promise<Json[]>;
  getEvalRun(corpusId: string, runId: string): Promise<Json>;
  listEvalSamples(corpusId: string, runId: string, opts?: { failedOnly?: boolean; metric?: string; below?: number; tag?: string; offset?: number; limit?: number }): Promise<Json>;
  rateEvalSample(corpusId: string, sampleId: string, rating: "good" | "acceptable" | "poor" | null): Promise<Json>;
  /** Two to four completed runs of one set; the first is the baseline. */
  compareEvalRuns(corpusId: string, runIds: string[]): Promise<Json>;
  reviewItems(corpusId: string, opts?: { kind?: "no_answer" | "low_confidence" | "thumbs_down" | "handoff"; since?: string; until?: string }): Promise<Json[]>;
  reviewAction(corpusId: string, itemKey: string, action: "dismissed" | "added_document" | "added_answer" | "added_rule", payload?: Json): Promise<Json>;
  /** `confirmName` must be the tenant's name exactly. */
  suspendTenant(id: string, confirmName: string, reason?: string): Promise<Json>;
  reactivateTenant(id: string): Promise<Json>;
  /** Admin: a new tk_ key for the tenant, returned once; the old one stops now. */
  rotateTenantKey(id: string): Promise<Json>;
  /** Admin: what deleting the customer would remove (counts) and the `confirm_name` to send. */
  tenantDeletePreview(id: string): Promise<Json>;
  declineTenant(id: string, reason?: string): Promise<null>;
  /** Deletes the tenant and everything it owns; `confirmName` must be its name exactly. */
  deleteTenant(id: string, confirmName: string, opts?: { confirmOperatorWorkspace?: boolean }): Promise<null>;
  /** This tenant's members and open invitations (tenant key). */
  team(): Promise<Json>;
  /** Invite by e-mail; the link is e-mailed, never returned. */
  invite(email: string, role?: "owner" | "member"): Promise<Json>;
  revokeInvitation(id: string): Promise<null>;
  resendInvitation(id: string): Promise<Json>;
  setMemberRole(userId: string, role: "owner" | "member"): Promise<Json>;
  removeMember(userId: string): Promise<null>;
  /** Admin: a tenant's members and open invitations. */
  tenantTeam(tenantId: string): Promise<Json>;
  /** Admin: the response carries `accept_url` (send it yourself without SMTP). */
  inviteToTenant(tenantId: string, email: string, role?: "owner" | "member"): Promise<Json>;
  /** Admin: revoke an open invitation of a tenant. */
  revokeTenantInvitation(tenantId: string, invitationId: string): Promise<null>;
  /** Admin: a new link (in `accept_url`) and 7 more days; the old link stops working. */
  resendTenantInvitation(tenantId: string, invitationId: string): Promise<Json>;
  /** Admin: change someone's role in a tenant (409 last_owner for the last owner). */
  setTenantMemberRole(tenantId: string, userId: string, role: "owner" | "member"): Promise<Json>;
  /** Admin: remove someone from a tenant; their account stays. */
  removeTenantMember(tenantId: string, userId: string): Promise<null>;
  /** Admin: recreate the operator's own workspace after it was deleted on purpose. */
  restoreOperatorWorkspace(): Promise<Json>;
  /** Admin: audit rows, newest first. `action`: exact, or a prefix ending in "." ("tenant."). */
  auditLog(opts?: {
    tenantId?: string;
    action?: string;
    actor?: string;
    start?: string;
    end?: string;
    offset?: number;
    limit?: number;
  }): Promise<AuditPage>;
  /** `fields.tenant_id` is required with the admin key; a tenant key may omit it. */
  createCorpus(name: string, fields?: Json): Promise<Json>;
  listCorpora(opts?: { offset?: number; limit?: number; tenantId?: string }): Promise<Json[]>;
  getCorpus(id: string): Promise<Json>;
  updateCorpus(id: string, fields: Json): Promise<Json>;
  /** With an API key, `confirmName` must be the knowledge base's exact name. */
  deleteCorpus(id: string, confirmName?: string): Promise<null>;
  /** What deleting the knowledge base would remove (counts) and the `confirm_name` to send. */
  corpusDeletePreview(id: string): Promise<Json>;
  /** Replaces the knowledge base's default key; other named keys are untouched. */
  rotateCorpusKey(id: string): Promise<Json>;
  /** The knowledge base's keys: name, prefix, permission, last used. Never a key. */
  listCorpusKeys(corpusId: string): Promise<Json[]>;
  /** A named key; `api_key` in the response is shown once. */
  createCorpusKey(corpusId: string, name: string, permission?: KeyPermission): Promise<Json>;
  updateCorpusKey(corpusId: string, keyId: string, fields: { name?: string; permission?: KeyPermission }): Promise<Json>;
  /** A new secret for one key (returned once); the old one stops now. */
  rotateCorpusNamedKey(corpusId: string, keyId: string): Promise<Json>;
  revokeCorpusKey(corpusId: string, keyId: string): Promise<null>;
  /** This customer's keys (a "full" tenant key, or an owner's sign-in). */
  listMyKeys(): Promise<Json[]>;
  createMyKey(name: string, permission?: KeyPermission): Promise<Json>;
  updateMyKey(keyId: string, fields: { name?: string; permission?: KeyPermission }): Promise<Json>;
  rotateMyNamedKey(keyId: string): Promise<Json>;
  revokeMyKey(keyId: string): Promise<null>;
  /** Admin: a customer's keys. */
  listTenantKeys(tenantId: string): Promise<Json[]>;
  createTenantKey(tenantId: string, name: string, permission?: KeyPermission): Promise<Json>;
  updateTenantKey(tenantId: string, keyId: string, fields: { name?: string; permission?: KeyPermission }): Promise<Json>;
  rotateTenantNamedKey(tenantId: string, keyId: string): Promise<Json>;
  revokeTenantKey(tenantId: string, keyId: string): Promise<null>;
  ingest(
    file: Blob | { name: string; data: Uint8Array | ArrayBuffer | string },
    opts?: { corpusId?: string; metadata?: Json; mode?: "auto" | "new" | "replace" | "version"; autoConfirm?: boolean; wait?: boolean; pollMs?: number; timeoutMs?: number },
  ): Promise<Json>;
  ingestRecords(
    records: { id: string; text: string; title?: string; url?: string; metadata?: Record<string, string> }[],
    opts?: { corpusId?: string; wait?: boolean; pollMs?: number; timeoutMs?: number },
  ): Promise<Json>;
  getJob(jobId: string): Promise<Json>;
  listJobs(params?: { status?: string; corpus_id?: string; offset?: number; limit?: number }): Promise<Json>;
  retryIngestJob(jobId: string): Promise<Json>;
  listDocuments(params?: {
    corpus_id?: string;
    search?: string;
    source?: "website" | "articles" | "files"; // BIZ3
    offset?: number;
    limit?: number;
  }): Promise<Json>;
  getDocument(
    id: string,
    params?: { corpus_id?: string; version_id?: string; chunk_offset?: number; chunk_limit?: number },
  ): Promise<Json>;
  /** With an API key, `confirmName` must be the document's file name (or record id) exactly. */
  deleteDocument(id: string, confirmName?: string, params?: { corpus_id?: string }): Promise<null>;
  /** What deleting the document would remove, and the `confirm_name` to send. */
  documentDeletePreview(id: string, params?: { corpus_id?: string }): Promise<Json>;
  waitForJob(jobId: string, opts?: { pollMs?: number; timeoutMs?: number }): Promise<Json>;
  query(query: string, opts?: QueryOptions): Promise<{ answer: string; citations: Json[] } & Json>;
  queryStream(query: string, opts?: QueryOptions): AsyncGenerator<string>;
  queryStreamEvents(query: string, opts?: QueryOptions): AsyncGenerator<QueryStreamEvent>;
  retrieve(query: string, opts?: QueryOptions): Promise<Json>;
  createWidgetKey(corpusId: string, name: string, allowedOrigins: string[], fields?: Json): Promise<Json>;
  listWidgetKeys(corpusId: string): Promise<Json[]>;
  updateWidgetKey(corpusId: string, keyId: string, fields: Json): Promise<Json>;
  rotateWidgetKey(corpusId: string, keyId: string): Promise<Json>;
  deleteWidgetKey(corpusId: string, keyId: string): Promise<null>;
  usage(params?: { corpus_id?: string; tenant_id?: string; start?: string; end?: string; group_by?: "day" | "corpus" | "source" | "tenant" }): Promise<Json>;
  /** The dashboard header summary (tenant key or workspace session; not a ck_ key). */
  usageConsumption(): Promise<Json>;
  /** Admin: platform cost report. */
  costs(params?: { start?: string; end?: string }): Promise<Json>;
  /** Admin: what OpenRouter, OpenAI, Anthropic and any manual provider have left ({items, worst}; USD micro-units). */
  providerBalances(): Promise<Json>; // BIL1
  /** Admin: how customers on a plan pay (`version: null` = no pricing). Money is integer minor units. */
  getPlanPricing(planId: string): Promise<Json>; // BIL2
  /** Admin: replace a plan's pricing with a new version; the response lists what `changed`. */
  setPlanPricing(planId: string, fields?: Json): Promise<Json>; // BIL2
  /** Admin: every customer's billing, `{items, total}`. */
  listBillingAccounts(opts?: { q?: string; paymentType?: "prepaid" | "postpaid"; offset?: number; limit?: number }): Promise<Json>; // BIL2
  /** Admin: one customer's billing account (created on first read). */
  getBillingAccount(tenantId: string): Promise<Json>; // BIL2
  /** Admin: change a customer's terms (`overrides`, `limit_overrides`, `currency`, `pause_override`, `billing_day`, `notes`). */
  updateBillingAccount(tenantId: string, fields: Json): Promise<Json>; // BIL2
  /** The caller's own workspace: how it pays (money fields are null for a member). */
  billingAccount(): Promise<Json>; // BIL2
  /** Admin: a customer's transactions, newest first (`{items, total, balance_minor, available_minor}`). */
  listLedger(tenantId: string, opts?: { entryType?: string; start?: string; end?: string; offset?: number; limit?: number }): Promise<Json>; // BIL3
  /** Admin: record a top-up, an adjustment (signed) or a refund; the same `idempotency_key` again changes nothing. */
  addLedgerEntry(tenantId: string, fields: Json): Promise<Json>; // BIL3
  /** Owner: this workspace's transactions (`{items, total}`). */
  billingLedger(opts?: { offset?: number; limit?: number }): Promise<Json>; // BIL3
  // P1 payments
  /** Admin: payments received, newest first (`{items, total}`), with each payment's fee. */
  listPayments(opts?: { tenantId?: string; status?: string; start?: string; end?: string; offset?: number; limit?: number }): Promise<Json>;
  /** Admin: one payment with its refunds, the deliveries about it and `refundable_minor`. */
  getPayment(paymentId: string): Promise<Json>;
  /** Admin: refund part of a payment from the workspace's unused balance (`amount_minor`, `memo`, `idempotency_key`, `confirm_amount_minor`). */
  refundPayment(paymentId: string, fields: Json): Promise<Json>;
  /** Admin: a link a customer opens to pay (`purpose`, `amount_minor`, `add_on_id`, `send_email`). */
  createPaymentLink(tenantId: string, fields: Json): Promise<Json>;
  /** Owner: who this workspace's tax invoices are made out to. */
  taxDetails(): Promise<Json>;
  /** Owner: save the billing name, address and state. */
  setTaxDetails(fields: Json): Promise<Json>;
  /** Owner: this workspace's payments (`{items, total}`), without fees. */
  billingPayments(opts?: { offset?: number; limit?: number }): Promise<Json>;
  // /P1 payments
  // P2 invoices
  /** Owner: this workspace's tax invoices and credit notes (`{items, total}`), newest first. */
  billingInvoices(opts?: { offset?: number; limit?: number }): Promise<Json>;
  /** Admin: every tax document (`{items, total, totals}`). */
  listInvoices(opts?: { tenantId?: string; docType?: "invoice" | "bill_of_supply" | "credit_note"; fy?: string; start?: string; end?: string; offset?: number; limit?: number }): Promise<Json>;
  // /P2 invoices
  /** Admin: the add-on catalogue (`{items}`). */
  listAddOns(opts?: { includeInactive?: boolean }): Promise<Json>; // BIL5
  /** Admin: add one to the catalogue (`kind`, `name`, `currency`, `price_minor` and the fields of the kind). */
  createAddOn(fields: Json): Promise<Json>; // BIL5
  /** Admin: change an add-on; existing grants keep what they were given. */
  updateAddOn(addOnId: string, fields: Json): Promise<Json>; // BIL5
  /** Admin: delete an add-on never given to anyone (else 409 `add_on_in_use`). */
  deleteAddOn(addOnId: string): Promise<Json>; // BIL5
  /** Admin: the add-ons given to a customer (`{items}`). */
  listGrants(tenantId: string, opts?: { activeOnly?: boolean }): Promise<Json>; // BIL5
  /** Admin: apply an add-on; the same `idempotency_key` again returns the same grant. */
  applyAddOn(tenantId: string, fields: Json): Promise<Json>; // BIL5
  /** Admin: end an extra limit or question pack now. */
  revokeGrant(tenantId: string, grantId: string, opts?: { memo?: string }): Promise<Json>; // BIL5
  /** Admin: a customer's monthly statements, newest first (`{items}`; the current month is `live`). */
  listStatements(tenantId: string): Promise<Json>; // BIL6
  /** Admin: one month (`YYYY-MM`) of a customer's statement: an object, or text for `format: "csv" | "html"`. */
  getStatement(tenantId: string, month: string, opts?: { format?: "json" }): Promise<Json>; // BIL6
  getStatement(tenantId: string, month: string, opts: { format: "csv" | "html" }): Promise<string>; // BIL6
  /** Owner: this workspace's monthly statements (`{items}`). */
  billingStatements(): Promise<Json>; // BIL6
  /** Owner: one month (`YYYY-MM`) of this workspace's statement: an object, or text for `format: "csv" | "html"`. */
  billingStatement(month: string, opts?: { format?: "json" }): Promise<Json>; // BIL6
  billingStatement(month: string, opts: { format: "csv" | "html" }): Promise<string>; // BIL6
  /** Owner: ask the operator to add money (`amountMinor` in paise or cents, `note`); one request per workspace per hour. */
  requestTopUp(opts?: { amountMinor?: number; note?: string }): Promise<{ sent: boolean }>; // BIL6
  // TV3 no surprises
  /** Owner: the monthly cap on usage charges in paise or cents; `null` removes it. */
  setSpendCap(capMinor: number | null): Promise<Json>;
  /** Admin: what saving these plan prices would do, without saving. */
  previewPricing(planId: string, fields?: Json): Promise<Json>;
  /** Admin: call off an announced price change that has not started. */
  cancelScheduledPricing(planId: string): Promise<Json>;
  /** Admin: call off a raise for one business that is waiting out its notice period. */
  cancelPendingChange(tenantId: string): Promise<Json>;
  // /TV3 no surprises
  // TV4 value report
  /** The values behind the value report (or suggestions until the owner saves their own). */
  valueSettings(): Promise<Json>;
  /** Owner: save the four values (paise or cents; minutes 0 to 120) and the e-mail switch. */
  updateValueSettings(values: {
    leadValueMinor: number;
    handoffValueMinor: number;
    staffHourMinor: number;
    minutesPerAnswer: number;
    emailEnabled?: boolean;
    industryDefaults?: string;
  }): Promise<Json>;
  /** A month's numbers with the estimated value and its formula; text for `format: "html"` (the printable page). */
  valueReport(month?: string, opts?: { format?: "json" }): Promise<Json>;
  valueReport(month: string | undefined, opts: { format: "html" }): Promise<string>;
  /** The frozen months, newest first. */
  valueReports(): Promise<Json>;
  /** Admin: one business's value report, as its owner sees it. */
  adminValueReport(tenantId: string, month?: string, opts?: { format?: "json" }): Promise<Json>;
  adminValueReport(tenantId: string, month: string | undefined, opts: { format: "html" }): Promise<string>;
  // /TV4 value report
  // BIZ3 home and articles
  home(params?: { corpus_id?: string }): Promise<Json>;
  createArticle(corpusId: string, article: { kind: "article" | "faq"; title: string; body: string }): Promise<Json>;
  getArticle(corpusId: string, documentId: string): Promise<Json>;
  updateArticle(
    corpusId: string,
    documentId: string,
    article: { kind: "article" | "faq"; title: string; body: string },
  ): Promise<Json>;
  // /BIZ3 home and articles
  // BIZ1 website learning
  website(corpusId: string): Promise<Json>;
  updateWebsite(
    corpusId: string,
    changes?: {
      enabled?: boolean;
      exclude_patterns?: string[];
      refresh_mode?: "off" | "monthly" | "weekly" | "daily" | "6h" | "hourly"; // BIZ2
      adaptive?: boolean; // BIZ2
    },
  ): Promise<Json>;
  learnWebsite(corpusId: string, origin: string): Promise<Json>;
  rereadWebsite(corpusId: string): Promise<{ queued: number }>;
  removeWebsiteSource(corpusId: string, sourceId: string): Promise<null>;
  websitePages(
    corpusId: string,
    params?: {
      status?: "learned" | "waiting" | "skipped" | "excluded" | "failed" | "removed";
      source_id?: string;
      q?: string;
      offset?: number;
      limit?: number;
    },
  ): Promise<Json>;
  rereadWebsitePage(corpusId: string, pageId: string): Promise<Json>;
  updateWebsitePage(corpusId: string, pageId: string, excluded: boolean): Promise<Json>;
  // /BIZ1 website learning
  // BIZ2 freshness
  createLearnKey(corpusId: string): Promise<{ key: string; push_url: string; hint: string }>;
  deleteLearnKey(corpusId: string): Promise<null>;
  // /BIZ2 freshness
  // BIZ4 avatar
  uploadAvatar(corpusId: string, file: Blob | { name?: string; data: Uint8Array | ArrayBuffer | string }): Promise<{ avatar_url: string }>;
  // /BIZ4 avatar
  getSettings(): Promise<EngineSettings>;
  updateSettings(changes: EngineSettingsPatch): Promise<EngineSettings>;
  publicBranding(): Promise<PublicBranding>;
  smtpStatus(): Promise<SmtpStatus>;
  sendTestEmail(to: string): Promise<SmtpTestResult>;
}

export interface AuditEntry {
  id: string;
  created_at: string;
  actor_kind: "admin_key" | "tenant_key" | "corpus_key" | "user" | "system";
  actor_user_id: string | null;
  actor_label: string;
  action: string;
  tenant_id: string | null;
  tenant_label: string | null;
  target_type: string | null;
  target_id: string | null;
  details: Json;
  ip: string | null;
}

export interface AuditPage {
  items: AuditEntry[];
  total: number;
  offset: number;
  limit: number;
}

export interface SmtpStatus {
  provider: "smtp" | "log" | "disabled";
  configured: boolean;
  host: string | null;
  port: number;
  security: "starttls" | "ssl" | "none";
  from_address: string | null;
  username_set: boolean;
  password_set: boolean;
  dashboard_url: string;
}

export interface SmtpTestResult {
  sent: boolean;
  provider: "smtp" | "log";
  to: string;
}

export type SignupMode = "open" | "approval" | "closed";

export interface Branding {
  product_name: string;
  logo_url: string | null;
  primary_color: string;
  support_email: string | null;
  contact_text: string;
  contact_url: string | null;
}

export interface EngineSettings {
  signup_mode: SignupMode;
  branding: Branding;
  updated_at: string | null;
  updated_by: string | null;
}

/** Only the fields sent change; null resets a field (or the whole branding) to its default. */
export interface EngineSettingsPatch {
  signup_mode?: SignupMode | null;
  branding?: { [K in keyof Branding]?: Branding[K] | null } | null;
}

export interface PublicBranding extends Branding {
  signup_mode: SignupMode;
  /** Constant; not a setting. */
  powered_by: { text: string; url: string | null };
}
