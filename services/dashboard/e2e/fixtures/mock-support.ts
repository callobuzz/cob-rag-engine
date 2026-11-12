/**
 * Support requests part of the fake engine (TV3): a business writes to its provider, the provider
 * answers from the inbox, the business follows up and closes. Follows `api/routes/support.py` and
 * `support_admin.py`: a workspace sign-in sees only its own requests (another's is a 404), the first
 * reply fixes the first-response time, a follow-up opens a closed request, the operator has no
 * workspace on the business routes and the business routes are not the inbox's. Indian demo data:
 * Shree Dental Clinic, Pune writes to Sahayak AI.
 */
interface Message {
	id: string;
	author_kind: "business" | "deployer";
	author_email: string;
	body: string;
	created_at: string;
}

interface Row {
	id: string;
	tenant_id: string;
	workspace_name: string;
	number: number;
	subject: string;
	status: "open" | "answered" | "waiting" | "closed";
	page: string | null;
	context: Record<string, unknown>;
	created_by_email: string;
	created_at: string;
	last_message_at: string;
	first_response_at: string | null;
	closed_at: string | null;
	messages: Message[];
}

export interface SupportMock {
	rows: Row[];
	enabled: boolean;
	slaHours: number | null;
	next: number;
	sent: { method: string; path: string; body: unknown }[];
}

export const createSupportState = (): SupportMock => ({
	rows: [],
	enabled: true,
	slaHours: 4,
	next: 1042,
	sent: [],
});

export interface SupportCtx {
	isAdmin: boolean;
	tenantId: string | undefined;
	tenantName: string;
	email: string;
	reply: (status: number, body?: unknown) => Promise<void>;
	html: (body: string) => Promise<void>;
}

const tenantRow = (r: Row) => ({
	id: r.id,
	number: r.number,
	subject: r.subject,
	status: r.status,
	page: r.page,
	created_by_email: r.created_by_email,
	created_at: r.created_at,
	last_message_at: r.last_message_at,
	first_response_at: r.first_response_at,
	closed_at: r.closed_at,
});
const detail = (r: Row) => ({
	...tenantRow(r),
	messages: r.messages.map((m) => ({
		id: m.id,
		author_kind: m.author_kind,
		author: m.author_kind === "business" ? m.author_email : null,
		body: m.body,
		created_at: m.created_at,
	})),
});
const adminRow = (r: Row) => ({ ...tenantRow(r), tenant_id: r.tenant_id, workspace_name: r.workspace_name, source: "landing_contact" in r.context ? "website" : "workspace" });
const adminDetail = (r: Row) => ({ ...adminRow(r), context: r.context, messages: r.messages });

let tick = 0;
const at = () => new Date(Date.UTC(2026, 9, 5, 9, 0, tick++)).toISOString();
const notFound = (ctx: SupportCtx) =>
	ctx.reply(404, { detail: "That support request does not exist.", code: "support_request_not_found" });

function median(values: number[]): number | null {
	if (!values.length) return null;
	const s = [...values].sort((a, b) => a - b);
	return s[Math.floor(s.length / 2)] ?? null;
}

export async function handleSupportRoute(
	state: SupportMock,
	method: string,
	path: string,
	body: Record<string, unknown>,
	ctx: SupportCtx,
): Promise<boolean> {
	// ---- the price list (its plans and a public link)
	if (path.startsWith("/api/v1/admin/price-sheet")) {
		if (!ctx.isAdmin) {
			await ctx.reply(403, { detail: "Admin access required" });
			return true;
		}
		if (method === "GET" && path === "/api/v1/admin/price-sheet") {
			await ctx.html("<!doctype html><title>Price list</title><h1>Price list</h1><p>Starter ₹499 a month</p>");
			return true;
		}
		if (method === "POST" && path === "/api/v1/admin/price-sheet/link") {
			await ctx.reply(201, { url: "https://api.sahayak.example/api/v1/public/price-sheet/tok123" });
			return true;
		}
		if (method === "DELETE" && path === "/api/v1/admin/price-sheet/link") {
			await ctx.reply(204);
			return true;
		}
		return false;
	}
	// ---- the provider's inbox
	if (path.startsWith("/api/v1/admin/support/")) {
		if (!ctx.isAdmin) {
			await ctx.reply(403, { detail: "Admin access required" });
			return true;
		}
		if (method === "GET" && path === "/api/v1/admin/support/requests") {
			const items = [...state.rows].sort((a, b) => b.last_message_at.localeCompare(a.last_message_at)).map(adminRow);
			await ctx.reply(200, { items, total: items.length });
			return true;
		}
		if (method === "GET" && path === "/api/v1/admin/support/stats") {
			const waits = state.rows
				.filter((r) => r.first_response_at)
				.map((r) => Math.floor((Date.parse(r.first_response_at as string) - Date.parse(r.created_at)) / 60_000));
			await ctx.reply(200, {
				open: state.rows.filter((r) => r.status === "open").length,
				answered: waits.length,
				median_first_response_minutes: median(waits),
				within_sla_pct: state.slaHours != null && waits.length ? 100 : null,
				sla_hours: state.slaHours,
				days: 30,
			});
			return true;
		}
		const m = /^\/api\/v1\/admin\/support\/requests\/([^/]+)(\/messages)?$/.exec(path);
		if (m) {
			const row = state.rows.find((r) => r.id === m[1]);
			if (!row) {
				await notFound(ctx);
				return true;
			}
			if (method === "GET" && !m[2]) {
				await ctx.reply(200, adminDetail(row));
				return true;
			}
			if (method === "POST" && m[2]) {
				state.sent.push({ method, path, body });
				const when = at();
				row.messages.push({
					id: `m-${tick}`,
					author_kind: "deployer",
					author_email: "admin key",
					body: String(body.body ?? ""),
					created_at: when,
				});
				row.first_response_at ??= when;
				row.status = (body.status as Row["status"] | undefined) ?? "answered";
				row.closed_at = row.status === "closed" ? when : null;
				row.last_message_at = when;
				await ctx.reply(201, adminDetail(row));
				return true;
			}
			if (method === "PATCH" && !m[2]) {
				state.sent.push({ method, path, body });
				row.status = body.status as Row["status"];
				row.closed_at = row.status === "closed" ? at() : null;
				await ctx.reply(200, adminDetail(row));
				return true;
			}
		}
		return false;
	}
	// ---- the business's side
	if (!path.startsWith("/api/v1/support/")) return false;
	if (ctx.isAdmin || !ctx.tenantId) {
		await ctx.reply(403, { detail: "There is no workspace here.", code: "no_workspace" });
		return true;
	}
	const own = state.rows.filter((r) => r.tenant_id === ctx.tenantId);
	if (method === "GET" && path === "/api/v1/support/info") {
		await ctx.reply(200, {
			enabled: state.enabled,
			provider_name: "Sahayak AI",
			sla_hours: state.slaHours,
			open_count: own.filter((r) => r.status !== "closed").length,
		});
		return true;
	}
	if (method === "GET" && path === "/api/v1/support/requests") {
		const items = [...own].sort((a, b) => b.last_message_at.localeCompare(a.last_message_at)).map(tenantRow);
		await ctx.reply(200, { items, total: items.length });
		return true;
	}
	if (method === "POST" && path === "/api/v1/support/requests") {
		state.sent.push({ method, path, body });
		if (!state.enabled) {
			await ctx.reply(409, { detail: "Support requests are off.", code: "support_disabled" });
			return true;
		}
		const when = at();
		const row: Row = {
			id: `99999999-9999-4999-8999-${String(state.next).padStart(12, "0")}`,
			tenant_id: ctx.tenantId,
			workspace_name: ctx.tenantName,
			number: state.next++,
			subject: String(body.subject ?? ""),
			status: "open",
			page: (body.page as string | undefined) ?? null,
			context: {
				plan: "Starter",
				workspace_status: "active",
				paused: false,
				kb_count: 2,
				page: (body.page as string | undefined) ?? null,
				client_errors: (body.client_errors as string[] | undefined) ?? [],
			},
			created_by_email: ctx.email,
			created_at: when,
			last_message_at: when,
			first_response_at: null,
			closed_at: null,
			messages: [
				{
					id: `m-${tick}`,
					author_kind: "business",
					author_email: ctx.email,
					body: String(body.message ?? ""),
					created_at: when,
				},
			],
		};
		state.rows.unshift(row);
		await ctx.reply(201, tenantRow(row));
		return true;
	}
	const m = /^\/api\/v1\/support\/requests\/([^/]+)(\/messages|\/close)?$/.exec(path);
	if (m) {
		const row = own.find((r) => r.id === m[1]);
		if (!row) {
			await notFound(ctx);
			return true;
		}
		if (method === "GET" && !m[2]) {
			await ctx.reply(200, detail(row));
			return true;
		}
		if (method === "POST" && m[2] === "/messages") {
			state.sent.push({ method, path, body });
			const when = at();
			row.messages.push({
				id: `m-${tick}`,
				author_kind: "business",
				author_email: ctx.email,
				body: String(body.body ?? ""),
				created_at: when,
			});
			row.status = "open";
			row.closed_at = null;
			row.last_message_at = when;
			await ctx.reply(201, detail(row).messages.at(-1));
			return true;
		}
		if (method === "POST" && m[2] === "/close") {
			state.sent.push({ method, path, body: null });
			row.status = "closed";
			row.closed_at = at();
			await ctx.reply(200, tenantRow(row));
			return true;
		}
	}
	return false;
}
