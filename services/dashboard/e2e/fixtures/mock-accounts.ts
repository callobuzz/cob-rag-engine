import { randomBytes, randomUUID } from "node:crypto";
import { grantedLimits } from "./mock-billing"; // BIL5 add-ons
import type { World } from "./mock-engine";

/**
 * Accounts part of the fake engine (D7): the A4 tenant lifecycle (list with status, search and
 * totals; activate with a plan and note; decline; suspend and delete with the typed name;
 * reactivate; change plan; rotate key), A5 teams (members, invitations, roles, last owner, admin
 * invitations with `accept_url`), A6 plans CRUD and A3 profile, password and sessions. It follows
 * the engine's route code closely enough to exercise the dashboard. Invitation e-mails go to
 * `world.accounts.mail` (the mock's `docker compose logs mailer`). Indian demo data only.
 */

export interface PlanRecord {
	id: string;
	name: string;
	description: string | null;
	max_knowledge_bases: number | null;
	monthly_questions: number | null;
	max_documents: number | null;
	max_widget_keys: number | null;
	is_default: boolean;
	is_active: boolean;
	/** A8: the plan the engine seeded on install (Standard). */
	is_builtin: boolean;
	/** The Internal plan of the operator's own workspace: unlimited, locked, assignable only to it. */
	is_internal: boolean;
	created_at: string;
	updated_at: string;
}

export interface TenantMeta {
	description: string | null;
	planId: string;
	maxCorpora: number | null;
	note: string | null;
	createdAt: string;
	activatedAt: string | null;
	createdByUserId: string | null;
}

export interface Mail {
	to: string;
	subject: string;
	link: string;
}

export interface AccountsState {
	plans: PlanRecord[];
	meta: Map<string, TenantMeta>;
	mail: Mail[];
	/** Engine SMTP (A2): the dev stack prints mail to the log, so this is false. */
	smtpConfigured: boolean;
	/** Every lifecycle / team write, for assertions. */
	sent: { method: string; path: string; body: unknown }[];
}

export const DASHBOARD_ORIGIN = "http://127.0.0.1:5173";

const now = () => new Date().toISOString();

export function createAccountsState(tenantIds: { id: string; status: string; isOperator?: boolean }[]): AccountsState {
	const plans: PlanRecord[] = [
		{
			id: randomUUID(),
			name: "Standard",
			description: "No price · for your own projects and tests",
			max_knowledge_bases: null,
			monthly_questions: null,
			max_documents: null,
			max_widget_keys: null,
			is_default: false,
			is_active: true,
			is_builtin: true,
			is_internal: false,
			created_at: "2026-09-01T09:00:00Z",
			updated_at: "2026-09-01T09:00:00Z",
		},
		{
			id: randomUUID(),
			name: "Starter",
			description: "₹1,499 / month · billed by you",
			max_knowledge_bases: 1,
			monthly_questions: 1000,
			max_documents: 200,
			max_widget_keys: 1,
			is_default: true,
			is_active: true,
			is_builtin: false,
			is_internal: false,
			created_at: "2026-09-05T09:00:00Z",
			updated_at: "2026-09-05T09:00:00Z",
		},
		// An ordinary plan the engine seeds (phase11_009): editable and deletable like any other.
		{
			id: randomUUID(),
			name: "Pilot",
			description: "Free trial for a first customer",
			max_knowledge_bases: 1,
			monthly_questions: 300,
			max_documents: 50,
			max_widget_keys: 1,
			is_default: false,
			is_active: true,
			is_builtin: false,
			is_internal: false,
			created_at: "2026-09-06T09:00:00Z",
			updated_at: "2026-09-06T09:00:00Z",
		},
		{
			id: randomUUID(),
			name: "Internal",
			description: "The operator's own workspace: unlimited and free",
			max_knowledge_bases: null,
			monthly_questions: null,
			max_documents: null,
			max_widget_keys: null,
			is_default: false,
			is_active: true,
			is_builtin: false,
			is_internal: true,
			created_at: "2026-09-06T09:00:00Z",
			updated_at: "2026-09-06T09:00:00Z",
		},
	];
	const standard = plans.find((p) => p.name === "Standard");
	const starter = plans.find((p) => p.name === "Starter");
	const internal = plans.find((p) => p.is_internal);
	const meta = new Map<string, TenantMeta>();
	tenantIds.forEach((t, i) => {
		meta.set(t.id, {
			description: null,
			planId: (t.isOperator ? internal : t.status === "pending" ? starter : standard)?.id ?? "",
			maxCorpora: null,
			note: t.status === "active" ? "Paid ₹4,999 via UPI" : null,
			createdAt: new Date(Date.UTC(2026, 8, 1 + i, 10)).toISOString(),
			activatedAt: t.status === "active" ? new Date(Date.UTC(2026, 8, 1 + i, 11)).toISOString() : null,
			createdByUserId: null,
		});
	});
	return { plans, meta, mail: [], smtpConfigured: false, sent: [] };
}

type Reply = (status: number, body?: unknown) => Promise<void>;
type Who =
	| { kind: "session"; userId: string; sessionId: string; tenantId: string | null; superadmin: boolean }
	| { kind: "key"; keyKind: "admin" | "tenant" | "corpus"; tenantId?: string };

export interface AccountsCtx {
	who: Who;
	headers: Record<string, string>;
	reply: Reply;
}

function metaOf(w: World, id: string): TenantMeta {
	let m = w.accounts.meta.get(id);
	if (!m) {
		m = {
			description: null,
			planId: w.accounts.plans.find((p) => p.is_default)?.id ?? "",
			maxCorpora: null,
			note: null,
			createdAt: now(),
			activatedAt: null,
			createdByUserId: null,
		};
		w.accounts.meta.set(id, m);
	}
	return m;
}

const planOf = (w: World, id: string) => w.accounts.plans.find((p) => p.id === id);

function members(w: World, tenantId: string) {
	return w.users.flatMap((u) =>
		u.memberships
			.filter((m) => m.tenantId === tenantId)
			.map((m) => ({
				user_id: u.id,
				email: u.email,
				name: u.name,
				role: m.role,
				joined_at: "2026-09-01T10:00:00Z",
				last_login_at: u.lastLoginAt ?? null,
			})),
	);
}

function adminTenant(w: World, id: string) {
	const t = w.tenants.find((x) => x.id === id);
	if (!t) return null;
	const m = metaOf(w, id);
	const plan = planOf(w, m.planId);
	const team = members(w, id);
	return {
		id: t.id,
		name: t.name,
		description: m.description,
		corpus_count: w.kbs.items.filter((k) => k.tenant_id === id).length,
		max_corpora: m.maxCorpora,
		effective_max_corpora: m.maxCorpora ?? plan?.max_knowledge_bases ?? null,
		plan_id: m.planId,
		plan_name: plan?.name ?? "",
		status: t.status,
		is_active: t.status === "active",
		is_operator: !!t.isOperator,
		activated_at: m.activatedAt,
		created_at: m.createdAt,
		activation_note: m.note,
		created_by_user_id: m.createdByUserId,
		owner_emails: team
			.filter((x) => x.role === "owner")
			.map((x) => x.email)
			.sort(),
		member_count: team.length,
		monthly_questions: plan?.monthly_questions ?? null,
		questions_this_month: w.query.used.get(id) ?? 0,
		// A8: a working tk_ key exists (sign-ups start without one).
		has_api_key: [...w.keys.values()].some((k) => k.kind === "tenant" && k.tenantId === id),
	};
}

function planOut(w: World, p: PlanRecord) {
	const tenant_count = [...w.accounts.meta.entries()].filter(
		([id, m]) => m.planId === p.id && w.tenants.some((t) => t.id === id),
	).length;
	return { ...p, tenant_count };
}

function inviteRecord(
	w: World,
	tenantId: string,
	email: string,
	role: "owner" | "member",
	invitedBy: string,
): { token: string; id: string } | { code: string; status: number } {
	const addr = email.trim().toLowerCase();
	const sa = w.users.find((u) => u.superadmin);
	if (sa && sa.email === addr) return { status: 409, code: "superadmin_email" };
	if (members(w, tenantId).some((m) => m.email === addr)) return { status: 409, code: "already_member" };
	for (const [token, inv] of w.invitations)
		if (inv.tenantId === tenantId && inv.email === addr) w.invitations.delete(token);
	const token = `inv_${randomBytes(12).toString("hex")}`;
	const id = randomUUID();
	w.invitations.set(token, {
		tenantId,
		email: addr,
		role,
		used: false,
		id,
		invitedBy,
		createdAt: now(),
		expiresAt: new Date(Date.now() + 7 * 86_400_000).toISOString(),
	});
	return { token, id };
}

function invitationOut(w: World, token: string) {
	const inv = w.invitations.get(token);
	if (!inv) return null;
	return {
		id: inv.id ?? token,
		email: inv.email,
		role: inv.role,
		invited_by: inv.invitedBy ?? "Priya Kulkarni",
		created_at: inv.createdAt ?? null,
		expires_at: inv.expiresAt ?? "2026-10-07T10:00:00Z",
	};
}

function openInvitations(w: World, tenantId: string) {
	return [...w.invitations.entries()]
		.filter(([, inv]) => inv.tenantId === tenantId && !inv.used)
		.map(([token]) => invitationOut(w, token))
		.filter((x) => x !== null);
}

const acceptUrl = (token: string) => `${DASHBOARD_ORIGIN}/invite#token=${token}`;

function sendInvite(w: World, tenantName: string, email: string, token: string) {
	w.accounts.mail.push({ to: email, subject: `You're invited to ${tenantName}`, link: acceptUrl(token) });
}

function lastOwner(w: World, tenantId: string, userId: string): boolean {
	const team = members(w, tenantId);
	const m = team.find((x) => x.user_id === userId);
	return !!m && m.role === "owner" && team.filter((x) => x.role === "owner").length === 1;
}

function removeMember(w: World, tenantId: string, userId: string) {
	const u = w.users.find((x) => x.id === userId);
	if (!u) return;
	u.memberships = u.memberships.filter((m) => m.tenantId !== tenantId);
	for (const s of w.sessions.values()) if (s.userId === userId && s.tenantId === tenantId) s.revoked = true;
}

function newKey(w: World, tenantId: string): string {
	for (const [k, v] of w.keys) if (v.kind === "tenant" && v.tenantId === tenantId) w.keys.delete(k);
	const key = `tk_${tenantId.slice(0, 8)}_${randomBytes(12).toString("hex")}`;
	w.keys.set(key, { kind: "tenant", tenantId });
	return key;
}

/** Handles the accounts routes; returns true when it answered. */
export async function handleAccountsRoute(
	w: World,
	method: string,
	url: URL,
	body: Record<string, unknown>,
	ctx: AccountsCtx,
): Promise<boolean> {
	const path = url.pathname;
	const { who, reply } = ctx;
	const done = (status: number, payload?: unknown) => reply(status, payload).then(() => true);
	const err = (status: number, code: string | null, detail: string, extra: Record<string, unknown> = {}) =>
		done(status, code ? { detail, code, ...extra } : { detail, ...extra });
	// The super admin is the admin scope until they switch into the operator workspace.
	const isAdmin =
		(who.kind === "session" && who.superadmin && !who.tenantId) || (who.kind === "key" && who.keyKind === "admin");
	const record = () => w.accounts.sent.push({ method, path, body: Object.keys(body).length ? body : null });

	// ---- A3: profile, password, sessions (sessions only) ----
	if (path.startsWith("/api/v1/auth/")) {
		if (who.kind !== "session") return err(403, "session_required", "This needs a session");
		const user = w.users.find((u) => u.id === who.userId);
		if (!user) return err(401, "session_revoked", "Session ended");
		const principal = () => {
			const tenant = (id: string | null) => {
				const t = id ? w.tenants.find((x) => x.id === id) : null;
				const m = user.memberships.find((x) => x.tenantId === id);
				return t && m ? { id: t.id, name: t.name, role: m.role, status: t.status, is_operator: !!t.isOperator } : null;
			};
			return {
				user: { id: user.id, email: user.email, name: user.name, is_superadmin: user.superadmin },
				tenant: tenant(who.tenantId),
				tenants: user.memberships.map((m) => tenant(m.tenantId)).filter(Boolean),
				session_id: who.sessionId,
			};
		};
		if (method === "PATCH" && path === "/api/v1/auth/me") {
			const name = String(body.name ?? "").trim();
			if (!name) return err(422, null, "name", { detail: [{ loc: ["body", "name"], msg: "Enter a name" }] });
			user.name = name;
			return done(200, principal());
		}
		if (method === "POST" && path === "/api/v1/auth/change-password") {
			if (body.current_password !== user.password) return err(401, "invalid_credentials", "Wrong password");
			const pw = String(body.new_password ?? "");
			if (pw.length < 10) return err(422, "weak_password", "Too weak", { reasons: ["too_short"] });
			if (pw.toLowerCase() === "password123456") return err(422, "weak_password", "Too weak", { reasons: ["common"] });
			user.password = pw;
			for (const s of w.sessions.values()) if (s.userId === user.id && s.id !== who.sessionId) s.revoked = true;
			return done(204);
		}
		if (method === "GET" && path === "/api/v1/auth/sessions") {
			const items = [...w.sessions.values()]
				.filter((s) => s.userId === user.id && !s.revoked)
				.sort((a, b) => (b.createdAt ?? "").localeCompare(a.createdAt ?? ""))
				.map((s) => ({
					id: s.id,
					created_at: s.createdAt ?? null,
					last_used_at: s.lastUsedAt ?? s.createdAt ?? null,
					expires_at: "2026-10-30T10:00:00Z",
					user_agent: s.userAgent ?? null,
					ip: "203.0.113.10",
					current: s.id === who.sessionId,
				}));
			return done(200, { items });
		}
		const one = /^\/api\/v1\/auth\/sessions\/([0-9a-f-]+)$/.exec(path);
		if (method === "DELETE" && one) {
			const s = w.sessions.get(one[1] ?? "");
			if (!s || s.userId !== user.id) return err(404, null, "Session not found");
			s.revoked = true;
			return done(204);
		}
		if (method === "POST" && path === "/api/v1/auth/sessions/revoke-others") {
			let revoked = 0;
			for (const s of w.sessions.values()) {
				if (s.userId === user.id && s.id !== who.sessionId && !s.revoked) {
					s.revoked = true;
					revoked++;
				}
			}
			return done(200, { revoked });
		}
		return false;
	}

	// ---- A4/A6: the operator's customers and plans ----
	if (path === "/api/v1/admin/smtp" && method === "GET") {
		if (!isAdmin) return err(403, null, "Admin only");
		return done(200, {
			configured: w.accounts.smtpConfigured,
			provider: w.accounts.smtpConfigured ? "smtp" : "log",
			host: null,
			port: 587,
			security: "starttls",
			from_address: null,
			username_set: false,
			password_set: false,
			dashboard_url: DASHBOARD_ORIGIN,
		});
	}
	if (path.startsWith("/api/v1/admin/plans")) {
		if (!isAdmin) return err(403, null, "Admin only");
		if (path === "/api/v1/admin/plans" && method === "GET") {
			const order = (p: PlanRecord) => (p.is_default ? 0 : 1);
			// include_inactive=false: only assignable plans, never the Internal one.
			const all = url.searchParams.get("include_inactive") !== "false";
			const items = w.accounts.plans
				.filter((p) => all || (p.is_active && !p.is_internal))
				.sort((a, b) => order(a) - order(b) || a.name.localeCompare(b.name));
			return done(200, { items: items.map((p) => planOut(w, p)), count: items.length });
		}
		if (path === "/api/v1/admin/plans" && method === "POST") {
			record();
			const name = String(body.name ?? "").trim();
			if (!name) return err(422, null, "", { detail: [{ loc: ["body", "name"], msg: "Enter a name" }] });
			if (w.accounts.plans.some((p) => p.name === name)) return err(409, "plan_name_taken", "Name taken");
			if (body.is_default) for (const p of w.accounts.plans) p.is_default = false;
			const p: PlanRecord = {
				id: randomUUID(),
				name,
				description: (body.description as string | null) ?? null,
				max_knowledge_bases: (body.max_knowledge_bases as number | null) ?? null,
				monthly_questions: (body.monthly_questions as number | null) ?? null,
				max_documents: (body.max_documents as number | null) ?? null,
				max_widget_keys: (body.max_widget_keys as number | null) ?? null,
				is_default: body.is_default === true,
				is_active: true,
				is_builtin: false,
				is_internal: false,
				created_at: now(),
				updated_at: now(),
			};
			w.accounts.plans.push(p);
			return done(201, planOut(w, p));
		}
		const m = /^\/api\/v1\/admin\/plans\/([^/]+)$/.exec(path);
		const p = m ? planOf(w, m[1] ?? "") : undefined;
		if (!p) return err(404, "plan_not_found", "Plan not found");
		if (method === "GET") return done(200, planOut(w, p));
		if (method === "PATCH") {
			record();
			if (p.is_internal) return err(409, "plan_internal_locked", "The Internal plan cannot be changed");
			if (p.is_default && body.is_default === false)
				return err(409, "plan_is_default", "The default plan stays the default");
			const willDefault = (body.is_default as boolean | undefined) ?? p.is_default;
			const willActive = (body.is_active as boolean | undefined) ?? p.is_active;
			if (willDefault && !willActive) return err(409, "plan_is_default", "The default plan cannot be deactivated");
			if (typeof body.name === "string" && body.name !== p.name && w.accounts.plans.some((x) => x.name === body.name))
				return err(409, "plan_name_taken", "Name taken");
			if (body.is_default === true) for (const x of w.accounts.plans) x.is_default = false;
			for (const k of [
				"name",
				"description",
				"max_knowledge_bases",
				"monthly_questions",
				"max_documents",
				"max_widget_keys",
				"is_default",
				"is_active",
			] as const) {
				if (k in body && !(["name", "is_default", "is_active"].includes(k) && body[k] === null)) {
					Object.assign(p, { [k]: body[k] });
				}
			}
			p.updated_at = now();
			return done(200, planOut(w, p));
		}
		if (method === "DELETE") {
			record();
			if (p.is_internal) return err(409, "plan_internal_locked", "The Internal plan cannot be deleted");
			if (p.is_default) return err(409, "plan_is_default", "The default plan cannot be deleted");
			const count = planOut(w, p).tenant_count;
			if (count) return err(409, "plan_in_use", "Tenants are on this plan", { tenant_count: count });
			w.accounts.plans = w.accounts.plans.filter((x) => x.id !== p.id);
			return done(204);
		}
		return false;
	}

	if (path.startsWith("/api/v1/admin/tenants")) {
		if (!isAdmin) return err(403, null, "Admin only");
		if (path === "/api/v1/admin/tenants" && method === "GET") {
			const status = url.searchParams.get("status");
			const search = url.searchParams.get("search")?.toLowerCase();
			const op = url.searchParams.get("is_operator");
			const offset = Number(url.searchParams.get("offset") ?? 0);
			const limit = Number(url.searchParams.get("limit") ?? 100);
			const all = w.tenants
				.filter((t) => !status || t.status === status)
				.filter((t) => op === null || !!t.isOperator === (op === "true"))
				.filter((t) => !search || t.name.toLowerCase().includes(search))
				.map((t) => adminTenant(w, t.id))
				.filter((x) => x !== null)
				.sort((a, b) => b.created_at.localeCompare(a.created_at));
			const items = all.slice(offset, offset + limit);
			return done(200, { items, count: items.length, total: all.length, offset, limit });
		}
		if (path === "/api/v1/admin/tenants" && method === "POST") {
			record();
			const name = String(body.name ?? "").trim();
			if (!name) return err(422, null, "", { detail: [{ loc: ["body", "name"], msg: "Enter a name" }] });
			const planId = (body.plan_id as string | null) ?? w.accounts.plans.find((p) => p.is_default)?.id ?? "";
			const plan = planOf(w, planId);
			if (!plan) return err(404, "plan_not_found", "Plan not found");
			if (plan.is_internal) return err(409, "plan_not_assignable", "The Internal plan is for the operator workspace");
			if (!plan.is_active) return err(409, "plan_inactive", "Plan switched off");
			const id = randomUUID();
			w.tenants.push({ id, name, status: "active" });
			w.accounts.meta.set(id, {
				description: (body.description as string | null) ?? null,
				planId,
				maxCorpora: (body.max_corpora as number | null) ?? null,
				note: null,
				createdAt: now(),
				activatedAt: now(),
				createdByUserId: null,
			});
			const api_key = newKey(w, id);
			return done(201, {
				id,
				name,
				description: (body.description as string | null) ?? null,
				api_key,
				max_corpora: (body.max_corpora as number | null) ?? null,
				plan_id: planId,
				status: "active",
				is_active: true,
				created_at: now(),
			});
		}
		const m = /^\/api\/v1\/admin\/tenants\/([^/]+)(\/.*)?$/.exec(path);
		const id = m?.[1] ?? "";
		const sub = m?.[2] ?? "";
		const t = w.tenants.find((x) => x.id === id);
		if (!t) return err(404, null, "Tenant not found");
		const meta = metaOf(w, id);
		const transition = (from: string, to: string) =>
			t.status === from ? null : err(409, "invalid_status_transition", "Not possible", { from: t.status, to });
		const assign = (planId: string) => {
			const plan = planOf(w, planId);
			if (!plan) return err(404, "plan_not_found", "Plan not found");
			if (plan.is_internal && !t.isOperator)
				return err(409, "plan_not_assignable", "The Internal plan is for the operator workspace");
			if (!plan.is_active) return err(409, "plan_inactive", "Plan switched off");
			meta.planId = planId;
			return null;
		};
		if (sub === "" && method === "GET") return done(200, adminTenant(w, id));
		if (sub === "" && method === "PUT") {
			record();
			if (typeof body.name === "string") t.name = body.name;
			if ("description" in body) meta.description = (body.description as string | null) ?? null;
			if ("max_corpora" in body) meta.maxCorpora = (body.max_corpora as number | null) ?? null;
			return done(200, adminTenant(w, id));
		}
		if (sub === "" && method === "DELETE") {
			record();
			const typed = url.searchParams.get("confirm_name");
			if (typed == null)
				return err(422, null, "", { detail: [{ loc: ["query", "confirm_name"], msg: "Field required" }] });
			if (typed.trim() !== t.name) return err(422, "confirm_mismatch", "Type the name exactly");
			if (t.isOperator && url.searchParams.get("confirm_operator_workspace") !== "true")
				return err(422, "operator_confirmation_required", "Confirm deleting your own workspace");
			w.tenants = w.tenants.filter((x) => x.id !== id);
			w.kbs.items = w.kbs.items.filter((k) => k.tenant_id !== id);
			for (const u of w.users) u.memberships = u.memberships.filter((x) => x.tenantId !== id);
			return done(204);
		}
		if (sub === "/activate" && method === "POST") {
			record();
			const bad = transition("pending", "active");
			if (bad) return bad;
			if (body.plan_id) {
				const e = assign(String(body.plan_id));
				if (e) return e;
			}
			t.status = "active";
			meta.activatedAt = now();
			meta.note = (body.note as string | null) ?? null;
			for (const x of members(w, id).filter((y) => y.role === "owner"))
				w.accounts.mail.push({
					to: x.email,
					subject: `You're in: ${t.name} is active`,
					link: `${DASHBOARD_ORIGIN}/login`,
				});
			return done(200, adminTenant(w, id));
		}
		if (sub === "/decline" && method === "POST") {
			record();
			if (t.status !== "pending")
				return err(409, "invalid_status_transition", "Not possible", { from: t.status, to: "declined" });
			w.tenants = w.tenants.filter((x) => x.id !== id);
			for (const u of w.users) u.memberships = u.memberships.filter((x) => x.tenantId !== id);
			return done(204);
		}
		if (sub === "/suspend" && method === "POST") {
			record();
			if (t.isOperator) return err(409, "operator_workspace_protected", "Your own workspace is never suspended");
			if (String(body.confirm_name ?? "").trim() !== t.name)
				return err(422, "confirm_mismatch", "Type the name exactly");
			const bad = transition("active", "suspended");
			if (bad) return bad;
			t.status = "suspended";
			return done(200, adminTenant(w, id));
		}
		if (sub === "/reactivate" && method === "POST") {
			record();
			const bad = transition("suspended", "active");
			if (bad) return bad;
			t.status = "active";
			return done(200, adminTenant(w, id));
		}
		if (sub === "/plan" && method === "POST") {
			record();
			const e = assign(String(body.plan_id ?? ""));
			return e ?? done(200, adminTenant(w, id));
		}
		if (sub === "/plan" && method === "GET") {
			const plan = planOf(w, meta.planId);
			const a = adminTenant(w, id);
			if (!plan || !a) return err(404, null, "Tenant not found");
			const granted = grantedLimits(w, id); // BIL5 add-ons
			const plus = (base: number | null, extra = 0): number | null => (base === null ? null : base + extra); // BIL5 add-ons
			return done(200, {
				plan,
				limits: {
					max_knowledge_bases: a.effective_max_corpora,
					// BIL5 add-ons: plan limit + the add-ons in force (unlimited stays unlimited)
					monthly_questions: plus(plan.monthly_questions, granted.monthly_questions),
					max_documents: plus(plan.max_documents, granted.max_documents),
					max_widget_keys: plus(plan.max_widget_keys, granted.max_widget_keys),
					// /BIL5 add-ons
				},
				usage: {
					month: now().slice(0, 7),
					questions: a.questions_this_month,
					knowledge_bases: a.corpus_count,
					documents: w.kbs.items.filter((k) => k.tenant_id === id).reduce((n, k) => n + k.document_count, 0),
					widget_keys: w.widget.keys.filter((k) => w.kbs.items.find((x) => x.id === k.corpus_id)?.tenant_id === id)
						.length,
				},
				resets_at: "2026-10-01T00:00:00Z",
			});
		}
		if (sub === "/rotate-key" && method === "POST") {
			record();
			return done(200, { api_key: newKey(w, id) });
		}
		if (sub === "/members" && method === "GET") {
			return done(200, { members: members(w, id), invitations: openInvitations(w, id) });
		}
		if (sub === "/invitations" && method === "POST") {
			record();
			const r = inviteRecord(w, id, String(body.email ?? ""), body.role === "owner" ? "owner" : "member", "admin");
			if ("code" in r) return err(r.status, r.code, r.code);
			if (w.accounts.smtpConfigured) sendInvite(w, t.name, String(body.email), r.token);
			return done(201, { ...invitationOut(w, r.token), accept_url: acceptUrl(r.token) });
		}
		// A8: the operator's resend — a new link and 7 more days, returned as `accept_url`.
		const resend = /^\/invitations\/([^/]+)\/resend$/.exec(sub);
		if (resend && method === "POST") {
			record();
			const entry = [...w.invitations.entries()].find(
				([token, x]) => x.tenantId === id && !x.used && (x.id ?? token) === resend[1],
			);
			if (!entry) return err(404, null, "Invitation not found");
			const [token, x] = entry;
			w.invitations.delete(token);
			const r = inviteRecord(w, id, x.email, x.role, x.invitedBy ?? "admin");
			if ("code" in r) return err(r.status, r.code, r.code);
			if (w.accounts.smtpConfigured) sendInvite(w, t.name, x.email, r.token);
			return done(200, { ...invitationOut(w, r.token), accept_url: acceptUrl(r.token) });
		}
		const inv = /^\/invitations\/([^/]+)$/.exec(sub);
		if (inv && method === "DELETE") {
			record();
			for (const [token, x] of w.invitations)
				if (x.tenantId === id && (x.id ?? token) === inv[1]) {
					w.invitations.delete(token);
					return done(204);
				}
			return err(404, null, "Invitation not found");
		}
		const mem = /^\/members\/([^/]+)$/.exec(sub);
		if (mem && (method === "PATCH" || method === "DELETE")) {
			record();
			return memberWrite(w, id, mem[1] ?? "", method, body, done, err);
		}
		return false;
	}

	// ---- A5 and A4: the caller's own workspace ----
	if (path.startsWith("/api/v1/tenants/me")) {
		const tenantId = who.kind === "session" ? who.tenantId : who.kind === "key" ? who.tenantId : null;
		if (!tenantId || isAdmin) return err(403, null, "Tenant only");
		const t = w.tenants.find((x) => x.id === tenantId);
		if (!t) return err(404, null, "Tenant not found");
		const role =
			who.kind === "session"
				? (w.users.find((u) => u.id === who.userId)?.memberships.find((m) => m.tenantId === tenantId)?.role ?? null)
				: "owner";
		const owner = () => (role === "owner" ? null : err(403, "owner_required", "Owners only"));
		if (path === "/api/v1/tenants/me" && method === "GET") {
			const a = adminTenant(w, tenantId);
			if (!a) return err(404, null, "Tenant not found");
			return done(200, {
				id: a.id,
				name: a.name,
				description: a.description,
				corpus_count: a.corpus_count,
				max_corpora: a.max_corpora,
				effective_max_corpora: a.effective_max_corpora,
				plan_id: a.plan_id,
				plan_name: a.plan_name,
				status: a.status,
				is_active: a.is_active,
				activated_at: a.activated_at,
				created_at: a.created_at,
				has_api_key: a.has_api_key,
				is_operator: a.is_operator,
			});
		}
		if (path === "/api/v1/tenants/me/rotate-key" && method === "POST") {
			return owner() ?? done(200, { api_key: newKey(w, tenantId) });
		}
		if (path === "/api/v1/tenants/me/members" && method === "GET") {
			return done(200, { members: members(w, tenantId), invitations: openInvitations(w, tenantId) });
		}
		const inviter = who.kind === "session" ? (w.users.find((u) => u.id === who.userId)?.name ?? "") : "tenant key";
		if (path === "/api/v1/tenants/me/members" && method === "POST") {
			record();
			const denied = owner();
			if (denied) return denied;
			const r = inviteRecord(
				w,
				tenantId,
				String(body.email ?? ""),
				body.role === "owner" ? "owner" : "member",
				inviter,
			);
			if ("code" in r) return err(r.status, r.code, r.code);
			sendInvite(w, t.name, String(body.email).trim().toLowerCase(), r.token);
			return done(201, invitationOut(w, r.token));
		}
		const inv = /^\/api\/v1\/tenants\/me\/invitations\/([^/]+)(\/resend)?$/.exec(path);
		if (inv) {
			record();
			const denied = owner();
			if (denied) return denied;
			const entry = [...w.invitations.entries()].find(
				([token, x]) => x.tenantId === tenantId && (x.id ?? token) === inv[1],
			);
			if (!entry) return err(404, null, "Invitation not found");
			const [token, x] = entry;
			if (inv[2] && method === "POST") {
				w.invitations.delete(token);
				const r = inviteRecord(w, tenantId, x.email, x.role, inviter);
				if ("code" in r) return err(r.status, r.code, r.code);
				sendInvite(w, t.name, x.email, r.token);
				return done(200, invitationOut(w, r.token));
			}
			if (!inv[2] && method === "DELETE") {
				w.invitations.delete(token);
				return done(204);
			}
			return false;
		}
		const mem = /^\/api\/v1\/tenants\/me\/members\/([^/]+)$/.exec(path);
		if (mem && (method === "PATCH" || method === "DELETE")) {
			record();
			const self = who.kind === "session" && who.userId === mem[1];
			if (!(method === "DELETE" && self)) {
				const denied = owner();
				if (denied) return denied;
			}
			return memberWrite(w, tenantId, mem[1] ?? "", method, body, done, err);
		}
		return false;
	}
	return false;
}

function memberWrite(
	w: World,
	tenantId: string,
	userId: string,
	method: string,
	body: Record<string, unknown>,
	done: (status: number, payload?: unknown) => Promise<boolean>,
	err: (status: number, code: string | null, detail: string) => Promise<boolean>,
): Promise<boolean> {
	const u = w.users.find((x) => x.id === userId);
	const m = u?.memberships.find((x) => x.tenantId === tenantId);
	if (!u || !m) return err(404, null, "Member not found");
	if (method === "PATCH") {
		const role = body.role === "owner" ? "owner" : "member";
		if (role === "member" && lastOwner(w, tenantId, userId)) return err(409, "last_owner", "Keep one owner");
		m.role = role;
		return done(
			200,
			members(w, tenantId).find((x) => x.user_id === userId),
		);
	}
	if (lastOwner(w, tenantId, userId)) return err(409, "last_owner", "Keep one owner");
	removeMember(w, tenantId, userId);
	return done(204);
}
