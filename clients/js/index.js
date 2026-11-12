/**
 * JavaScript / TypeScript client for cob-rag-engine. Zero dependencies — uses
 * the global fetch (Node 18+, Deno, Bun, browsers).
 *
 *   import { CobRag } from "cob-rag-client";
 *   const rag = new CobRag({ baseUrl: "http://127.0.0.1:8000", apiKey: "ck_..." });
 *   const { answer, citations } = await rag.query("What is the refund policy?");
 *   for await (const token of rag.queryStream("Summarise the handbook")) process.stdout.write(token);
 *   for await (const { event, data } of rag.queryStreamEvents("…")) if (event === "sources") console.log(data.sources);
 *
 * Server-side only: ck_/tk_/admin keys are secrets. For browsers, use the
 * embeddable widget (publishable pk_ keys) instead.
 *
 * A key has a permission: "read" (search, ask, list, get), "write" (read plus add and change
 * content) or "full" (write plus delete and key management). A call its permission does not
 * allow rejects with CobRagError whose `code` is "key_permission_denied". Deleting a knowledge
 * base, a document or a customer with a key needs `confirmName`, the item's exact name; the
 * *DeletePreview methods say what would be removed and the name to send.
 *
 * Assistant methods: reads need `read`; draft edits, publish, restore and the on/off switch
 * need `write` (a workspace member's sign-in may edit drafts but not publish). The guided-setup
 * methods (startSetup ... applySetup): reads need `read`, every other step `write`; applySetup
 * publishes, so a workspace member's sign-in cannot. The handoff methods (listHandoffs, getHandoff, updateHandoff) need read, write and write. The lead methods (listLeads ...
 * withdrawLeadConsent): reads, updates and the export need `write`; deleting a lead and managing
 * a knowledge base's webhooks need `full`.
 */

const TERMINAL = new Set(["completed", "failed", "pending_review", "cancelled"]);

export class CobRagError extends Error {
  constructor(status, detail, code) {
    super(`${status}: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
    this.name = "CobRagError";
    this.status = status;
    this.detail = detail;
    /** The engine's machine-readable error code, when it sent one. */
    this.code = code;
  }
}

function llmHeaders(llm) {
  if (!llm) return {};
  const names = { provider: "X-LLM-Provider", model: "X-LLM-Model", apiKey: "X-LLM-API-Key", baseUrl: "X-LLM-Base-URL" };
  const out = {};
  for (const [k, v] of Object.entries(llm)) if (names[k] && v) out[names[k]] = String(v);
  return out;
}

// The conversation layer's options, camelCase in, the engine's names out.
function assistantBody(a) {
  const out = {};
  if (a.conversationId) out.conversation_id = a.conversationId;
  if (a.pageUrl) out.page_url = a.pageUrl;
  if (a.useDraft) out.use_draft = true;
  if (a.debug) out.debug = true;
  if (a.simulate) {
    const s = {};
    if (a.simulate.officeHours) s.office_hours = a.simulate.officeHours;
    if (a.simulate.pageUrl) s.page_url = a.simulate.pageUrl;
    if (a.simulate.language) s.language = a.simulate.language;
    out.simulate = s;
  }
  return out;
}

function queryBody(query, { corpusId, systemPrompt, options, assistant } = {}) {
  const body = { query };
  if (corpusId) body.corpus_id = corpusId;
  if (systemPrompt) body.system_prompt = systemPrompt;
  if (options) body.options = options;
  if (assistant) body.assistant = assistantBody(assistant);
  return body;
}

export class CobRag {
  /**
   * @param {{baseUrl?: string, apiKey?: string, fetch?: typeof fetch}} [opts]
   */
  constructor({ baseUrl = "http://127.0.0.1:8000", apiKey, fetch: fetchImpl } = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.apiKey = apiKey;
    this._fetch = fetchImpl || globalThis.fetch.bind(globalThis);
  }

  async _raw(method, path, { json, body, headers = {}, params } = {}) {
    const url = new URL(this.baseUrl + path);
    if (params) for (const [k, v] of Object.entries(params)) if (v != null) url.searchParams.set(k, String(v));
    const h = { ...headers };
    if (this.apiKey) h["X-API-Key"] = this.apiKey;
    if (json !== undefined) h["Content-Type"] = "application/json";
    const res = await this._fetch(url.toString(), {
      method,
      headers: h,
      body: json !== undefined ? JSON.stringify(json) : body,
    });
    if (!res.ok) {
      let detail = res.statusText;
      let code;
      try {
        const body = await res.json();
        detail = body.detail ?? detail;
        if (typeof body.code === "string") code = body.code;
      } catch { /* not JSON */ }
      throw new CobRagError(res.status, detail, code);
    }
    return res;
  }

  async _req(method, path, opts) {
    const res = await this._raw(method, path, opts);
    return res.status === 204 ? null : res.json();
  }

  health() { return this._req("GET", "/api/v1/health"); }
  /** Readiness: resolves when Postgres, Redis and Qdrant answer; rejects on 503. */
  healthReady() { return this._req("GET", "/api/v1/health/ready"); }

  // identity
  /** Which kind of key this is (admin, tenant, corpus), what it belongs to, and `auth`
   *  ("api_key"; SDKs never sign in), `role` ("owner" for a tenant key) and `user`
   *  (dashboard sign-ins only). */
  whoami() { return this._req("GET", "/api/v1/whoami"); }
  /** Engine version, enabled features, models and the limits a UI should mirror. */
  info() { return this._req("GET", "/api/v1/info"); }
  /** The measured fast models and the recommended ones: `{available, benchmarks}`. */
  fastModelBenchmarks() { return this._req("GET", "/api/v1/models/fast-benchmarks"); }
  /** The measured answer models and the recommended ones: `{available, benchmarks}`. */
  answerModelBenchmarks() { return this._req("GET", "/api/v1/models/answer-benchmarks"); }
  /** Headline tables of the engine's other benchmarks: `{available, summary}`. */
  benchmarkSummary() { return this._req("GET", "/api/v1/models/benchmark-summary"); }
  ownTenant() { return this._req("GET", "/api/v1/tenants/me"); }
  rotateOwnTenantKey() { return this._req("POST", "/api/v1/tenants/me/rotate-key"); }

  // tenants (admin key)
  createTenant(name, fields = {}) { return this._req("POST", "/api/v1/admin/tenants", { json: { name, ...fields } }); }
  /** One page of tenants, newest first. status: pending | active | suspended; search: part of the name; isOperator: false = customers only (without the operator's own workspace). */
  async listTenants({ status, search, isOperator, offset = 0, limit = 100 } = {}) {
    return (await this._req("GET", "/api/v1/admin/tenants", { params: { status, search, is_operator: isOperator, offset, limit } })).items;
  }
  /** Activate a pending sign-up (note: e.g. how they paid). */
  activateTenant(id, opts = {}) {
    // `opts` is `{ planId?, note? }`; a bare string is the note (the earlier signature).
    const { planId, note } = typeof opts === "string" ? { note: opts } : opts || {};
    const json = {};
    if (planId != null) json.plan_id = planId;
    if (note != null) json.note = note;
    return this._req("POST", `/api/v1/admin/tenants/${id}/activate`, { json });
  }

  // plans (admin key; myPlan: tenant key or sign-in)
  /** Every plan, the default first, each with its `tenant_count`. */
  async listPlans({ includeInactive = true } = {}) {
    const res = await this._req("GET", "/api/v1/admin/plans", { params: { include_inactive: String(includeInactive) } });
    return res.items;
  }
  /** `fields`: description, max_knowledge_bases, monthly_questions, max_documents,
   *  max_widget_keys (omit or null = unlimited), is_default. */
  createPlan(name, fields = {}) { return this._req("POST", "/api/v1/admin/plans", { json: { name, ...fields } }); }
  /** One plan, with its `tenant_count`. */
  getPlan(planId) { return this._req("GET", `/api/v1/admin/plans/${planId}`); }
  /** Only the fields given change; a limit given as null becomes unlimited. */
  updatePlan(planId, fields) { return this._req("PATCH", `/api/v1/admin/plans/${planId}`, { json: fields }); }
  /** Refused (409) while tenants use it or while it is the default. */
  deletePlan(planId) { return this._req("DELETE", `/api/v1/admin/plans/${planId}`); }
  setTenantPlan(tenantId, planId) {
    return this._req("POST", `/api/v1/admin/tenants/${tenantId}/plan`, { json: { plan_id: planId } });
  }
  /** Admin: a tenant's plan, effective limits and usage this month. */
  tenantPlan(tenantId) { return this._req("GET", `/api/v1/admin/tenants/${tenantId}/plan`); }
  /** This tenant's plan, limits and usage: `{plan, limits, usage, resets_at}`. */
  myPlan() { return this._req("GET", "/api/v1/tenants/me/plan"); }

  // pipeline profiles (admin key; PIP1): how a business's questions are answered (model,
  // re-ranker, search width, answer length, queue priority). A plan picks one
  // (`pipeline_profile_id` on the plan); a business may be given another.
  /** Every profile, the default first: `{items, default_id}`. */
  listPipelineProfiles() { return this._req("GET", "/api/v1/admin/pipelines"); }
  /** A new profile. 422 `invalid_profile` names the rule when the settings do not fit. */
  createPipelineProfile(name, label, { description, config } = {}) {
    return this._req("POST", "/api/v1/admin/pipelines", { json: { name, label, description, config: config ?? {} } });
  }
  getPipelineProfile(profileId) { return this._req("GET", `/api/v1/admin/pipelines/${profileId}`); }
  /** Only the fields given change (`name`, `label`, `description`, `config`); `config` replaces the whole config. */
  updatePipelineProfile(profileId, fields) {
    return this._req("PATCH", `/api/v1/admin/pipelines/${profileId}`, { json: fields });
  }
  /** Refused (409) while a plan or business uses it, or while it is the default. */
  deletePipelineProfile(profileId) { return this._req("DELETE", `/api/v1/admin/pipelines/${profileId}`); }
  /** Which profile a business answers with, and where it comes from. */
  getTenantPipeline(tenantId) { return this._req("GET", `/api/v1/admin/pipelines/tenants/${tenantId}`); }
  /** Give a business a profile of its own; null goes back to its plan's. */
  setTenantPipeline(tenantId, profileId) {
    return this._req("PUT", `/api/v1/admin/pipelines/tenants/${tenantId}`, { json: { profile_id: profileId } });
  }

  // fair use (PIP3): per-plan limits (fields of the plan) with per-business overrides here.
  // The first three are for the admin key; fairUse() is a workspace's own read-only view.
  listFairUse({ q, state, offset, limit } = {}) {
    return this._req("GET", "/api/v1/admin/fair-use", { params: { q, state, offset, limit } });
  }

  getFairUse(tenantId) { return this._req("GET", `/api/v1/admin/fair-use/tenants/${tenantId}`); }

  setFairUse(tenantId, { limits, policy, clear } = {}) {
    return this._req("PUT", `/api/v1/admin/fair-use/tenants/${tenantId}`, {
      json: { limits: limits ?? {}, policy: policy ?? {}, clear: clear ?? [] },
    });
  }

  fairUse() { return this._req("GET", "/api/v1/fair-use"); }

  // leads and knowledge-base webhooks (conversation C4). Lead reads, updates and the export need
  // a `write` key (contact data is not for a read-only key); deleting a lead and managing webhooks
  // need `full`. Delete, export and webhook changes are owner-only for a signed-in workspace member.
  /** Leads the key may see, newest first. `q` finds a phone number or e-mail exactly. */
  listLeads({ corpusId, status, trigger, dateFrom, dateTo, q, pageUrl, offset = 0, limit = 25 } = {}) {
    const params = { offset: String(offset), limit: String(limit) };
    const filters = { corpus_id: corpusId, status, trigger, date_from: dateFrom, date_to: dateTo, q, page_url: pageUrl };
    for (const [k, v] of Object.entries(filters)) if (v !== undefined && v !== null) params[k] = String(v);
    return this._req("GET", "/api/v1/leads", { params });
  }
  /** One lead with its summary, notes, consent record and chat transcript. */
  getLead(id) { return this._req("GET", `/api/v1/leads/${id}`); }
  /** `fields`: status, assignee_user_id (null unassigns), notes, qualification. */
  updateLead(id, fields) { return this._req("PATCH", `/api/v1/leads/${id}`, { json: fields }); }
  // handoffs (conversation C5). The list needs a `read` key; one handoff carries the linked lead's
  // contact data, so it and the status change need `write`. Nothing here is owner-only.
  /** Handoffs the key may see, newest first. `status` is "new" or "handled". */
  listHandoffs({ corpusId, status, reason, channel, from, to, offset = 0, limit = 50 } = {}) {
    const params = { offset: String(offset), limit: String(limit) };
    const filters = { corpus_id: corpusId, status, reason, channel, from, to };
    for (const [k, v] of Object.entries(filters)) if (v !== undefined && v !== null) params[k] = String(v);
    return this._req("GET", "/api/v1/handoffs", { params });
  }
  /** One handoff with its summary, the masked chat and the lead of a callback request. */
  getHandoff(id) { return this._req("GET", `/api/v1/handoffs/${id}`); }
  /** Mark a handoff "handled" (or "new" again). */
  updateHandoff(id, status) { return this._req("PATCH", `/api/v1/handoffs/${id}`, { json: { status } }); }
  /** Find the chat a visitor quoted a reference for ("SD-4F7K", any case): the matching conversation,
   * handoff and lead ids per knowledge base. Throws reference_invalid (422) or reference_not_found (404). */
  lookupReference(ref) { return this._req("GET", "/api/v1/handoffs/lookup", { params: { ref } }); } // TV2 reference
  // WhatsApp (TV6): a knowledge base's number and its chats. Reads need `read` (one chat's number and
  // messages need `write`); replying and the chat switch need `write`; settings, the brochure list and
  // disconnecting are owner-only for a signed-in member. The deployer connects a number with an admin key.
  /** The knowledge base's WhatsApp number: status, settings, who pays Meta (never a token). */
  whatsapp(corpusId) { return this._req("GET", `/api/v1/corpora/${corpusId}/whatsapp`); }
  /** Connect a number by hand (admin key): ids, a permanent token, and who pays Meta ("business" or "deployer"). */
  setWhatsappManual(corpusId, { phoneNumberId, wabaId, accessToken, metaBilling }) {
    return this._req("PUT", `/api/v1/corpora/${corpusId}/whatsapp/manual`, {
      json: { phone_number_id: phoneNumberId, waba_id: wabaId, access_token: accessToken, meta_billing: metaBilling },
    });
  }
  /** `enabled`, `human_pause_hours` (1 to 72) and `follow_up` ({ enabled, template_name, language, after_hours }). */
  updateWhatsapp(corpusId, fields) { return this._req("PATCH", `/api/v1/corpora/${corpusId}/whatsapp`, { json: fields }); }
  /** The documents customers may be sent on WhatsApp (at most 20 PDFs, JPGs or PNGs). */
  setWhatsappDocuments(corpusId, documentIds) {
    return this._req("PUT", `/api/v1/corpora/${corpusId}/whatsapp/documents`, { json: { document_ids: documentIds } });
  }
  /** Disconnect the number: its token is deleted, the chats stay readable. */
  disconnectWhatsapp(corpusId) { return this._req("DELETE", `/api/v1/corpora/${corpusId}/whatsapp`); }
  /** The WhatsApp chats, most recent customer message first (names masked). */
  whatsappThreads(corpusId, { unread = false, offset = 0, limit = 50 } = {}) {
    const params = { offset: String(offset), limit: String(limit) };
    if (unread) params.unread = "true";
    return this._req("GET", `/api/v1/corpora/${corpusId}/whatsapp/threads`, { params });
  }
  /** One chat: the number in full and the last 100 messages. Opening it marks it read. */
  whatsappThread(corpusId, threadId) { return this._req("GET", `/api/v1/corpora/${corpusId}/whatsapp/threads/${threadId}`); }
  /** Answer a customer (text, up to 4,096 characters, within 24 hours of their last message). */
  whatsappReply(corpusId, threadId, text) {
    return this._req("POST", `/api/v1/corpora/${corpusId}/whatsapp/threads/${threadId}/reply`, { json: { text } });
  }
  /** Keep the assistant quiet in one chat (true), or let it answer again (false). */
  updateWhatsappThread(corpusId, threadId, { botPaused }) {
    return this._req("PATCH", `/api/v1/corpora/${corpusId}/whatsapp/threads/${threadId}`, { json: { bot_paused: botPaused } });
  }
  // /WhatsApp (TV6)
  /** Erase a lead and the website chat it came from (owners only). */
  deleteLead(id) { return this._req("DELETE", `/api/v1/leads/${id}`); }
  /** The matching leads as CSV text (owners only; the export is audited). */
  async exportLeads({ corpusId, ...filters } = {}) {
    const params = {};
    for (const [k, v] of Object.entries({ corpus_id: corpusId, ...filters })) if (v != null) params[k] = String(v);
    const res = await this._raw("GET", "/api/v1/leads/export.csv", { params });
    // Keep the byte-order mark (res.text() drops it): a file saved from this opens right in Excel.
    return new TextDecoder("utf-8", { ignoreBOM: true }).decode(await res.arrayBuffer());
  }
  /** The webhooks that receive this knowledge base's leads. */
  async listCorpusWebhooks(corpusId) { return (await this._req("GET", `/api/v1/corpora/${corpusId}/webhooks`)).webhooks; }
  /** `events`: "lead.created" and/or "handoff.requested". https and a public host only. */
  createCorpusWebhook(corpusId, { url, events, secret }) {
    return this._req("POST", `/api/v1/corpora/${corpusId}/webhooks`, { json: { url, events, secret } });
  }
  updateCorpusWebhook(corpusId, webhookId, { enabled }) {
    return this._req("PATCH", `/api/v1/corpora/${corpusId}/webhooks/${webhookId}`, { json: { enabled } });
  }
  deleteCorpusWebhook(corpusId, webhookId) {
    return this._req("DELETE", `/api/v1/corpora/${corpusId}/webhooks/${webhookId}`);
  }
  /** What the public withdrawal page does with the token from its link (no key needed). */
  withdrawLeadConsent(token) { return this._req("POST", "/api/v1/public/leads/withdraw", { json: { token } }); }

  // assistant configuration (conversation C2). Reads need the `read` permission; draft edits,
  // publish, restore and the on/off switch need `write` (a workspace member's sign-in may edit
  // drafts but not publish). Publishing is reversible: restore an older version.
  /** The published assistant and the draft being edited (either may be null). */
  getAssistant(corpusId) { return this._req("GET", `/api/v1/corpora/${corpusId}/assistant`); }
  /** Replace the draft. `baseRevision` is the draft_revision you edited (409 `assistant_draft_conflict` otherwise). */
  putAssistantDraft(corpusId, config, { baseRevision } = {}) {
    const json = { config };
    if (baseRevision != null) json.base_revision = baseRevision;
    return this._req("PUT", `/api/v1/corpora/${corpusId}/assistant/draft`, { json });
  }
  /** Merge a JSON merge patch (RFC 7396; null removes a field) into the draft. */
  patchAssistantDraft(corpusId, patch, { baseRevision } = {}) {
    const json = { patch };
    if (baseRevision != null) json.base_revision = baseRevision;
    return this._req("PATCH", `/api/v1/corpora/${corpusId}/assistant/draft`, { json });
  }
  /** Start the draft from a business template (see assistantTemplates). */
  assistantDraftFromTemplate(corpusId, template, { keepIdentity = true, baseRevision } = {}) {
    const json = { template, keep_identity: keepIdentity };
    if (baseRevision != null) json.base_revision = baseRevision;
    return this._req("POST", `/api/v1/corpora/${corpusId}/assistant/draft/from-template`, { json });
  }
  /** Throw the draft away; the published assistant and its history are untouched. */
  discardAssistantDraft(corpusId) { return this._req("DELETE", `/api/v1/corpora/${corpusId}/assistant/draft`); }
  /** Publish the draft as the next version (owner keys; 422 names what to fix). */
  publishAssistant(corpusId, { baseRevision, note } = {}) {
    const json = { base_revision: baseRevision };
    if (note != null) json.note = note;
    return this._req("POST", `/api/v1/corpora/${corpusId}/assistant/publish`, { json });
  }
  /** Published and archived versions, newest first: `{items, total, offset, limit}`. */
  assistantVersions(corpusId, { offset = 0, limit = 20 } = {}) {
    return this._req("GET", `/api/v1/corpora/${corpusId}/assistant/versions`, { params: { offset, limit } });
  }
  assistantVersion(corpusId, version) { return this._req("GET", `/api/v1/corpora/${corpusId}/assistant/versions/${version}`); }
  /** Publish an older version again as a new version (history is never rewritten). */
  restoreAssistantVersion(corpusId, version) {
    return this._req("POST", `/api/v1/corpora/${corpusId}/assistant/versions/${version}/restore`);
  }
  /** Off = every surface answers exactly as before the assistant existed. */
  setAssistantEnabled(corpusId, enabled) {
    return this._req("POST", `/api/v1/corpora/${corpusId}/assistant/enabled`, { json: { enabled } });
  }
  /** Outcome analytics for one knowledge base, or for all the key can see (corpusId null). */
  assistantAnalytics(corpusId, { from, to } = {}) {
    const params = {};
    if (from != null) params.from = from;
    if (to != null) params.to = to;
    if (!corpusId) return this._req("GET", "/api/v1/assistant/analytics", { params });
    return this._req("GET", `/api/v1/corpora/${corpusId}/assistant/analytics`, { params });
  }
  /** The prompt the model would receive for a sample turn (no model call, nothing saved). */
  compileAssistant(corpusId, { config, sample } = {}) {
    const json = {};
    if (config != null) json.config = config;
    if (sample != null) json.sample = sample;
    return this._req("POST", `/api/v1/corpora/${corpusId}/assistant/compile`, { json });
  }
  /** Suggest a cleaner wording for a guidance rule (nothing is saved; needs `write`). */
  improveAssistantRule(corpusId, { instruction, type, ruleId } = {}) {
    const json = { instruction, type };
    if (ruleId != null) json.rule_id = ruleId;
    return this._req("POST", `/api/v1/corpora/${corpusId}/assistant/rules/improve`, { json });
  }
  /** The eight business templates. */
  assistantTemplates() { return this._req("GET", "/api/v1/assistant/templates"); }
  /** One template: its full config and ten test questions. */
  assistantTemplate(key) { return this._req("GET", `/api/v1/assistant/templates/${key}`); }
  // guided assistant setup (conversation C8). Reads (getSetup, setupQuestions, setupGaps) need
  // `read`; every other step needs `write`. applySetup publishes: a workspace member's sign-in
  // cannot, a tenant key acts as the owner and a corpus key with `write` can.
  /** Start a guided setup for a knowledge base (older unfinished ones are abandoned). */
  startSetup(corpusId, { businessType, websiteUrl } = {}) {
    const json = {};
    if (businessType != null) json.business_type = businessType;
    if (websiteUrl != null) json.website_url = websiteUrl;
    return this._req("POST", `/api/v1/corpora/${corpusId}/assistant/setup`, { json });
  }
  /** One setup session: status, proposal, confirmed profile, answers, provenance. */
  getSetup(corpusId, sessionId) { return this._req("GET", `/api/v1/corpora/${corpusId}/assistant/setup/${sessionId}`); }
  /** Send crawled pages ({ url, title, text }); returns the proposal. */
  proposeSetup(corpusId, sessionId, pages, { crawlJobId } = {}) {
    const json = { pages };
    if (crawlJobId != null) json.crawl_job_id = crawlJobId;
    return this._req("POST", `/api/v1/corpora/${corpusId}/assistant/setup/${sessionId}/propose`, { json });
  }
  /** The interview questions (prefilled) and the business profile to confirm. */
  setupQuestions(corpusId, sessionId) { return this._req("GET", `/api/v1/corpora/${corpusId}/assistant/setup/${sessionId}/questions`); }
  /** Save the answers and the confirmed profile; partial: true saves progress (required questions may be open). */
  answerSetup(corpusId, sessionId, answers, { businessType, profile, partial } = {}) {
    const json = { answers };
    if (partial) json.partial = true;
    if (businessType != null) json.business_type = businessType;
    if (profile != null) json.profile = profile;
    return this._req("POST", `/api/v1/corpora/${corpusId}/assistant/setup/${sessionId}/answers`, { json });
  }
  /** Write the draft assistant; wording "template" makes no model call. */
  generateSetup(corpusId, sessionId, { wording = "ai", baseRevision } = {}) {
    const json = { wording };
    if (baseRevision != null) json.base_revision = baseRevision;
    return this._req("POST", `/api/v1/corpora/${corpusId}/assistant/setup/${sessionId}/generate`, { json });
  }
  /** Create the setup's test set and start its run; returns golden_set_id / eval_run_id. */
  runSetupTests(corpusId, sessionId) { return this._req("POST", `/api/v1/corpora/${corpusId}/assistant/setup/${sessionId}/tests`); }
  /** Questions the documents could not answer (empty while the run is unfinished). */
  setupGaps(corpusId, sessionId) { return this._req("GET", `/api/v1/corpora/${corpusId}/assistant/setup/${sessionId}/gaps`); }
  /** Publish the generated draft; baseRevision is the draft revision you reviewed. */
  applySetup(corpusId, sessionId, baseRevision, { note } = {}) {
    const json = { base_revision: baseRevision };
    if (note != null) json.note = note;
    return this._req("POST", `/api/v1/corpora/${corpusId}/assistant/setup/${sessionId}/apply`, { json });
  }
  // golden sets, evaluation runs and the review inbox (conversation C7). Reads, estimates and
  // comparisons need `read`; creating or changing sets, criteria, runs and ratings need `write`
  // (a full run spends model money and monthly questions); deleting a set needs `full`; the
  // judge settings need `write` from an owner (never a workspace member).
  /** The knowledge base's test sets, each with its question count and last run. */
  async listGoldenSets(corpusId) { return (await this._req("GET", `/api/v1/corpora/${corpusId}/golden-sets`)).items; }
  /** A set of questions: each pair has `query` and an expected_answer and/or an expected_behaviour. */
  createGoldenSet(corpusId, name, pairs = []) {
    return this._req("POST", `/api/v1/corpora/${corpusId}/golden-sets`, { json: { name, pairs } });
  }
  /** A set from a business template's test questions (default: the assistant's own). */
  goldenSetFromTemplate(corpusId, template = null) {
    return this._req("POST", `/api/v1/corpora/${corpusId}/golden-sets/from-template`, { json: { template } });
  }
  getGoldenSet(corpusId, setId) { return this._req("GET", `/api/v1/corpora/${corpusId}/golden-sets/${setId}`); }
  renameGoldenSet(corpusId, setId, name) {
    return this._req("PATCH", `/api/v1/corpora/${corpusId}/golden-sets/${setId}`, { json: { name } });
  }
  /** Delete a set with its questions (never the default set). Needs `full`. */
  deleteGoldenSet(corpusId, setId) { return this._req("DELETE", `/api/v1/corpora/${corpusId}/golden-sets/${setId}`); }
  addGoldenPairs(corpusId, setId, pairs) {
    return this._req("POST", `/api/v1/corpora/${corpusId}/golden-sets/${setId}/pairs`, { json: { pairs } });
  }
  /** Your own plain-language rules a judge model scores every answer against. */
  async listEvalCriteria(corpusId) { return (await this._req("GET", `/api/v1/corpora/${corpusId}/eval/criteria`)).items; }
  createEvalCriterion(corpusId, name, definition, threshold = 0.7) {
    return this._req("POST", `/api/v1/corpora/${corpusId}/eval/criteria`, { json: { name, definition, threshold } });
  }
  /** fields: definition, threshold, enabled. */
  updateEvalCriterion(corpusId, criterionId, fields) {
    return this._req("PATCH", `/api/v1/corpora/${corpusId}/eval/criteria/${criterionId}`, { json: fields });
  }
  deleteEvalCriterion(corpusId, criterionId) {
    return this._req("DELETE", `/api/v1/corpora/${corpusId}/eval/criteria/${criterionId}`);
  }
  /** The judge model (never its key) and the pass thresholds. */
  getEvalSettings(corpusId) { return this._req("GET", `/api/v1/corpora/${corpusId}/eval/settings`); }
  /** fields: judge_provider, judge_model, judge_api_key, judge_base_url, thresholds. */
  setEvalSettings(corpusId, fields) { return this._req("PUT", `/api/v1/corpora/${corpusId}/eval/settings`, { json: fields }); }
  /** What a run would cost (an estimate from token assumptions and the price table). */
  estimateEvalRun(corpusId, setId, { mode = "full", settings } = {}) {
    const json = { set_id: setId, mode };
    if (settings != null) json.settings = settings;
    return this._req("POST", `/api/v1/corpora/${corpusId}/eval/estimate`, { json });
  }
  /**
   * mode "fast": behaviour checks only, no answers. mode "full": fresh answers, scores and
   * criteria; send the estimate's total_usd as `confirmCostUsd` (409 `eval_cost_unconfirmed`
   * otherwise). `settings` is the experiment: assistant version, retrieval overrides, answer model.
   */
  startEvalRun(corpusId, setId, { mode = "fast", name, settings, confirmCostUsd } = {}) {
    const json = { set_id: setId, mode };
    if (name != null) json.name = name;
    if (settings != null) json.settings = settings;
    if (confirmCostUsd != null) json.confirm_cost_usd = confirmCostUsd;
    return this._req("POST", `/api/v1/corpora/${corpusId}/eval/runs`, { json });
  }
  async listEvalRuns(corpusId, { setId, kind } = {}) {
    const params = {};
    if (setId) params.set_id = setId;
    if (kind) params.kind = kind;
    return (await this._req("GET", `/api/v1/corpora/${corpusId}/eval/runs`, { params })).items;
  }
  /** A run with its metrics (mean, 95% interval, pass rate), tags and warnings. */
  getEvalRun(corpusId, runId) { return this._req("GET", `/api/v1/corpora/${corpusId}/eval/runs/${runId}`); }
  /** One row per question: answer, scores, the judge's reasons, behaviour checks. */
  listEvalSamples(corpusId, runId, { failedOnly = false, metric, below, tag, offset = 0, limit = 50 } = {}) {
    const params = { offset, limit };
    if (failedOnly) params.failed_only = "true";
    if (metric != null) params.metric = metric;
    if (below != null) params.below = below;
    if (tag != null) params.tag = tag;
    return this._req("GET", `/api/v1/corpora/${corpusId}/eval/runs/${runId}/samples`, { params });
  }
  /** rating: "good" | "acceptable" | "poor" (null clears it). */
  rateEvalSample(corpusId, sampleId, rating) {
    return this._req("PATCH", `/api/v1/corpora/${corpusId}/eval/samples/${sampleId}`, { json: { rating } });
  }
  /** Two to four completed runs of one set; the first is the baseline. */
  compareEvalRuns(corpusId, runIds) {
    return this._req("POST", `/api/v1/corpora/${corpusId}/eval/compare`, { json: { run_ids: runIds } });
  }
  /** Questions visitors could not get answered, grouped, most asked first. */
  async reviewItems(corpusId, { kind, since, until } = {}) {
    const params = {};
    if (kind) params.kind = kind;
    if (since) params.from = since;
    if (until) params.to = until;
    return (await this._req("GET", `/api/v1/corpora/${corpusId}/assistant/review`, { params })).items;
  }
  /**
   * action: "dismissed" | "added_document" | "added_answer" | "added_rule". payload:
   * { answer: {topic_name, question, answer, language}, rule: {instruction, type}, base_revision }.
   */
  reviewAction(corpusId, itemKey, action, payload = {}) {
    return this._req("POST", `/api/v1/corpora/${corpusId}/assistant/review/actions`, {
      json: { item_key: itemKey, action, ...payload },
    });
  }
  /** Suspend: every key, widget and sign-in of the tenant stops. `confirmName` must be its name exactly. */
  suspendTenant(id, confirmName, reason) {
    const json = { confirm_name: confirmName };
    if (reason != null) json.reason = reason;
    return this._req("POST", `/api/v1/admin/tenants/${id}/suspend`, { json });
  }
  reactivateTenant(id) { return this._req("POST", `/api/v1/admin/tenants/${id}/reactivate`); }
  /** Replace a tenant's tk_ key; the new one is returned once, the old one stops now. */
  rotateTenantKey(id) { return this._req("POST", `/api/v1/admin/tenants/${id}/rotate-key`); }
  /** Admin: what deleting the customer would remove (counts) and the `confirm_name` to send. */
  tenantDeletePreview(id) { return this._req("GET", `/api/v1/admin/tenants/${id}/delete-preview`); }
  /** Decline a pending sign-up: the tenant and its sign-up account are removed. */
  declineTenant(id, reason) {
    return this._req("POST", `/api/v1/admin/tenants/${id}/decline`, { json: reason == null ? {} : { reason } });
  }
  /** Delete the tenant and everything it owns. `confirmName` must be its name exactly; the operator's own workspace also needs `{ confirmOperatorWorkspace: true }`. */
  deleteTenant(id, confirmName, { confirmOperatorWorkspace = false } = {}) {
    const params = { confirm_name: confirmName };
    if (confirmOperatorWorkspace) params.confirm_operator_workspace = "true";
    return this._req("DELETE", `/api/v1/admin/tenants/${id}`, { params });
  }

  // team (tenant key; admin variants after)
  /** This tenant's members and open invitations: {members, invitations}. */
  team() { return this._req("GET", "/api/v1/tenants/me/members"); }
  /** Invite someone as "owner" or "member"; the link is e-mailed (never returned). */
  invite(email, role = "member") { return this._req("POST", "/api/v1/tenants/me/members", { json: { email, role } }); }
  revokeInvitation(id) { return this._req("DELETE", `/api/v1/tenants/me/invitations/${id}`); }
  /** E-mail a new link (the old one stops working), valid 7 more days. */
  resendInvitation(id) { return this._req("POST", `/api/v1/tenants/me/invitations/${id}/resend`); }
  /** role: "owner" | "member". The last owner stays an owner (409 last_owner). */
  setMemberRole(userId, role) { return this._req("PATCH", `/api/v1/tenants/me/members/${userId}`, { json: { role } }); }
  /** Remove someone from the workspace; their account stays. */
  removeMember(userId) { return this._req("DELETE", `/api/v1/tenants/me/members/${userId}`); }
  /** Admin: a tenant's members and open invitations. */
  tenantTeam(tenantId) { return this._req("GET", `/api/v1/admin/tenants/${tenantId}/members`); }
  /** Admin: invite someone to a tenant. The response's `accept_url` is the link to send
   *  them yourself when the engine has no SMTP. */
  inviteToTenant(tenantId, email, role = "owner") {
    return this._req("POST", `/api/v1/admin/tenants/${tenantId}/invitations`, { json: { email, role } });
  }
  /** Admin: revoke an open invitation of a tenant. */
  revokeTenantInvitation(tenantId, invitationId) {
    return this._req("DELETE", `/api/v1/admin/tenants/${tenantId}/invitations/${invitationId}`);
  }
  /** Admin: a new link (in `accept_url`) and 7 more days for a tenant's open invitation. */
  resendTenantInvitation(tenantId, invitationId) {
    return this._req("POST", `/api/v1/admin/tenants/${tenantId}/invitations/${invitationId}/resend`);
  }
  /** Admin: change someone's role in a tenant (409 last_owner for the last owner). */
  setTenantMemberRole(tenantId, userId, role) {
    return this._req("PATCH", `/api/v1/admin/tenants/${tenantId}/members/${userId}`, { json: { role } });
  }
  /** Admin: remove someone from a tenant; their account stays. */
  removeTenantMember(tenantId, userId) {
    return this._req("DELETE", `/api/v1/admin/tenants/${tenantId}/members/${userId}`);
  }

  /** Admin: recreate the operator's own workspace after it was deleted on purpose. */
  restoreOperatorWorkspace() {
    return this._req("POST", "/api/v1/admin/operator-workspace/restore");
  }

  // audit log (admin key)
  /**
   * Audit rows, newest first: `{items, total, offset, limit}`. `action` is exact or a
   * prefix ending in "." ("tenant."); `actor` is part of who did it; `start`/`end` are ISO
   * datetimes (default: the last 90 days; at most 366 days).
   */
  auditLog({ tenantId, action, actor, start, end, offset = 0, limit = 50 } = {}) {
    return this._req("GET", "/api/v1/admin/audit", {
      params: { tenant_id: tenantId, action, actor, start, end, offset, limit },
    });
  }

  // corpora (admin or tenant key)
  /**
   * Create a corpus; its ck_ key is in the response, once. With the admin key
   * `fields.tenant_id` is required (every knowledge base belongs to a tenant); a tenant
   * key creates corpora for itself and may omit it.
   */
  createCorpus(name, fields = {}) { return this._req("POST", "/api/v1/corpora", { json: { name, ...fields } }); }
  /** One page of corpora; items carry owner, document/chunk counts and last activity. */
  async listCorpora({ offset = 0, limit = 100, tenantId } = {}) {
    return (await this._req("GET", "/api/v1/corpora", { params: { offset, limit, tenant_id: tenantId } })).items;
  }
  getCorpus(id) { return this._req("GET", `/api/v1/corpora/${id}`); }
  updateCorpus(id, fields) { return this._req("PUT", `/api/v1/corpora/${id}`, { json: fields }); }
  /** With an API key, `confirmName` must be the knowledge base's exact name (422 `confirmation_required` / `confirm_mismatch`). */
  deleteCorpus(id, confirmName) {
    return this._req("DELETE", `/api/v1/corpora/${id}`, { params: { confirm_name: confirmName } });
  }
  /** What deleting the knowledge base would remove (counts) and the `confirm_name` to send. */
  corpusDeletePreview(id) { return this._req("GET", `/api/v1/corpora/${id}/delete-preview`); }
  /** Replaces the knowledge base's default key; other named keys are untouched. */
  rotateCorpusKey(id) { return this._req("POST", `/api/v1/corpora/${id}/rotate-key`); }

  // named API keys (a "full" key, or the admin or tenant key)
  /** The knowledge base's keys: name, prefix, permission, last used. Never a key. */
  async listCorpusKeys(corpusId) { return (await this._req("GET", `/api/v1/corpora/${corpusId}/keys`)).keys; }
  /** A named key; `api_key` in the response is shown once. permission: "read" (default) | "write" | "full". */
  createCorpusKey(corpusId, name, permission = "read") {
    return this._req("POST", `/api/v1/corpora/${corpusId}/keys`, { json: { name, permission } });
  }
  /** `fields`: `{ name?, permission? }`. */
  updateCorpusKey(corpusId, keyId, fields) {
    return this._req("PATCH", `/api/v1/corpora/${corpusId}/keys/${keyId}`, { json: fields });
  }
  /** A new secret for one key (returned once); the old one stops now. */
  rotateCorpusNamedKey(corpusId, keyId) {
    return this._req("POST", `/api/v1/corpora/${corpusId}/keys/${keyId}/rotate`);
  }
  revokeCorpusKey(corpusId, keyId) { return this._req("DELETE", `/api/v1/corpora/${corpusId}/keys/${keyId}`); }
  /** This customer's keys (a "full" tenant key, or an owner's sign-in). */
  async listMyKeys() { return (await this._req("GET", "/api/v1/tenants/me/keys")).keys; }
  createMyKey(name, permission = "read") {
    return this._req("POST", "/api/v1/tenants/me/keys", { json: { name, permission } });
  }
  updateMyKey(keyId, fields) { return this._req("PATCH", `/api/v1/tenants/me/keys/${keyId}`, { json: fields }); }
  rotateMyNamedKey(keyId) { return this._req("POST", `/api/v1/tenants/me/keys/${keyId}/rotate`); }
  revokeMyKey(keyId) { return this._req("DELETE", `/api/v1/tenants/me/keys/${keyId}`); }
  /** Admin: a customer's keys. */
  async listTenantKeys(tenantId) { return (await this._req("GET", `/api/v1/admin/tenants/${tenantId}/keys`)).keys; }
  createTenantKey(tenantId, name, permission = "read") {
    return this._req("POST", `/api/v1/admin/tenants/${tenantId}/keys`, { json: { name, permission } });
  }
  updateTenantKey(tenantId, keyId, fields) {
    return this._req("PATCH", `/api/v1/admin/tenants/${tenantId}/keys/${keyId}`, { json: fields });
  }
  rotateTenantNamedKey(tenantId, keyId) {
    return this._req("POST", `/api/v1/admin/tenants/${tenantId}/keys/${keyId}/rotate`);
  }
  revokeTenantKey(tenantId, keyId) { return this._req("DELETE", `/api/v1/admin/tenants/${tenantId}/keys/${keyId}`); }

  /**
   * Upload a document. `file` is a Blob/File or {name, data} with data a
   * Uint8Array/ArrayBuffer/string. With wait:true, resolves when indexed.
   */
  async ingest(file, { corpusId, metadata, mode = "auto", autoConfirm = false, wait = false, pollMs = 2000, timeoutMs = 600000 } = {}) {
    const form = new FormData();
    const blob = file instanceof Blob ? file : new Blob([file.data]);
    form.append("file", blob, file.name || "document");
    form.append("mode", mode); // auto (hold near-duplicates for review) | new | replace | version
    if (autoConfirm) form.append("auto_confirm", "true");
    if (corpusId) form.append("corpus_id", corpusId);
    if (metadata) form.append("metadata", JSON.stringify(metadata));
    const job = await this._req("POST", "/api/v1/ingest", { body: form });
    return wait ? this.waitForJob(job.job_id, { pollMs, timeoutMs }) : job;
  }

  /**
   * Import up to 1000 text records ({id, text, title?, url?, metadata?}) per call.
   * Re-importing an id replaces that record; unchanged records are skipped.
   */
  async ingestRecords(records, { corpusId, wait = false, pollMs = 1000, timeoutMs = 600000 } = {}) {
    const json = corpusId ? { records, corpus_id: corpusId } : { records };
    const job = await this._req("POST", "/api/v1/ingest/records", { json });
    return wait ? this.waitForJob(job.job_id, { pollMs, timeoutMs }) : job;
  }

  getJob(jobId) { return this._req("GET", `/api/v1/ingest/jobs/${jobId}`); }
  /** params: status, corpus_id, offset, limit -> {items, total, offset, limit} */
  listJobs(params = {}) { return this._req("GET", "/api/v1/ingest/jobs", { params }); }
  /** Run a failed upload again from its stored file, as a new job -> {job_id, status}. A job that cannot be retried is a 409 job_not_retryable with a reason. */
  retryIngestJob(jobId) { return this._req("POST", `/api/v1/ingest/jobs/${jobId}/retry`); }

  // documents (corpus key, or tenant/admin key + corpus_id)
  /** params: corpus_id, search, source ("website" | "articles" | "files"), offset, limit */
  listDocuments(params = {}) { return this._req("GET", "/api/v1/documents", { params }); }
  /** params: corpus_id, version_id, chunk_offset, chunk_limit */
  getDocument(id, params = {}) { return this._req("GET", `/api/v1/documents/${id}`, { params }); }
  /** With an API key, `confirmName` must be the document's file name (or record id) exactly. params: `{ corpus_id }`. */
  deleteDocument(id, confirmName, params = {}) {
    return this._req("DELETE", `/api/v1/documents/${id}`, { params: { ...params, confirm_name: confirmName } });
  }
  /** What deleting the document would remove, and the `confirm_name` to send. params: `{ corpus_id }`. */
  documentDeletePreview(id, params = {}) {
    return this._req("GET", `/api/v1/documents/${id}/delete-preview`, { params });
  }

  async waitForJob(jobId, { pollMs = 2000, timeoutMs = 600000 } = {}) {
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      const job = await this.getJob(jobId);
      if (TERMINAL.has(job.status)) return job;
      if (Date.now() > deadline) throw new Error(`ingest job ${jobId} still ${job.status}`);
      await new Promise((r) => setTimeout(r, pollMs));
    }
  }

  /**
   * Answer with citations. opts.llm = {provider, model, apiKey, baseUrl} overrides the model.
   * opts.assistant = {conversationId, pageUrl, useDraft, debug, simulate} are the conversation
   * layer's options: with the knowledge base's assistant on, the response also carries `label`,
   * `route`, `language`, `quick_replies`, `cta`, `ui`, `timings`, `assistant_version` (and
   * `trace` with `debug`, which needs an owner and an API key with write permission).
   */
  query(query, opts = {}) {
    return this._req("POST", "/api/v1/query", { json: queryBody(query, opts), headers: llmHeaders(opts.llm) });
  }

  /** Async-iterate the answer token by token. A failure part-way throws CobRagError. */
  async *queryStream(query, opts = {}) {
    for await (const { event, data } of this.queryStreamEvents(query, opts)) {
      if (event === "token") yield data.content;
      else if (event === "error") throw new CobRagError(502, data.detail);
      else if (event === "done") return;
    }
  }

  /**
   * Async-iterate every event as {event, data}: `stage` ({stage: "understand" |
   * "search" | "rerank" | "check" | "write", elapsed_ms}) while the engine works, `sources`
   * (citations with score_breakdown, and query_id), then `token` ({content})
   * repeatedly, then `done` ({query_id, model, latency_ms, usage, timings,
   * thinking_ms?, reasoning_tokens?}) or `error` ({detail, code?}).
   * With the assistant on the stream also has `meta` ({label, route, language, ...}, first
   * after the `understand` stage), `quick_replies`, `ui` (a lead form or handoff card) and
   * `trace` (debug only), and `done` adds {label, route, language, cta, claims_removed,
   * gate_threshold}. `queryStream` skips everything but the tokens.
   */
  async *queryStreamEvents(query, opts = {}) {
    const res = await this._raw("POST", "/api/v1/query/stream", { json: queryBody(query, opts), headers: llmHeaders(opts.llm) });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let event = "message";
    let data = [];
    const line = (raw) => {
      const l = raw.replace(/\r$/, "");
      if (l === "") {
        if (!data.length) return null;
        const out = { event, data: JSON.parse(data.join("\n")) };
        event = "message";
        data = [];
        return out;
      }
      if (l.startsWith(":")) return null;
      const i = l.indexOf(":");
      const field = i === -1 ? l : l.slice(0, i);
      let value = i === -1 ? "" : l.slice(i + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
      return null;
    };
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const l of lines) {
        const out = line(l);
        if (out) yield out;
      }
    }
    for (const l of [buffer, ""]) {
      const out = line(l);
      if (out) yield out;
    }
  }

  /** Ranked chunks only — no LLM call. */
  retrieve(query, opts = {}) { return this._req("POST", "/api/v1/query/retrieve", { json: queryBody(query, opts) }); }

  // website widget
  createWidgetKey(corpusId, name, allowedOrigins, fields = {}) {
    return this._req("POST", `/api/v1/corpora/${corpusId}/widget-keys`, { json: { name, allowed_origins: allowedOrigins, ...fields } });
  }
  listWidgetKeys(corpusId) { return this._req("GET", `/api/v1/corpora/${corpusId}/widget-keys`); }
  updateWidgetKey(corpusId, keyId, fields) { return this._req("PATCH", `/api/v1/corpora/${corpusId}/widget-keys/${keyId}`, { json: fields }); }
  rotateWidgetKey(corpusId, keyId) { return this._req("POST", `/api/v1/corpora/${corpusId}/widget-keys/${keyId}/rotate`); }
  deleteWidgetKey(corpusId, keyId) { return this._req("DELETE", `/api/v1/corpora/${corpusId}/widget-keys/${keyId}`); }

  /** params: {corpus_id, tenant_id, start, end, group_by} */
  usage(params = {}) { return this._req("GET", "/api/v1/usage", { params }); }
  /** The dashboard header summary (tenant key or workspace session; not a ck_ key). */
  usageConsumption() { return this._req("GET", "/api/v1/usage/consumption"); }
  /** Admin: platform cost report. */
  costs(params = {}) { return this._req("GET", "/api/v1/admin/costs", { params }); }
  // BIL1 provider balances
  /** Admin: what OpenRouter, OpenAI, Anthropic and any manual provider have left ({items, worst}; USD micro-units). */
  providerBalances() { return this._req("GET", "/api/v1/admin/billing/providers"); }
  // /BIL1 provider balances
  // BIL2 pricing and accounts
  /** Admin: how customers on a plan pay (`version: null` = no pricing, nothing is charged). Money is integer minor units next to `currency`. */
  getPlanPricing(planId) { return this._req("GET", `/api/v1/admin/billing/plans/${planId}/pricing`); }
  /** Admin: replace a plan's pricing with a new version; a field not given takes its default. The response lists what `changed`. */
  setPlanPricing(planId, fields = {}) { return this._req("PUT", `/api/v1/admin/billing/plans/${planId}/pricing`, { json: fields }); }
  /** Admin: every customer's billing, `{items, total}`. `paymentType`: "prepaid" | "postpaid". */
  listBillingAccounts({ q, paymentType, offset = 0, limit = 50 } = {}) {
    const params = { offset: String(offset), limit: String(limit) };
    if (q != null) params.q = q;
    if (paymentType != null) params.payment_type = paymentType;
    return this._req("GET", "/api/v1/admin/billing/accounts", { params });
  }
  /** Admin: one customer's billing account (created on first read), with its effective terms. */
  getBillingAccount(tenantId) { return this._req("GET", `/api/v1/admin/billing/accounts/${tenantId}`); }
  /** Admin: change a customer's terms: `overrides` (null removes one), `limit_overrides`, `currency`, `pause_override`, `billing_day`, `notes`. */
  updateBillingAccount(tenantId, fields) { return this._req("PATCH", `/api/v1/admin/billing/accounts/${tenantId}`, { json: fields }); }
  /** The caller's own workspace: how it pays (money fields are null for a member). */
  billingAccount() { return this._req("GET", "/api/v1/billing/account"); }
  // /BIL2 pricing and accounts
  // BIL3 ledger
  /** Admin: a customer's transactions, newest first: `{items, total, balance_minor, available_minor}`. `start`/`end` are YYYY-MM-DD (UTC). Amounts are integer minor units next to `currency`. */
  listLedger(tenantId, { entryType, start, end, offset = 0, limit = 50 } = {}) {
    const params = { offset: String(offset), limit: String(limit) };
    if (entryType != null) params.entry_type = entryType;
    if (start != null) params.start = start;
    if (end != null) params.end = end;
    return this._req("GET", `/api/v1/admin/billing/accounts/${tenantId}/ledger`, { params });
  }
  /** Admin: record a top-up, an adjustment (signed) or a refund: `entry_type`, `amount_minor`, `idempotency_key`, `memo`, `memo_public`, `confirm_amount_minor`, `expires_at`. The same key again changes nothing. */
  addLedgerEntry(tenantId, fields) { return this._req("POST", `/api/v1/admin/billing/accounts/${tenantId}/ledger`, { json: fields }); }
  /** Owner: this workspace's transactions, `{items, total}`, with the customer-facing `description`. A member gets 403 `owner_required`. */
  billingLedger({ offset = 0, limit = 50 } = {}) {
    return this._req("GET", "/api/v1/billing/ledger", { params: { offset: String(offset), limit: String(limit) } });
  }
  // /BIL3 ledger
  // P1 payments
  /** Admin: payments received, newest first: `{items, total}` with each payment's fee. `status`: captured, authorized, failed, mismatch, refunded, partially_refunded. `start`/`end` are YYYY-MM-DD (UTC). Amounts are integer minor units (paise). */
  listPayments({ tenantId, status, start, end, offset = 0, limit = 50 } = {}) {
    const params = { offset: String(offset), limit: String(limit) };
    if (tenantId != null) params.tenant_id = tenantId;
    if (status != null) params.status = status;
    if (start != null) params.start = start;
    if (end != null) params.end = end;
    return this._req("GET", "/api/v1/admin/billing/payments", { params });
  }
  /** Admin: one payment with its refunds, the deliveries about it and what can still be refunded (`refundable_minor`). */
  getPayment(paymentId) { return this._req("GET", `/api/v1/admin/billing/payments/${paymentId}`); }
  /** Admin: refund part of a payment from the workspace's unused balance, to the way it paid: `amount_minor` (taken off the balance, before GST), `memo`, `idempotency_key`, `confirm_amount_minor` for a large refund. A refusal by the payment service is 502 `refund_failed` and the money is put back. */
  refundPayment(paymentId, fields) { return this._req("POST", `/api/v1/admin/billing/payments/${paymentId}/refunds`, { json: fields }); }
  /** Admin: a link a customer opens to pay: `purpose` (top_up, plan, add_on), `amount_minor`, `add_on_id`, `send_email`. Returns `{order_id, short_url, expires_at, gross_minor, emailed}`. */
  createPaymentLink(tenantId, fields) { return this._req("POST", `/api/v1/admin/billing/accounts/${tenantId}/payment-links`, { json: fields }); }
  /** Owner: who this workspace's tax invoices are made out to (`complete` says whether they are enough to pay). A member gets 403 `owner_required`. */
  taxDetails() { return this._req("GET", "/api/v1/billing/tax-details"); }
  /** Owner: save the billing name, address and state (a GST number is optional; its state wins). */
  setTaxDetails(fields) { return this._req("PUT", "/api/v1/billing/tax-details", { json: fields }); }
  /** Owner: this workspace's payments, newest first, `{items, total}` (no fees). */
  billingPayments({ offset = 0, limit = 50 } = {}) {
    return this._req("GET", "/api/v1/billing/payments", { params: { offset: String(offset), limit: String(limit) } });
  }
  // /P1 payments

  // P2 invoices
  /** Owner: this workspace's tax invoices and credit notes, newest first, `{items, total}`. Each item says whether its PDF is ready. */
  billingInvoices({ offset = 0, limit = 50 } = {}) {
    return this._req("GET", "/api/v1/billing/invoices", { params: { offset: String(offset), limit: String(limit) } });
  }
  /** Admin: every tax document, newest first: `{items, total, totals}`. `docType`: invoice, bill_of_supply, credit_note. `fy` like "26-27"; `start`/`end` are YYYY-MM-DD (Indian time). `totals` covers everything the filter matches (credit notes subtract). */
  listInvoices({ tenantId, docType, fy, start, end, offset = 0, limit = 50 } = {}) {
    const params = { offset: String(offset), limit: String(limit) };
    if (tenantId) params.tenant_id = tenantId;
    if (docType) params.doc_type = docType;
    if (fy) params.fy = fy;
    if (start) params.start = start;
    if (end) params.end = end;
    return this._req("GET", "/api/v1/admin/billing/invoices", { params });
  }
  // /P2 invoices

  // BIL5 add-ons
  /** Admin: the add-on catalogue, `{items}`: credit packs, extra limits and question packs. */
  listAddOns({ includeInactive = false } = {}) {
    return this._req("GET", "/api/v1/admin/billing/add-ons", includeInactive ? { params: { include_inactive: "true" } } : {});
  }
  /** Admin: add one to the catalogue: `kind`, `name`, `currency`, `price_minor` and the fields of the kind (`credit_minor` + `bonus_pct` + `expiry_days`, or `limit_field` + `limit_delta` + `duration`, or `questions`). */
  createAddOn(fields) { return this._req("POST", "/api/v1/admin/billing/add-ons", { json: fields }); }
  /** Admin: change an add-on (only the fields you send; `is_active: false` retires it). Existing grants keep what they were given. */
  updateAddOn(addOnId, fields) { return this._req("PATCH", `/api/v1/admin/billing/add-ons/${addOnId}`, { json: fields }); }
  /** Admin: delete an add-on never given to anyone (else 409 `add_on_in_use`). */
  deleteAddOn(addOnId) { return this._req("DELETE", `/api/v1/admin/billing/add-ons/${addOnId}`); }
  /** Admin: the add-ons given to a customer, newest first, `{items}`; `activeOnly` keeps those raising a limit now. */
  listGrants(tenantId, { activeOnly = false } = {}) {
    return this._req("GET", `/api/v1/admin/billing/accounts/${tenantId}/grants`, activeOnly ? { params: { active_only: "true" } } : {});
  }
  /** Admin: apply an add-on to a customer: `add_on_id`, `idempotency_key` (the same key again returns the same grant), `payment` (external, free or wallet), `memo`. */
  applyAddOn(tenantId, fields) { return this._req("POST", `/api/v1/admin/billing/accounts/${tenantId}/grants`, { json: fields }); }
  /** Admin: end an extra limit or question pack now. Money is not returned automatically; a credit pack cannot be revoked. */
  revokeGrant(tenantId, grantId, { memo } = {}) {
    return this._req("POST", `/api/v1/admin/billing/accounts/${tenantId}/grants/${grantId}/revoke`, { json: memo ? { memo } : {} });
  }
  // /BIL5 add-ons

  // BIL6 statements
  /** Admin: a customer's monthly statements, newest first: `{items: [{month, currency, opening_minor, closing_minor, live}]}`. The current month is `live`. */
  listStatements(tenantId) { return this._req("GET", `/api/v1/admin/billing/accounts/${tenantId}/statements`); }
  /** Admin: one month (`YYYY-MM`, UTC) of a customer's statement. `format`: "json" (default, an object), "csv" or "html" (text; the page prints to PDF). */
  getStatement(tenantId, month, { format = "json" } = {}) {
    if (format === "json") return this._req("GET", `/api/v1/admin/billing/accounts/${tenantId}/statements/${month}`);
    return this._statementText("GET", `/api/v1/admin/billing/accounts/${tenantId}/statements/${month}`, format);
  }
  /** Owner: this workspace's monthly statements, `{items}`. A member gets 403 `owner_required`. */
  billingStatements() { return this._req("GET", "/api/v1/billing/statements"); }
  /** Owner: one month (`YYYY-MM`) of this workspace's statement, as "json" (default), "csv" or "html". */
  billingStatement(month, { format = "json" } = {}) {
    if (format === "json") return this._req("GET", `/api/v1/billing/statements/${month}`);
    return this._statementText("GET", `/api/v1/billing/statements/${month}`, format);
  }
  /** Owner: ask the operator to add money (`amountMinor` in paise or cents, `note`; both optional) -> `{sent}`. One request per workspace per hour: another is 429 `topup_request_too_soon`. */
  requestTopUp({ amountMinor, note } = {}) {
    const json = {};
    if (amountMinor != null) json.amount_minor = amountMinor;
    if (note != null) json.note = note;
    return this._req("POST", "/api/v1/billing/top-up-requests", { json });
  }
  async _statementText(method, path, format, extra = {}) {
    const res = await this._raw(method, path, { params: { ...extra, format } });
    // Keep the byte-order mark-free text exactly as sent (a CSV saved from this opens in Excel).
    return new TextDecoder("utf-8", { ignoreBOM: true }).decode(await res.arrayBuffer());
  }
  // /BIL6 statements

  // TV3 no surprises
  /** Owner: the monthly cap on usage charges, in minor units (paise, cents); `null` removes it. Alerts at 80% and at the cap. */
  setSpendCap(capMinor) { return this._req("PUT", "/api/v1/billing/spend-cap", { json: { cap_minor: capMinor } }); }
  /** Admin: what saving these plan prices would do (same body as `setPlanPricing`), without saving. */
  previewPricing(planId, fields = {}) { return this._req("POST", `/api/v1/admin/billing/plans/${planId}/pricing/preview`, { json: fields }); }
  /** Admin: call off a price change that has been announced but has not started. */
  cancelScheduledPricing(planId) { return this._req("DELETE", `/api/v1/admin/billing/plans/${planId}/pricing/scheduled`); }
  /** Admin: call off a raise for one business that is waiting out its notice period. */
  cancelPendingChange(tenantId) { return this._req("DELETE", `/api/v1/admin/billing/accounts/${tenantId}/pending-change`); }
  // /TV3 no surprises

  // TV4 value report
  /** The values behind the value report (or the suggestions for this kind of business until the owner saves their own). Workspace sign-in or tenant key. */
  valueSettings() { return this._req("GET", "/api/v1/value/settings"); }
  /** Owner: save the four values (paise or cents; `minutesPerAnswer` 0 to 120) and the e-mail switch. */
  updateValueSettings({ leadValueMinor, handoffValueMinor, staffHourMinor, minutesPerAnswer, emailEnabled = true, industryDefaults }) {
    const json = {
      lead_value_minor: leadValueMinor,
      handoff_value_minor: handoffValueMinor,
      staff_hour_minor: staffHourMinor,
      minutes_per_answer: minutesPerAnswer,
      email_enabled: emailEnabled,
    };
    if (industryDefaults != null) json.industry_defaults = industryDefaults;
    return this._req("PUT", "/api/v1/value/settings", { json });
  }
  /** A month's numbers with the estimated value and its formula (`month` is `YYYY-MM`, default this month so far), as "json" (default) or "html" (the printable page, text). */
  valueReport(month, { format = "json" } = {}) {
    const params = month ? { month } : {};
    if (format === "json") return this._req("GET", "/api/v1/value/report", { params });
    return this._statementText("GET", "/api/v1/value/report", format, params);
  }
  /** The frozen months, newest first: `{items: [{month, total_minor, currency}]}`. */
  valueReports() { return this._req("GET", "/api/v1/value/reports"); }
  /** Admin: one business's value report, as its owner sees it. */
  adminValueReport(tenantId, month, { format = "json" } = {}) {
    const params = month ? { month } : {};
    if (format === "json") return this._req("GET", `/api/v1/admin/tenants/${tenantId}/value-report`, { params });
    return this._statementText("GET", `/api/v1/admin/tenants/${tenantId}/value-report`, format, params);
  }
  // /TV4 value report

  // BIZ3 home and articles
  /** The business dashboard's summary: assistant, website, today's numbers. Workspace sign-in or tenant key. params: `{ corpus_id }` (default: the workspace's main knowledge base). */
  home(params = {}) { return this._req("GET", "/api/v1/home", { params }); }
  /** Write an article (`kind: "article"`) or an FAQ answer (`kind: "faq"`, the title is the question). The assistant learns it in a moment; it counts as one document. */
  createArticle(corpusId, { kind, title, body }) {
    return this._req("POST", `/api/v1/corpora/${corpusId}/articles`, { json: { kind, title, body } });
  }
  /** An article or FAQ entry with its text as written (by document id or article id). */
  getArticle(corpusId, documentId) { return this._req("GET", `/api/v1/corpora/${corpusId}/articles/${documentId}`); }
  /** Change an article's text; the assistant learns the new version. `kind` cannot change. */
  updateArticle(corpusId, documentId, { kind, title, body }) {
    return this._req("PUT", `/api/v1/corpora/${corpusId}/articles/${documentId}`, { json: { kind, title, body } });
  }
  // /BIZ3 home and articles

  // BIZ1 website learning
  /** What the assistant learns from the business's own website: settings, the plan's limits, the sites and how far each is, allowed sites not read yet. */
  website(corpusId) { return this._req("GET", `/api/v1/corpora/${corpusId}/website`); }
  /** Owner: switch learning on or off and choose paths to leave out (`/blog/*`). Only the fields you pass are changed. */
  updateWebsite(corpusId, { enabled, exclude_patterns, refresh_mode, adaptive } = {}) {
    const json = {};
    if (enabled !== undefined) json.enabled = enabled;
    if (exclude_patterns !== undefined) json.exclude_patterns = exclude_patterns;
    if (refresh_mode !== undefined) json.refresh_mode = refresh_mode; // BIZ2
    if (adaptive !== undefined) json.adaptive = adaptive; // BIZ2
    return this._req("PATCH", `/api/v1/corpora/${corpusId}/website`, { json });
  }
  /** Start reading one of the knowledge base's allowed sites (an origin of one of its active widget keys). Returns the site (202). */
  learnWebsite(corpusId, origin) { return this._req("POST", `/api/v1/corpora/${corpusId}/website/learn`, { json: { origin } }); }
  /** Read every site again. Once every 10 minutes (429 `website_reread_too_soon`). */
  rereadWebsite(corpusId) { return this._req("POST", `/api/v1/corpora/${corpusId}/website/reread`); }
  /** Owner: stop learning a site; its pages leave the answers (they keep their history). */
  removeWebsiteSource(corpusId, sourceId) { return this._req("DELETE", `/api/v1/corpora/${corpusId}/website/sources/${sourceId}`); }
  /** Every page found on the site and what became of it. params: status (learned, waiting, skipped, excluded, failed, removed), source_id, q, offset, limit (<= 100). */
  websitePages(corpusId, params = {}) { return this._req("GET", `/api/v1/corpora/${corpusId}/website/pages`, { params }); }
  /** Read one page again now (202). */
  rereadWebsitePage(corpusId, pageId) { return this._req("POST", `/api/v1/corpora/${corpusId}/website/pages/${pageId}/reread`); }
  /** Owner: leave a page out of the answers (`excluded: true`) or put it back. */
  updateWebsitePage(corpusId, pageId, excluded) {
    return this._req("PATCH", `/api/v1/corpora/${corpusId}/website/pages/${pageId}`, { json: { excluded } });
  }
  // /BIZ1 website learning
  // BIZ2 freshness
  /** Owner: make the push link a website calls when a page changes. Returns `{ key, push_url, hint }` once; a new one replaces (and stops) the old one. */
  createLearnKey(corpusId) { return this._req("POST", `/api/v1/corpora/${corpusId}/website/learn-key`); }
  /** Owner: remove the push link; it stops working at once. */
  deleteLearnKey(corpusId) { return this._req("DELETE", `/api/v1/corpora/${corpusId}/website/learn-key`); }
  // /BIZ2 freshness
  // BIZ4 avatar
  /**
   * Owner: upload the assistant's picture (PNG, JPEG or WebP, at least 64 px). `file` is a Blob/File
   * or {name, data}. The engine re-saves it as a 256 px PNG and returns `{ avatar_url }`
   * (`/api/v1/public/avatars/<id>.png`): put that in the assistant's `identity.avatar_url` and publish.
   */
  uploadAvatar(corpusId, file) {
    const form = new FormData();
    const blob = file instanceof Blob ? file : new Blob([file.data]);
    form.append("file", blob, file.name || "avatar");
    return this._req("POST", `/api/v1/corpora/${corpusId}/assistant/avatar`, { body: form });
  }
  // /BIZ4 avatar

  // engine settings and branding
  /** Admin: the sign-up mode and branding, with who changed them last. */
  getSettings() { return this._req("GET", "/api/v1/admin/settings"); }
  /** Admin: {signup_mode, branding: {...}}. Only what you send changes; null resets to the default. */
  updateSettings(changes) { return this._req("PATCH", "/api/v1/admin/settings", { json: changes }); }
  /** Product name, logo, colour, contact text, sign-up mode. Needs no key. */
  publicBranding() { return this._req("GET", "/api/v1/public/branding"); }

  // e-mail (SMTP)
  /** Admin: the e-mail provider in use and the SMTP settings (never the password). */
  smtpStatus() { return this._req("GET", "/api/v1/admin/smtp"); }
  /** Admin: send a test e-mail now. 503 when e-mail is not configured, 502 when SMTP refuses it. */
  sendTestEmail(to) { return this._req("POST", "/api/v1/admin/smtp/test", { json: { to } }); }
}
