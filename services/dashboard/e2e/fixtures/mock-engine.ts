import { randomUUID } from "node:crypto";
import type { BrowserContext, Page, Route } from "@playwright/test";
import { type AccountsState, createAccountsState, handleAccountsRoute } from "./mock-accounts";
import { type AssistantState, assistantEnabled, createAssistantState, handleAssistantRoute } from "./mock-assistant";
import { billingStateFor, handleBillingRoute } from "./mock-billing"; // BIL2
import { handleCobRoute } from "./mock-cob";
import { handleCobPublicRoute } from "./mock-cob-public";
import { handleDocsRoute } from "./mock-docs";
import { createDocState, type DocState, filesOf, handleDocumentRoute, parseMultipart } from "./mock-documents";
import { createHomeState, type HomeState, handleHomeRoute } from "./mock-home"; // BIZ3
import { createInvoicesState, handleInvoicesRoute, type InvoicesMock } from "./mock-invoices"; // P2 invoices
import { createKbState, handleKbRoute, type KbState } from "./mock-kbs";
import { createKeysState, handleKeysRoute, type KeysState } from "./mock-keys";
import { createLandingState, handleLandingRoute, type LandingMock } from "./mock-landing"; // LP4 landing
import { createLegalState, handleLegalRoute, type LegalMock } from "./mock-legal"; // P3 legal
import {
	createOnboardingState,
	handleOnboardingRoute,
	handleSuggestions,
	homeConnected,
	homeOnboarding,
	type OnboardingMock,
} from "./mock-onboarding"; // BIZ4
import { handleOverviewRoute } from "./mock-overview";
import { createPaymentsState, handlePaymentsRoute, type PaymentsMock } from "./mock-payments"; // P1 payments
import { handlePipelinesRoute } from "./mock-pipelines"; // PIP4
import { createPlanChoiceState, handlePlanChoiceRoute, type PlanChoiceState } from "./mock-plan-choice"; // P6 plan choice
import { handleProviderRoute } from "./mock-providers"; // BIL1
import { createQueryState, handleQueryRoute, type QueryState } from "./mock-query";
import { installMockScraper } from "./mock-scraper";
import { handleSetupRoute } from "./mock-setup";
import { createShopState, handleShopRoute, type ShopMock } from "./mock-shop"; // P5 plans
import { createSupportState, handleSupportRoute, type SupportMock } from "./mock-support"; // TV3
import { createValueState, handleValueRoute, homeValue, type ValueMock } from "./mock-value"; // TV4
import { createWebsiteState, handleWebsiteRoute, type WebsiteState } from "./mock-website"; // BIZ1
import { createWhatsappState, handleWhatsappRoute, type WhatsappMock } from "./mock-whatsapp"; // TV6
import {
	createWidgetState,
	handleWidgetPublic,
	handleWidgetRoute,
	type WidgetState,
	widgetKeysUsed,
} from "./mock-widget";

/**
 * A stateful fake engine for Playwright, served through `context.route()` (no network, no Docker).
 * It follows the accounts contracts (A3/A4/A5) closely enough to exercise the dashboard's real
 * behaviour — including A3's zero-grace refresh-token reuse detection, so the two-tab test fails
 * if refreshes are not serialised. Used until A3–A5 are merged; `auth.spec.ts` then runs the same
 * journeys against the real stack.
 *
 * Indian demo data only.
 */

export const MOCK_ENGINE = "http://engine.mock";

type Status = "pending" | "active" | "suspended";
export interface Tenant {
	id: string;
	name: string;
	status: Status;
	/** The operator's own workspace (tenants.is_operator): at most one, owned by the super admin. */
	isOperator?: boolean;
}
interface User {
	id: string;
	email: string;
	name: string;
	password: string;
	superadmin: boolean;
	memberships: { tenantId: string; role: "owner" | "member" }[];
	verified: boolean;
	lastLoginAt?: string;
}
interface Session {
	id: string;
	userId: string;
	tenantId: string | null;
	current: string;
	previous: string | null;
	revoked: boolean;
	userAgent?: string;
	createdAt?: string;
	lastUsedAt?: string;
}

export interface World {
	branding: Record<string, unknown>;
	tenants: Tenant[];
	users: User[];
	sessions: Map<string, Session>;
	access: Map<string, { sid: string; expired: boolean }>;
	keys: Map<string, { kind: "admin" | "tenant" | "corpus"; tenantId?: string }>;
	invitations: Map<
		string,
		{
			tenantId: string;
			email: string;
			role: "owner" | "member";
			used: boolean;
			id?: string;
			invitedBy?: string;
			createdAt?: string;
			expiresAt?: string;
		}
	>;
	contentPortal: boolean;
	/** Knowledge bases, plans and catalogs (D3, e2e/fixtures/mock-kbs.ts). */
	kbs: KbState;
	/** Documents, versions, uploads, jobs and records (D4, e2e/fixtures/mock-documents.ts). */
	docs: DocState;
	/** The assistant of every knowledge base: versions, draft, templates (C3, e2e/fixtures/mock-assistant.ts). */
	assistant: AssistantState;
	/** Answers, feedback, conversations and A6 question counts (D5, e2e/fixtures/mock-query.ts). */
	query: QueryState;
	/** Widget keys and the public widget endpoints (D6, e2e/fixtures/mock-widget.ts). */
	widget: WidgetState;
	/** Named API keys of knowledge bases and customers (e2e/fixtures/mock-keys.ts). */
	apiKeys: KeysState;
	// BIZ1
	/** Website learning: sites, pages, settings (e2e/fixtures/mock-website.ts). */
	website: WebsiteState;
	// /BIZ1
	// BIZ3
	/** What a business's plan shows in the dashboard (e2e/fixtures/mock-home.ts). */
	home: HomeState;
	// /BIZ3
	// TV4 value
	/** The owner's value settings and the value report (e2e/fixtures/mock-value.ts). */
	value: ValueMock;
	// /TV4 value
	// P3 legal
	/** The legal pages: drafts, published versions and the details (e2e/fixtures/mock-legal.ts). */
	legal: LegalMock;
	// LP4 landing
	/** The public website editor: draft, versions, assets, the Ask link (e2e/fixtures/mock-landing.ts). */
	landing: LandingMock;
	// /LP4 landing
	// /P3 legal
	// P1 payments
	/** Online payments: tax details, orders, payments, settings (e2e/fixtures/mock-payments.ts). */
	payments: PaymentsMock;
	// /P1 payments
	// P2 invoices
	/** Tax invoices and credit notes: owner list, operator list, PDF, CSV, resend (e2e/fixtures/mock-invoices.ts). */
	invoices: InvoicesMock;
	// /P2 invoices
	// P5 plans
	/** The add-on shop and service orders: owner shop, buying, orders, operator moves (e2e/fixtures/mock-shop.ts). */
	shop: ShopMock;
	// /P5 plans
	/** Choosing and changing a plan: the owner list, a move, calling one off (e2e/fixtures/mock-plan-choice.ts). */
	planChoice: PlanChoiceState; // P6 plan choice
	// TV3 support
	/** Support requests from businesses and the provider's replies (e2e/fixtures/mock-support.ts). */
	support: SupportMock;
	// /TV3 support
	/** The WhatsApp number, chats and replies (e2e/fixtures/mock-whatsapp.ts). */
	whatsapp: WhatsappMock;
	// BIZ4
	/** The first-run steps of every workspace (e2e/fixtures/mock-onboarding.ts). */
	onboarding: OnboardingMock;
	// /BIZ4
	/** Plans, tenant lifecycle data, teams' mail and write log (D7, e2e/fixtures/mock-accounts.ts). */
	accounts: AccountsState;
	/** Counters the specs assert on. */
	stats: { refresh: number; reuse: number; logins: number };
	/** Mark every issued access token expired (the next Bearer call gets 401 token_expired). */
	expireAccessTokens: () => void;
	activate: (tenantName: string) => void;
	/** An approval-mode sign-up that verified its e-mail: a pending tenant and its owner (A4). */
	signUp: (business: string, name: string, email: string, password?: string) => void;
}

const SIGNUP_CODE = "482913";
const RESET_CODE = "654321";

let seq = 0;
const hex = () => {
	seq++;
	return `${Date.now().toString(16)}${seq.toString(16)}`.padEnd(32, "0").slice(0, 32);
};
// Random, not derived from hex(): slicing hex() dropped a digit of the counter, so two users
// created in the same millisecond could get the same id once a worker had run a few tests.
const uuid = () => randomUUID();

export const ACCOUNTS = {
	superadmin: { email: "sv@punedigital.in", password: "Pune-digital 2026 admin" },
	owner: { email: "priya@shree-labs.in", password: "Shree labs owner 2026" },
	member: { email: "rohan@shree-labs.in", password: "Shree labs member 2026" },
	adminKey: "mock-admin-key-not-a-secret",
	tenantKey: "tk_mock_shree_labs",
	/** Owner of Mehta Opticals (D7 suspend journey). */
	mehta: { email: "rakesh@mehtaopticals.in", password: "Mehta opticals owner 2026" },
	corpusKey: "ck_mock_patient_help",
};

export function createWorld(
	overrides: {
		signupMode?: "open" | "approval" | "closed";
		contentPortal?: boolean;
		/** Shree Labs' plan cap on knowledge bases (default 3; null = unlimited). */
		kbLimit?: number | null;
		/** Shree Labs' plan cap on active documents (A6 max_documents; default unlimited). */
		docLimit?: number;
		/** Shree Labs' monthly questions (A6 monthly_questions; default unlimited). */
		questionLimit?: number;
		/** Shree Labs' plan cap on widget keys (A6 max_widget_keys; default 3; null = unlimited). */
		widgetKeyLimit?: number | null;
	} = {},
): World {
	const shree: Tenant = { id: uuid(), name: "Shree Labs", status: "active" };
	const rangoli: Tenant = { id: uuid(), name: "Rangoli Sarees", status: "active" };
	// "Vidya Classes" is the customer D7's e2e adds by hand, so the third seeded tenant is Sai Tutorials.
	const vidya: Tenant = { id: uuid(), name: "Sai Tutorials", status: "active" };
	const mehta: Tenant = { id: uuid(), name: "Mehta Opticals", status: "active" };
	const kothrud: Tenant = { id: uuid(), name: "Kothrud Physio Clinic", status: "pending" };
	// The operator's own workspace, named after the product; the super admin is its owner.
	const operator: Tenant = { id: uuid(), name: "Sahayak AI", status: "active", isOperator: true };
	const mode = overrides.signupMode ?? "approval";
	const world: World = {
		branding: {
			product_name: "Sahayak AI",
			logo_url: null,
			primary_color: "#0b7a5f",
			support_email: "support@sahayak.ai",
			contact_text:
				"Sahayak AI is set up personally for each clinic, school and store. Message us and we'll set you up the same day.",
			contact_url: "https://wa.me/919822012345",
			signup_mode: mode,
			powered_by: { text: "cob-rag-engine by Callobuzz", url: null },
			accounts_enabled: true,
			password_policy: { min_length: 10, max_length: 128 },
			signup_available: mode !== "closed",
			special_use_emails_allowed: false,
			cob_public: true,
		},
		tenants: [operator, shree, rangoli, vidya, mehta, kothrud],
		users: [
			{
				id: uuid(),
				email: ACCOUNTS.superadmin.email,
				name: "SV",
				password: ACCOUNTS.superadmin.password,
				superadmin: true,
				memberships: [{ tenantId: operator.id, role: "owner" }],
				verified: true,
			},
			{
				id: uuid(),
				email: ACCOUNTS.owner.email,
				name: "Priya Kulkarni",
				password: ACCOUNTS.owner.password,
				superadmin: false,
				memberships: [{ tenantId: shree.id, role: "owner" }],
				verified: true,
			},
			{
				id: uuid(),
				email: ACCOUNTS.member.email,
				name: "Rohan Deshmukh",
				password: ACCOUNTS.member.password,
				superadmin: false,
				memberships: [{ tenantId: shree.id, role: "member" }],
				verified: true,
			},
			{
				id: uuid(),
				email: ACCOUNTS.mehta.email,
				name: "Rakesh Mehta",
				password: ACCOUNTS.mehta.password,
				superadmin: false,
				memberships: [{ tenantId: mehta.id, role: "owner" }],
				verified: true,
			},
			{
				id: uuid(),
				email: "neha@kothrudphysio.in",
				name: "Dr. Neha Patil",
				password: "Kothrud physio owner 2026",
				superadmin: false,
				memberships: [{ tenantId: kothrud.id, role: "owner" }],
				verified: true,
			},
		],
		sessions: new Map(),
		access: new Map(),
		keys: new Map([
			[ACCOUNTS.adminKey, { kind: "admin" }],
			[ACCOUNTS.tenantKey, { kind: "tenant", tenantId: shree.id }],
			[ACCOUNTS.corpusKey, { kind: "corpus", tenantId: shree.id }],
		]),
		invitations: new Map([
			["inv_mock_new_member", { tenantId: shree.id, email: "asha@shree-labs.in", role: "member", used: false }],
		]),
		contentPortal: overrides.contentPortal ?? false,
		kbs: createKbState([shree, rangoli, vidya]),
		docs: createDocState(
			PATIENT_HELP,
			(id) => world.kbs.items.find((k) => k.id === id)?.tenant_id,
			(id) => syncCounts(world, id),
		),
		assistant: createAssistantState(createKbState([shree, rangoli, vidya]).items),
		query: createQueryState(),
		widget: createWidgetState(),
		apiKeys: createKeysState(shree.id, PATIENT_HELP),
		website: createWebsiteState(), // BIZ1
		home: createHomeState(), // BIZ3
		value: createValueState(), // TV4
		support: createSupportState(), // TV3
		legal: createLegalState(), // P3 legal
		landing: createLandingState(), // LP4 landing
		payments: createPaymentsState(), // P1 payments
		invoices: createInvoicesState(), // P2 invoices
		shop: createShopState(), // P5 plans
		planChoice: createPlanChoiceState(), // P6 plan choice
		whatsapp: createWhatsappState(), // TV6
		onboarding: createOnboardingState(), // BIZ4
		accounts: createAccountsState([operator, shree, rangoli, vidya, mehta, kothrud]),
		stats: { refresh: 0, reuse: 0, logins: 0 },
		expireAccessTokens: () => {
			for (const v of world.access.values()) v.expired = true;
		},
		activate: (name) => {
			const t = world.tenants.find((x) => x.name === name);
			if (t) t.status = "active";
		},
		signUp: (business, name, email, password = "Signed up owner 2026") => {
			const t: Tenant = { id: uuid(), name: business, status: "pending" };
			world.tenants.push(t);
			const u: User = {
				id: uuid(),
				email,
				name,
				password,
				superadmin: false,
				memberships: [{ tenantId: t.id, role: "owner" }],
				verified: true,
			};
			world.users.push(u);
			const starter = world.accounts.plans.find((p) => p.is_default);
			world.accounts.meta.set(t.id, {
				description: null,
				planId: starter?.id ?? "",
				maxCorpora: null,
				note: null,
				createdAt: new Date().toISOString(),
				activatedAt: null,
				createdByUserId: u.id,
			});
		},
	};
	if (overrides.docLimit !== undefined) world.docs.limits.set(shree.id, overrides.docLimit);
	if (overrides.questionLimit !== undefined) world.query.limits.set(shree.id, overrides.questionLimit);
	if (overrides.widgetKeyLimit !== undefined) world.widget.limits.set(shree.id, overrides.widgetKeyLimit);
	if (overrides.kbLimit !== undefined) {
		world.kbs.plans.set(shree.id, { name: overrides.kbLimit === 1 ? "Starter" : "Pro", maxKbs: overrides.kbLimit });
	}
	return world;
}

const PATIENT_HELP = "7b221003-0000-4000-8000-000000000001";

/** Keep the KB list's document and passage counts in step after uploads and deletes. */
function syncCounts(w: World, corpusId: string) {
	const k = w.kbs.items.find((x) => x.id === corpusId);
	if (!k) return;
	const active = w.docs.docs
		.filter((d) => d.corpusId === corpusId)
		.map((d) => d.versions.find((v) => v.status === "active"))
		.filter((v) => !!v);
	k.document_count = active.length;
	k.chunk_count = active.reduce((a, v) => a + v.chunks.length, 0);
	k.last_activity_at = new Date().toISOString();
}

const CORS = {
	"access-control-allow-origin": "*",
	"access-control-allow-headers":
		"authorization, x-api-key, x-widget-key, content-type, x-llm-provider, x-llm-model, x-llm-api-key, x-llm-base-url",
	"access-control-allow-methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
	"access-control-expose-headers": "retry-after",
};

function json(route: Route, status: number, body: unknown, headers: Record<string, string> = {}) {
	return route.fulfill({
		status,
		headers: { ...CORS, "content-type": "application/json", ...headers },
		body: JSON.stringify(body),
	});
}
const err = (route: Route, status: number, code: string, extra: Record<string, unknown> = {}) =>
	json(route, status, { detail: code.replace(/_/g, " "), code, ...extra });

function tenantOut(w: World, u: User, tenantId: string | null) {
	if (!tenantId) return null;
	const t = w.tenants.find((x) => x.id === tenantId);
	const m = u.memberships.find((x) => x.tenantId === tenantId);
	return t && m ? { id: t.id, name: t.name, role: m.role, status: t.status, is_operator: !!t.isOperator } : null;
}

/** A super admin's session has a workspace only after switch-tenant into the operator workspace. */
function principalParts(w: World, u: User, tenantId: string | null) {
	return {
		user: { id: u.id, email: u.email, name: u.name, is_superadmin: u.superadmin },
		tenant: tenantOut(w, u, tenantId),
		tenants: u.memberships.map((m) => tenantOut(w, u, m.tenantId)).filter(Boolean),
	};
}

function issue(w: World, s: Session) {
	const at = `at_${hex()}`;
	w.access.set(at, { sid: s.id, expired: false });
	return at;
}

function tokenResponse(w: World, u: User, s: Session) {
	return {
		access_token: issue(w, s),
		token_type: "bearer",
		expires_in: 900,
		refresh_token: s.current,
		refresh_expires_at: "2026-10-30T10:00:00Z",
		...principalParts(w, u, s.tenantId),
	};
}

function newSession(w: World, u: User, userAgent?: string): Session {
	const id = hex();
	u.lastLoginAt = new Date().toISOString();
	const s: Session = {
		id,
		userId: u.id,
		tenantId: u.superadmin ? null : (u.memberships[0]?.tenantId ?? null),
		current: `rt_${id}_${hex()}`,
		previous: null,
		revoked: false,
		userAgent,
		createdAt: new Date().toISOString(),
		lastUsedAt: new Date().toISOString(),
	};
	w.sessions.set(id, s);
	return s;
}

type Who =
	| { kind: "session"; user: User; session: Session }
	| { kind: "key"; key: { kind: "admin" | "tenant" | "corpus"; tenantId?: string } }
	| { kind: "none" }
	| { kind: "error"; status: number; code: string };

function resolve(w: World, headers: Record<string, string>): Who {
	const key = headers["x-api-key"];
	if (key) {
		const k = w.keys.get(key);
		if (!k) return { kind: "error", status: 401, code: "invalid_api_key" };
		const t = k.tenantId ? w.tenants.find((x) => x.id === k.tenantId) : null;
		if (t && t.status !== "active") return { kind: "error", status: 403, code: `workspace_not_active:${t.status}` };
		return { kind: "key", key: k };
	}
	const auth = headers.authorization;
	if (auth?.toLowerCase().startsWith("bearer ")) {
		const a = w.access.get(auth.slice(7));
		if (!a) return { kind: "error", status: 401, code: "invalid_token" };
		if (a.expired) return { kind: "error", status: 401, code: "token_expired" };
		const s = w.sessions.get(a.sid);
		if (!s || s.revoked) return { kind: "error", status: 401, code: "session_revoked" };
		const u = w.users.find((x) => x.id === s.userId);
		if (!u) return { kind: "error", status: 401, code: "session_revoked" };
		s.lastUsedAt = new Date().toISOString();
		return { kind: "session", user: u, session: s };
	}
	return { kind: "none" };
}

export async function installMockEngine(target: Page | BrowserContext, world: World): Promise<void> {
	// The crawler (D8) at the scraperUrl below.
	await installMockScraper(target, world);
	await target.route("**/config.js", (route) =>
		route.fulfill({
			status: 200,
			contentType: "application/javascript",
			body: `globalThis.RAG_CONFIG = { ragUrl: "${MOCK_ENGINE}", scraperUrl: "http://scraper.mock", usdToInr: 85 };`,
		}),
	);
	await target.route(`${MOCK_ENGINE}/**`, async (route) => {
		const req = route.request();
		const method = req.method();
		const url = new URL(req.url());
		const path = url.pathname;
		const headers = await req.allHeaders();
		if (method === "OPTIONS") return route.fulfill({ status: 204, headers: CORS });
		let body: Record<string, string> = {};
		try {
			body = (req.postDataJSON() as Record<string, string>) ?? {};
		} catch {
			body = {};
		}
		const w = world;
		const findUser = (email: string) => w.users.find((u) => u.email === String(email).trim().toLowerCase());

		// ---- public ----
		const tenantOfKb = (id: string) => w.kbs.items.find((x) => x.id === id)?.tenant_id;
		const widgetPublic = await handleWidgetPublic(w.widget, method, path, headers, body, {
			json: (status, payload) => json(route, status, payload),
			text: (status, contentType, text) =>
				route.fulfill({ status, headers: { ...CORS, "content-type": contentType }, body: text }),
		});
		if (widgetPublic) return;
		if (method === "GET" && path === "/api/v1/public/branding") return json(route, 200, w.branding);
		// P6 plan choice: the plans a visitor can start on (no credential)
		if (method === "GET" && path === "/api/v1/public/plans")
			return void (await handlePlanChoiceRoute(
				w.planChoice,
				method,
				path,
				{},
				{
					isAdmin: false,
					tenantId: undefined,
					role: null,
					reply: (status, payload) => json(route, status, payload ?? {}),
					empty: () => route.fulfill({ status: 204, headers: CORS }),
				},
			));
		// /P6 plan choice
		// P3 legal: the published pages, for the sign-up form (no credential)
		if (method === "GET" && path === "/api/v1/public/legal")
			return void (await handleLegalRoute(
				w.legal,
				method,
				path,
				{},
				{
					isAdmin: false,
					branding: w.branding as unknown as { legal?: { slug: string; title: string; url: string }[] },
					reply: (status, payload) => json(route, status, payload ?? {}),
				},
			));
		// /P3 legal
		// LP4 landing: the page the preview frames show (a signed link, no credential)
		if (method === "GET" && path.startsWith("/_landing/preview/"))
			return void (await handleLandingRoute(
				w.landing,
				method,
				path,
				{},
				{
					isAdmin: true,
					raw: () => null,
					reply: (status, payload) => json(route, status, payload ?? {}),
					html: (text) =>
						route.fulfill({
							status: 200,
							headers: { ...CORS, "content-type": "text/html; charset=utf-8" },
							body: text,
						}),
				},
				url.search,
			));
		// /LP4 landing
		// COB6: the docs-only COB on the sign-in pages.
		if (
			await handleCobPublicRoute(w, method, path, body, {
				reply: (status, payload, extra) => json(route, status, payload, extra),
				sse: (text) =>
					route.fulfill({
						status: 200,
						headers: { ...CORS, "content-type": "text/event-stream", "cache-control": "no-cache" },
						body: text,
					}),
			})
		)
			return;
		if (method === "GET" && path === "/api/v1/health")
			return json(route, 200, {
				status: "ok",
				services: { api: true, postgres: true, redis: true, qdrant: true, storage: true },
			});
		if (method === "POST" && path === "/api/v1/auth/login") {
			w.stats.logins++;
			const u = findUser(body.email ?? "");
			if (!u || u.password !== body.password) return err(route, 401, "invalid_credentials");
			if (!u.verified) return err(route, 403, "email_not_verified");
			const s = newSession(w, u, headers["user-agent"]);
			return json(route, 200, tokenResponse(w, u, s));
		}
		if (method === "POST" && path === "/api/v1/auth/refresh") {
			w.stats.refresh++;
			const token = body.refresh_token ?? "";
			const sid = /^rt_([0-9a-f]{32})_/.exec(token)?.[1];
			const s = sid ? w.sessions.get(sid) : undefined;
			if (!s || s.revoked) return err(route, 401, s ? "session_revoked" : "refresh_token_invalid");
			if (token === s.previous) {
				w.stats.reuse++;
				s.revoked = true;
				return err(route, 401, "refresh_token_reused");
			}
			if (token !== s.current) return err(route, 401, "refresh_token_invalid");
			s.previous = s.current;
			s.current = `rt_${s.id}_${hex()}`;
			const u = w.users.find((x) => x.id === s.userId);
			if (!u) return err(route, 401, "session_revoked");
			return json(route, 200, tokenResponse(w, u, s));
		}
		if (method === "POST" && path === "/api/v1/auth/logout") {
			const sid = /^rt_([0-9a-f]{32})_/.exec(body.refresh_token ?? "")?.[1];
			const s = sid ? w.sessions.get(sid) : undefined;
			if (s) s.revoked = true;
			return route.fulfill({ status: 204, headers: CORS });
		}
		if (method === "POST" && path === "/api/v1/auth/signup") {
			if (w.branding.signup_mode === "closed") {
				return err(route, 403, "signup_closed", {
					detail: w.branding.contact_text,
					contact_url: w.branding.contact_url,
				});
			}
			const email = String(body.email).trim().toLowerCase();
			if (!findUser(email)) {
				w.users.push({
					id: uuid(),
					email,
					name: body.name ?? "",
					password: body.password ?? "",
					superadmin: false,
					memberships: [],
					verified: false,
				});
				(w as World & { pendingBusiness?: Record<string, string> }).pendingBusiness = {
					...((w as World & { pendingBusiness?: Record<string, string> }).pendingBusiness ?? {}),
					[email]: body.business_name ?? "",
				};
			}
			return json(route, 202, { status: "verification_sent", email });
		}
		if (method === "POST" && path === "/api/v1/auth/verify") {
			const u = findUser(body.email ?? "");
			if (!u || body.code !== SIGNUP_CODE) return err(route, 400, "invalid_code");
			const business =
				(w as World & { pendingBusiness?: Record<string, string> }).pendingBusiness?.[u.email] ?? "New workspace";
			const t: Tenant = {
				id: uuid(),
				name: business,
				status: w.branding.signup_mode === "open" ? "active" : "pending",
			};
			w.tenants.push(t);
			u.verified = true;
			u.memberships.push({ tenantId: t.id, role: "owner" });
			const s = newSession(w, u);
			return json(route, 200, tokenResponse(w, u, s));
		}
		if (method === "POST" && (path === "/api/v1/auth/resend" || path === "/api/v1/auth/forgot")) {
			return json(route, 202, { status: "sent_if_applicable" });
		}
		if (method === "POST" && path === "/api/v1/auth/reset") {
			const u = findUser(body.email ?? "");
			if (String(body.new_password ?? "").length < 10)
				return err(route, 422, "weak_password", { reasons: ["too_short"] });
			if (!u || body.code !== RESET_CODE) return err(route, 400, "invalid_code");
			u.password = body.new_password ?? "";
			for (const s of w.sessions.values()) if (s.userId === u.id) s.revoked = true;
			return json(route, 200, { reset: true });
		}
		if (method === "POST" && path === "/api/v1/invitations/view") {
			const inv = w.invitations.get(body.token ?? "");
			if (!inv || inv.used) return err(route, 404, "invitation_invalid");
			const t = w.tenants.find((x) => x.id === inv.tenantId);
			return json(route, 200, {
				tenant_name: t?.name,
				role: inv.role,
				email: inv.email,
				invited_by: "Priya Kulkarni",
				expires_at: "2026-10-07T10:00:00Z",
				account_exists: !!findUser(inv.email),
			});
		}
		if (method === "POST" && path === "/api/v1/invitations/accept") {
			const inv = w.invitations.get(body.token ?? "");
			if (!inv || inv.used) return err(route, 404, "invitation_invalid");
			if (findUser(inv.email)) return err(route, 409, "account_exists");
			const u: User = {
				id: uuid(),
				email: inv.email,
				name: body.name ?? "",
				password: body.password ?? "",
				superadmin: false,
				memberships: [{ tenantId: inv.tenantId, role: inv.role }],
				verified: true,
			};
			w.users.push(u);
			inv.used = true;
			const s = newSession(w, u);
			return json(route, 200, tokenResponse(w, u, s));
		}

		// ---- authenticated ----
		const who = resolve(w, headers);
		if (who.kind === "error") {
			const [code, status] = who.code.split(":");
			return err(route, who.status, code ?? who.code, status ? { status } : {});
		}
		if (who.kind === "none") return err(route, 401, "auth_required");

		if (who.kind === "session") {
			const u = who.user;
			const s = who.session;
			if (method === "GET" && path === "/api/v1/auth/me")
				return json(route, 200, { ...principalParts(w, u, s.tenantId), session_id: s.id });
			if (path === "/api/v1/auth/switch-admin") {
				if (!u.superadmin) return err(route, 403, "not_superadmin");
				s.tenantId = null;
				return json(route, 200, { access_token: issue(w, s), token_type: "bearer", expires_in: 900, tenant: null });
			}
			if (path === "/api/v1/auth/switch-tenant") {
				const m = u.memberships.find((x) => x.tenantId === body.tenant_id);
				// A super admin may enter only the operator workspace.
				const target = w.tenants.find((x) => x.id === body.tenant_id);
				if (!m || (u.superadmin && !target?.isOperator)) return err(route, 403, "not_member");
				s.tenantId = m.tenantId;
				return json(route, 200, {
					access_token: issue(w, s),
					token_type: "bearer",
					expires_in: 900,
					tenant: tenantOut(w, u, s.tenantId),
				});
			}
			const t = s.tenantId ? w.tenants.find((x) => x.id === s.tenantId) : null;
			// A4: an inactive workspace's session may still call /auth/*, /whoami and /tenants/me.
			if (
				t &&
				t.status !== "active" &&
				!path.startsWith("/api/v1/auth/") &&
				!["/api/v1/whoami", "/api/v1/tenants/me"].includes(path)
			) {
				return err(route, 403, "workspace_not_active", { status: t.status });
			}
		}
		// The super admin is the admin scope until they switch into the operator workspace.
		const isAdmin =
			(who.kind === "session" && who.user.superadmin && !who.session.tenantId) ||
			(who.kind === "key" && who.key.kind === "admin");
		const tenantId = who.kind === "session" ? who.session.tenantId : who.kind === "key" ? who.key.tenantId : undefined;

		if (method === "GET" && path === "/api/v1/whoami") {
			if (who.kind === "key" && who.key.kind === "corpus") {
				return json(route, 200, {
					kind: "corpus",
					corpus_id: "7b221003-0000-4000-8000-000000000001",
					corpus_name: "Patient help",
					tenant_id: tenantId,
				});
			}
			const t = w.tenants.find((x) => x.id === tenantId);
			// A tk_ key is one of the workspace's named keys: the mock's is its "Default key".
			const own =
				who.kind === "key" && who.key.kind === "tenant"
					? w.apiKeys.keys.find((k) => k.owner === `tenant:${tenantId}` && k.is_default)
					: undefined;
			return json(route, 200, {
				kind: isAdmin ? "admin" : "tenant",
				tenant_id: isAdmin ? null : tenantId,
				tenant_name: isAdmin ? null : t?.name,
				auth: who.kind === "session" ? "session" : "api_key",
				...(own ? { permission: own.permission, key_name: own.name, key_id: own.id } : {}),
			});
		}
		if (method === "GET" && path === "/api/v1/info") {
			return json(route, 200, {
				version: "0.1.0",
				features: {
					content_portal: w.contentPortal,
					streaming: true,
					transcription: { enabled: false },
					reranker: { available: false },
					web_search: { enabled: false },
					accounts: true,
					cob: { enabled: true, public: true, corpus_id: null, docs_version: null },
					billing: { enabled: true }, // BIL2
					whatsapp: { enabled: w.whatsapp.enabled, available: w.whatsapp.enabled, embedded_signup: true }, // TV6
					eval_ragas: true,
					dashboard: isAdmin ? null : w.home.dashboard, // BIZ3
				},
				embedding: { provider: "tei", model: "BAAI/bge-m3", dimensions: 1024 },
				llm: {
					provider: "openrouter",
					model: "openai/gpt-4o-mini",
					fast_model: null,
					reasoning_effort: null,
					thinking: null,
					fast_thinking: "off",
					key_configured: true,
				},
				public_url: MOCK_ENGINE,
				public_url_configured: true,
				limits: {
					max_upload_bytes: 52428800,
					max_document_tokens: 500000,
					allowed_mime_types: [
						"application/pdf",
						"application/vnd.openxmlformats-officedocument.presentationml.presentation",
						"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
						"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
						"image/jpeg",
						"image/png",
						"text/csv",
						"text/html",
						"text/markdown",
						"text/plain",
					],
					allowed_extensions: [
						".csv",
						".docx",
						".htm",
						".html",
						".jpeg",
						".jpg",
						".md",
						".pdf",
						".png",
						".pptx",
						".txt",
						".xlsx",
					],
					max_records_per_request: 1000,
					max_record_chars: 100000,
					max_query_chars: 2000,
				},
				// The engine's `Settings` defaults (core/config.py), for fields a corpus leaves unset.
				retrieval_defaults: {
					top_k: 20,
					final_k: 5,
					multi_query_count: 3,
					hyde_enabled: false,
					query_classification_enabled: true,
					reranking_enabled: false,
					mmr_enabled: true,
					mmr_lambda: 0.7,
					temporal_decay_enabled: true,
					validation_enabled: true,
					cache_enabled: true,
					hybrid_search_enabled: true,
					fusion_strategy: "convex",
					hybrid_dense_weight: 0.75,
					rrf_k: 60,
					rerank_provider: "local",
					rerank_llm_model: "openai/gpt-oss-20b",
					pipeline_order_enabled: false,
					boilerplate_strip: true,
					collapse_duplicates: true,
				},
			});
		}
		const role =
			who.kind === "session"
				? (who.user.memberships.find((m) => m.tenantId === who.session.tenantId)?.role ?? null)
				: who.kind === "key" && who.key.kind === "tenant"
					? "owner"
					: null;
		let full: Record<string, unknown> = {};
		try {
			full = (req.postDataJSON() as Record<string, unknown>) ?? {};
		} catch {
			full = {};
		}
		const accountsHandled = await handleAccountsRoute(w, method, url, full, {
			who:
				who.kind === "session"
					? {
							kind: "session",
							userId: who.user.id,
							sessionId: who.session.id,
							tenantId: who.session.tenantId,
							superadmin: who.user.superadmin,
						}
					: { kind: "key", keyKind: who.key.kind, tenantId: who.key.tenantId },
			headers,
			reply: (status, payload) =>
				status === 204 ? route.fulfill({ status, headers: CORS }) : json(route, status, payload ?? {}),
		});
		if (accountsHandled) return;
		const widgetHandled = await handleWidgetRoute(w.widget, method, path, full, {
			role,
			canUse: (id) => {
				const k = w.kbs.items.find((x) => x.id === id);
				return !!k && (isAdmin || k.tenant_id === tenantId);
			},
			tenantOf: tenantOfKb,
			engineUrl: MOCK_ENGINE,
			reply: (status, payload) =>
				status === 204 ? route.fulfill({ status, headers: CORS }) : json(route, status, payload ?? {}),
		});
		if (widgetHandled) return;
		// BIZ1
		const websiteHandled = await handleWebsiteRoute(w.website, method, path, `${path}${url.search}`, full, {
			role,
			canUse: (id) => {
				const k = w.kbs.items.find((x) => x.id === id);
				return !!k && (isAdmin || k.tenant_id === tenantId);
			},
			reply: (status, payload, extra) =>
				status === 204 ? route.fulfill({ status, headers: CORS }) : json(route, status, payload ?? {}, extra),
		});
		if (websiteHandled) return;
		// /BIZ1
		const keysHandled = await handleKeysRoute(w.apiKeys, method, path, full, {
			role,
			isAdmin,
			tenantId: tenantId ?? undefined,
			canUseKb: (id) => {
				const k = w.kbs.items.find((x) => x.id === id);
				return !!k && (isAdmin || k.tenant_id === tenantId);
			},
			actor:
				who.kind === "session"
					? who.user.email
					: who.kind === "key" && who.key.kind === "admin"
						? "admin key"
						: "tenant key",
			reply: (status, payload) =>
				status === 204 ? route.fulfill({ status, headers: CORS }) : json(route, status, payload ?? {}),
		});
		if (keysHandled) return;
		// BIL1 providers
		const providersHandled = await handleProviderRoute(w, method, path, body, {
			isAdmin,
			reply: (status, payload, extra) => json(route, status, payload ?? {}, extra),
		});
		if (providersHandled) return;
		// /BIL1 providers
		// BIL2 billing
		const billingHandled = await handleBillingRoute(w, method, path, full, {
			isAdmin,
			query: url.searchParams,
			reply: (status, payload, extra) => json(route, status, payload ?? {}, extra), // BIL6: extra = headers
			raw: (status, body, headers) => route.fulfill({ status, headers: { ...CORS, ...headers }, body }), // BIL6
		});
		if (billingHandled) return;
		// /BIL2 billing
		// PIP4
		const pipelinesHandled = await handlePipelinesRoute(w, method, path, body, {
			isAdmin,
			reply: (status, payload, extra) =>
				status === 204 ? route.fulfill({ status, headers: CORS }) : json(route, status, payload ?? {}, extra),
		});
		if (pipelinesHandled) return;
		// /PIP4
		const overviewHandled = await handleOverviewRoute(method, url, {
			isAdmin,
			operatorIds: w.tenants.filter((t) => t.isOperator).map((t) => t.id),
			tenantId: tenantId ?? undefined,
			kbs: w.kbs.items,
			query: w.query,
			widgetKeyIds: (id) => w.widget.keys.filter((k) => k.corpus_id === id).map((k) => k.id),
			reply: (status, payload) => json(route, status, payload ?? {}),
		});
		if (overviewHandled) return;
		// BIZ4
		const onboardingHandled =
			(await handleSuggestions(method, path, url.searchParams.get("lang"), (status, payload) =>
				json(route, status, payload ?? {}),
			)) ||
			(await handleOnboardingRoute(w.onboarding, method, path, body, {
				isAdmin,
				tenantId: tenantId ?? undefined,
				tenantName: w.tenants.find((x) => x.id === tenantId)?.name ?? "",
				role,
				kbs: w.kbs.items,
				keys: w.widget.keys,
				files: (kbId) => filesOf(w.docs, kbId), // TV5
				engineUrl: MOCK_ENGINE,
				reply: (status, payload) => json(route, status, payload ?? {}),
			}));
		if (onboardingHandled) return;
		// /BIZ4
		// TV4 value
		const valueHandled = await handleValueRoute(w.value, method, url, full, {
			isAdmin,
			tenantId: tenantId ?? undefined,
			role: role === "owner" || role === "member" ? role : null,
			reply: (status, payload) => json(route, status, payload ?? {}),
			html: (text) =>
				route.fulfill({
					status: 200,
					headers: { ...CORS, "content-type": "text/html; charset=utf-8" },
					body: text,
				}),
		});
		if (valueHandled) return;
		// /TV4 value
		// P3 legal
		const legalHandled = await handleLegalRoute(w.legal, method, path, full, {
			isAdmin,
			branding: w.branding as unknown as { legal?: { slug: string; title: string; url: string }[] },
			reply: (status, payload) => json(route, status, payload ?? {}),
		});
		if (legalHandled) return;
		// /P3 legal
		// LP4 landing
		const landingHandled = await handleLandingRoute(w.landing, method, path, full, {
			isAdmin,
			raw: () => req.postDataBuffer(),
			reply: (status, payload) =>
				status === 204 ? route.fulfill({ status, headers: CORS }) : json(route, status, payload ?? {}),
			html: (text) =>
				route.fulfill({ status: 200, headers: { ...CORS, "content-type": "text/html; charset=utf-8" }, body: text }),
		});
		if (landingHandled) return;
		// /LP4 landing
		// P1 payments
		const paymentsHandled = await handlePaymentsRoute(w.payments, method, path, full, {
			isAdmin,
			tenantId: tenantId ?? undefined,
			reply: (status, payload) => json(route, status, payload ?? {}),
		});
		if (paymentsHandled) return;
		// /P1 payments
		// P2 invoices
		const invoicesHandled = await handleInvoicesRoute(w.invoices, method, path, url.searchParams, {
			isAdmin,
			tenantId: tenantId ?? undefined,
			reply: (status, payload, headers) => json(route, status, payload ?? {}, headers),
			file: (text, contentType, headers) =>
				route.fulfill({ status: 200, headers: { ...CORS, "content-type": contentType, ...headers }, body: text }),
		});
		if (invoicesHandled) return;
		// /P2 invoices
		// P5 plans
		const shopHandled = await handleShopRoute(w.shop, method, path, url.searchParams, full, {
			isAdmin,
			tenantId: tenantId ?? undefined,
			tenantName: w.tenants.find((x) => x.id === tenantId)?.name ?? "",
			role: role === "owner" || role === "member" ? role : null,
			reply: (status, payload) => json(route, status, payload ?? {}),
		});
		if (shopHandled) return;
		// /P5 plans
		// P6 plan choice
		const planChoiceHandled = await handlePlanChoiceRoute(w.planChoice, method, path, full, {
			isAdmin,
			tenantId: tenantId ?? undefined,
			role: role === "owner" || role === "member" ? role : null,
			reply: (status, payload) => json(route, status, payload ?? {}),
			empty: () => route.fulfill({ status: 204, headers: CORS }),
		});
		if (planChoiceHandled) return;
		// /P6 plan choice
		// TV3 support
		const supportHandled = await handleSupportRoute(w.support, method, path, full, {
			isAdmin,
			tenantId: tenantId ?? undefined,
			tenantName: w.tenants.find((x) => x.id === tenantId)?.name ?? "",
			email: who.kind === "session" ? who.user.email : "tenant key",
			reply: (status, payload) =>
				status === 204 ? route.fulfill({ status, headers: CORS }) : json(route, status, payload ?? {}),
			html: (text) =>
				route.fulfill({ status: 200, headers: { ...CORS, "content-type": "text/html; charset=utf-8" }, body: text }),
		});
		if (supportHandled) return;
		// /TV3 support
		// TV6
		const waHandled = await handleWhatsappRoute(w.whatsapp, method, path, full, {
			isAdmin,
			reply: (status, payload) => json(route, status, payload ?? {}),
		});
		if (waHandled) return;
		// /TV6
		// BIZ3
		const homeHandled = await handleHomeRoute(method, url, {
			isAdmin,
			tenantId: tenantId ?? undefined,
			kbs: w.kbs.items,
			onboarding: homeOnboarding(w.onboarding, tenantId ?? undefined), // BIZ4
			connected: homeConnected(w.onboarding, tenantId ?? undefined), // BIZ4
			value: homeValue(w.value), // TV4
			reply: (status, payload) => json(route, status, payload ?? {}),
		});
		if (homeHandled) return;
		// /BIZ3
		// COB3: the COB panel's chat, history, feedback and starters.
		const cobHandled = await handleCobRoute(w, method, url, full, {
			reply: (status, payload) =>
				status === 204 ? route.fulfill({ status, headers: CORS }) : json(route, status, payload ?? {}),
			sse: (body) =>
				route.fulfill({
					status: 200,
					headers: { ...CORS, "content-type": "text/event-stream", "cache-control": "no-cache" },
					body,
				}),
		});
		if (cobHandled) return;
		// COB8: the in-app Docs (tree, pages, search).
		const docsHandled = await handleDocsRoute(w, method, url, {
			isAdmin,
			reply: (status, payload) => json(route, status, payload ?? {}),
		});
		if (docsHandled) return;
		// Guided setup (C8) and the runs it starts; before the assistant routes, whose pattern also matches /assistant/setup.
		const setupHandled = await handleSetupRoute(w, method, path, full, {
			role,
			canUse: (id) => {
				const k = w.kbs.items.find((x) => x.id === id);
				return !!k && (isAdmin || k.tenant_id === tenantId);
			},
			reply: (status, payload) => json(route, status, payload ?? {}),
		});
		if (setupHandled) return;
		// The assistant routes sit before the knowledge-base handler, which would otherwise take /corpora/{id}/….
		const assistantHandled = await handleAssistantRoute(w.assistant, method, path, full, {
			role,
			canUse: (id) => {
				const k = w.kbs.items.find((x) => x.id === id);
				return !!k && (isAdmin || k.tenant_id === tenantId);
			},
			reply: (status, payload) =>
				status === 204 ? route.fulfill({ status, headers: CORS }) : json(route, status, payload ?? {}),
		});
		if (assistantHandled) return;
		const handled = await handleKbRoute(w.kbs, method, url, full, {
			isAdmin,
			tenantId: tenantId ?? undefined,
			role,
			tenants: w.tenants,
			widgetKeys: (id) => ({
				used: widgetKeysUsed(w.widget, tenantOfKb, id),
				limit: w.widget.limits.has(id) ? (w.widget.limits.get(id) ?? null) : 3,
			}),
			reply: (status, payload) =>
				status === 204 ? route.fulfill({ status, headers: CORS }) : json(route, status, payload ?? {}),
		});
		if (handled) return;
		const docHandled = await handleDocumentRoute(w.docs, method, url, full, {
			isAdmin,
			tenantId: tenantId ?? undefined,
			role,
			canUse: (id) => {
				const k = w.kbs.items.find((x) => x.id === id);
				return !!k && (isAdmin || k.tenant_id === tenantId);
			},
			multipart: () => parseMultipart(req.postDataBuffer(), headers["content-type"] ?? ""),
			reply: (status, payload) =>
				status === 204 ? route.fulfill({ status, headers: CORS }) : json(route, status, payload ?? {}),
		});
		if (docHandled) return;
		const queryHandled = await handleQueryRoute(w.query, method, url, full, {
			isAdmin,
			role,
			assistantOn: (id) => assistantEnabled(w.assistant, id),
			billingPaused: () => billingStateFor(w).paused, // BIL4
			canUse: (id) => {
				const k = w.kbs.items.find((x) => x.id === id);
				return !!k && (isAdmin || k.tenant_id === tenantId);
			},
			tenantOf: (id) => w.kbs.items.find((x) => x.id === id)?.tenant_id,
			reply: (status, payload, extra = {}) =>
				status === 204 ? route.fulfill({ status, headers: CORS }) : json(route, status, payload ?? {}, extra),
			sse: (text) =>
				route.fulfill({
					status: 200,
					headers: { ...CORS, "content-type": "text/event-stream", "cache-control": "no-cache" },
					body: text,
				}),
		});
		if (queryHandled) return;
		return err(route, 404, "not_found");
	});
}
