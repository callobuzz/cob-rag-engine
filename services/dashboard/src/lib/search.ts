import { z } from "zod";

/** `?kb=<uuid>` on KB-scoped routes; an invalid value is dropped, never an error page. */
export const kbSearchSchema = z.object({
	kb: z.uuid().optional().catch(undefined),
});

/** `/admin/customers?status=pending&q=&page=` (the status link is in A4's e-mail; D7 adds search and paging). */
export const customersSearchSchema = z.object({
	status: z.enum(["pending", "active", "suspended"]).optional().catch(undefined),
	q: z.string().max(255).optional().catch(undefined),
	page: z.coerce.number().int().min(1).optional().catch(undefined),
	/** `add=1` opens the "Add a customer" dialog (the empty-state links and the checklist). */
	add: z.coerce.number().int().optional().catch(undefined),
});

export const CUSTOMER_TABS = [
	"overview",
	"kbs",
	"team",
	"keys",
	"billing",
	"limits",
	"value", // TV4
	"settings",
	"danger",
] as const; // BIL2: billing, PIP4: limits
export type CustomerTab = (typeof CUSTOMER_TABS)[number];

/** `/admin/customers/$tenantId?tab=` (D7). */
export const customerDetailSearchSchema = z.object({
	tab: z.enum(CUSTOMER_TABS).optional().catch(undefined),
});

// BIL2
export const BILLING_TABS = [
	"accounts",
	"pricing",
	"add-ons",
	"pnl",
	"payments",
	"tax",
	"invoices",
	"service-orders", // P5 plans
] as const; // BIL5 add-ons, BIL6 P&L, P1 payments + tax, P2 invoices
export type BillingTab = (typeof BILLING_TABS)[number];

/** `/admin/billing?tab=` (BIL2: Accounts and Pricing; later epics add tabs). */
export const billingSearchSchema = z.object({
	tab: z.enum(BILLING_TABS).optional().catch(undefined),
});
// /BIL2

// P1 payments
/** `/pay?purpose=top_up&amount=100000&add_on=<uuid>`: what is being paid (amount in paise). */
export const paySearchSchema = z.object({
	purpose: z.enum(["top_up", "plan", "add_on"]).optional().catch(undefined),
	amount: z.coerce.number().int().min(1).optional().catch(undefined),
	add_on: z.uuid().optional().catch(undefined),
});
// /P1 payments

/** `/team?invite=1` opens the invite dialog (palette "Invite a teammate"). */
export const teamSearchSchema = z.object({
	invite: z.coerce.number().int().optional().catch(undefined),
});

/** `/admin/audit?tenant_id=&action=` (D7's "History" link; D10 builds the page). */
export const auditSearchSchema = z.object({
	tenant_id: z.uuid().optional().catch(undefined),
	action: z.string().max(100).optional().catch(undefined),
});

/** `/login?next=&reason=` — `next` is checked by safeNext() before use. */
export const loginSearchSchema = z.object({
	next: z.string().optional().catch(undefined),
	reason: z.string().optional().catch(undefined),
	email: z.string().optional().catch(undefined),
});

export const reasonSearchSchema = z.object({
	reason: z.string().optional().catch(undefined),
});

/** `/kbs?page=&tenant=` (D3): 1-based page; admin customer filter. */
export const kbsSearchSchema = z.object({
	page: z.coerce.number().int().min(1).optional().catch(undefined),
	tenant: z.uuid().optional().catch(undefined),
	/** `new=1` opens the "New knowledge base" dialog (the checklist step). */
	new: z.coerce.number().int().optional().catch(undefined),
});

export const KB_TABS = ["overview", "model", "assistant", "retrieval", "embedding", "key", "danger"] as const;
export type KbTab = (typeof KB_TABS)[number];

export const ASSISTANT_SECTION_VALUES = [
	"overview",
	"identity",
	"voice",
	"language",
	"scope",
	"rules",
	"conversation",
	"leads",
	"handoff",
	"advanced",
	"versions",
	"tests",
	"preview",
] as const;

/** `/kbs/$kbId?tab=&section=` (D3, C3). `tab=prompt` (before the Assistant tab existed) opens Assistant. */
export const kbDetailSearchSchema = z.object({
	tab: z
		.preprocess((v) => (v === "prompt" ? "assistant" : v), z.enum(KB_TABS))
		.optional()
		.catch(undefined),
	section: z.enum(ASSISTANT_SECTION_VALUES).optional().catch(undefined),
});

/** `/kbs/$kbId/setup?sid=` (C8): the guided setup session being resumed. */
export const setupSearchSchema = z.object({
	sid: z.uuid().optional().catch(undefined),
});

/** `/migrations?id=` (D3 links a new migration; D10 builds the page). */
export const migrationsSearchSchema = z.object({
	id: z.uuid().optional().catch(undefined),
});

/** `/documents?kb=&q=&page=` (D4). */
export const documentsSearchSchema = kbSearchSchema.extend({
	q: z.string().max(200).optional().catch(undefined),
	page: z.coerce.number().int().min(1).optional().catch(undefined),
});

export const DOCUMENT_TABS = ["passages", "versions"] as const;
export type DocumentTab = (typeof DOCUMENT_TABS)[number];

/** `/documents/$documentId?kb=&tab=&version=&chunkPage=` (D4). */
export const documentDetailSearchSchema = kbSearchSchema.extend({
	tab: z.enum(DOCUMENT_TABS).optional().catch(undefined),
	version: z.uuid().optional().catch(undefined),
	chunkPage: z.coerce.number().int().min(1).optional().catch(undefined),
});

export const ADD_TABS = ["upload", "records", "jobs"] as const;
export type AddTab = (typeof ADD_TABS)[number];

export const JOB_STATUSES = [
	"queued",
	"uploading",
	"parsing",
	"chunking",
	"enriching",
	"embedding",
	"storing",
	"completed",
	"failed",
	"pending_review",
	"expired",
	"skipped",
] as const;
export type JobStatus = (typeof JOB_STATUSES)[number];

/** `/add?kb=&tab=&status=&page=` (D4; status and page filter the Jobs tab). */
export const addSearchSchema = kbSearchSchema.extend({
	tab: z.enum(ADD_TABS).optional().catch(undefined),
	status: z.enum(JOB_STATUSES).optional().catch(undefined),
	page: z.coerce.number().int().min(1).optional().catch(undefined),
});

/** `/playground?kb=&q=` (D5): `q` pre-fills the question (e.g. a link from Insights). */
export const playgroundSearchSchema = kbSearchSchema.extend({
	q: z.string().max(2000).optional().catch(undefined),
});

/** `/conversations?kb=&id=` (D5): the open conversation. */
export const conversationsSearchSchema = kbSearchSchema.extend({
	id: z.uuid().optional().catch(undefined),
});

/** `/widget?kb=&key=` (D6): `key` is the selected widget key's id (preview + appearance). */
export const widgetSearchSchema = kbSearchSchema.extend({
	key: z.uuid().optional().catch(undefined),
});

export const AGENT_CLIENTS = ["claude-code", "claude-desktop", "cursor", "vscode", "n8n"] as const;
export type AgentClient = (typeof AGENT_CLIENTS)[number];

/** `/agents?kb=&client=` (D6): the open client tab. */
export const agentsSearchSchema = kbSearchSchema.extend({
	client: z.enum(AGENT_CLIENTS).optional().catch(undefined),
});

/** `/?range=today|7d|30d` (D2 Overview; default 7 days). */
export const overviewSearchSchema = z.object({
	range: z.enum(["today", "7d", "30d"]).optional().catch(undefined),
});

export const USAGE_GROUPS = ["day", "corpus", "source", "tenant", "model"] as const;

/** `/usage?range=&from=&to=&kb=&tenant=&group=` (D2). Dates are UTC days (YYYY-MM-DD). */
export const usageSearchSchema = z.object({
	range: z.enum(["7d", "30d", "90d", "month", "lastMonth", "custom"]).optional().catch(undefined),
	from: z.iso.date().optional().catch(undefined),
	to: z.iso.date().optional().catch(undefined),
	kb: z.uuid().optional().catch(undefined),
	tenant: z.uuid().optional().catch(undefined),
	group: z.enum(USAGE_GROUPS).optional().catch(undefined),
	/** `own=0` leaves the operator's own workspace out (`exclude_operator=true`); default: shown, labelled. */
	own: z.coerce.number().int().optional().catch(undefined),
});

export const LEAD_STATUSES = ["new", "contacted", "won", "lost", "spam"] as const;
export type LeadStatusFilter = (typeof LEAD_STATUSES)[number];
export const LEAD_QUALIFICATIONS = ["qualified", "partial", "unqualified"] as const;
export type LeadQualificationFilter = (typeof LEAD_QUALIFICATIONS)[number];

/** `/leads?kb=&status=&qualification=&q=&from=&to=&url=&page=&lead=` (C4): `lead` opens that lead's drawer. */
export const leadsSearchSchema = kbSearchSchema.extend({
	status: z.enum(LEAD_STATUSES).optional().catch(undefined),
	/** The owner's own bucket for a lead (C9), the same words as the API's `qualification`. */
	qualification: z.enum(LEAD_QUALIFICATIONS).optional().catch(undefined),
	/** A phone number or e-mail, exact. */
	q: z.string().max(254).optional().catch(undefined),
	from: z.iso.date().optional().catch(undefined),
	to: z.iso.date().optional().catch(undefined),
	/** Part of the page address the visitor was on. */
	url: z.string().max(200).optional().catch(undefined),
	page: z.coerce.number().int().min(1).optional().catch(undefined),
	lead: z.uuid().optional().catch(undefined),
	/** The open tab, and on the Handoffs tab (C5) the open handoff and its filters. */
	tab: z.enum(["leads", "handoffs"]).optional().catch(undefined),
	handoff: z.uuid().optional().catch(undefined),
	hstatus: z.enum(["new", "handled"]).optional().catch(undefined),
	hreason: z.string().max(32).optional().catch(undefined),
	hchannel: z.string().max(20).optional().catch(undefined),
});
export type LeadsSearch = z.infer<typeof leadsSearchSchema>;

// BIZ3
export const KNOWLEDGE_TABS = ["website", "articles"] as const;
export type KnowledgeTab = (typeof KNOWLEDGE_TABS)[number];

/** `/knowledge?tab=website|articles` and `new=article|faq` (opens the editor; the Home quick action). */
export const knowledgeSearchSchema = z.object({
	tab: z.enum(KNOWLEDGE_TABS).optional().catch(undefined),
	new: z.enum(["article", "faq"]).optional().catch(undefined),
	question: z.string().max(300).optional().catch(undefined), // TV4: a question to start the editor with
});
// /BIZ3

// TV4 value
/** `/value?month=YYYY-MM` (a closed month, or this one) and `edit=1` (opens the owner's values). */
export const valueSearchSchema = z.object({
	month: z
		.string()
		.regex(/^\d{4}-(0[1-9]|1[0-2])$/)
		.optional()
		.catch(undefined),
	edit: z.coerce.number().int().min(1).max(1).optional().catch(undefined),
});
// /TV4 value

// TV3 support
/** `/support?request=<id>` opens a thread, `new=1` the form, `page=/path` is the page it was written on. */
export const supportSearchSchema = z.object({
	request: z.string().uuid().optional().catch(undefined),
	new: z.coerce.number().int().min(1).max(1).optional().catch(undefined),
	page: z.string().max(300).regex(/^\//).optional().catch(undefined),
	status: z.enum(["open", "answered", "waiting", "closed"]).optional().catch(undefined),
});

/** `/admin/support?request=<id>` opens a thread; `status` and `q` filter the inbox. */
export const adminSupportSearchSchema = z.object({
	request: z.string().uuid().optional().catch(undefined),
	status: z.enum(["open", "answered", "waiting", "closed"]).optional().catch(undefined),
	q: z.string().max(100).optional().catch(undefined),
});
// /TV3 support

// BIZ4
/** `/welcome?step=1|2|3|4`: the first-run steps (website, brochures, install, try; TV5); an invalid value is dropped, never an error page. */
export const welcomeSearchSchema = z.object({
	step: z.coerce.number().int().min(1).max(4).optional().catch(undefined),
});
// /BIZ4

// LP4 landing
export const LANDING_TABS = ["home", "look", "pricing", "pages", "menu", "languages", "versions"] as const;

/** `/admin/landing?tab=` (a link from the help pages can open a tab). */
export const landingSearchSchema = z.object({
	tab: z.enum(LANDING_TABS).optional().catch(undefined),
});
// /LP4 landing
