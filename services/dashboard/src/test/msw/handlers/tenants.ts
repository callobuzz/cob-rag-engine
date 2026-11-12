import { HttpResponse, http } from "msw";
import type { AdminTenant, PlanUsageResponse } from "@/lib/api/accounts-types";
import { BASE, OPERATOR, RANGOLI, SHREE } from "../data";
import { coded } from "./auth";
import { planById, plansState } from "./plans";

/**
 * A4 tenant lifecycle and A6 tenant plans (admin), plus the tenant's own record and key. Stateful
 * (reset after each test): lists filter by status/search with offset paging and totals like the
 * engine; activate/suspend/reactivate/decline follow A4's transitions (409
 * `invalid_status_transition` with `from`/`to`), suspend and delete check `confirm_name`
 * (case-sensitive, outer spaces forgiven; 422 `confirm_mismatch`).
 */
export const PENDING_ID = "44444444-4444-4444-8444-444444444444";
export const KOTHRUD_ID = "55555555-5555-4555-8555-555555555555";
export const MEHTA_ID = "66666666-6666-4666-8666-666666666666";
export const VIDYA_ID = "33333333-3333-4333-8333-333333333333";
export const OPERATOR_ID = OPERATOR.id;

type Status = AdminTenant["status"];

function tenant(
	id: string,
	name: string,
	corpus_count: number,
	over: Partial<AdminTenant> & { status?: Status } = {},
): AdminTenant {
	const status = over.status ?? "active";
	return {
		id,
		name,
		description: null,
		status,
		is_active: status === "active",
		is_operator: false,
		corpus_count,
		created_at: "2026-09-01T10:00:00Z",
		activated_at: status === "pending" ? null : "2026-09-01T10:00:00Z",
		activation_note: null,
		created_by_user_id: null,
		max_corpora: null,
		member_count: 2,
		owner_emails: [],
		effective_max_corpora: 3,
		has_api_key: true,
		monthly_questions: 10000,
		plan_id: "plan-pro",
		plan_name: "Pro",
		questions_this_month: 0,
		...over,
	};
}

function seed(): AdminTenant[] {
	return [
		// The operator's own workspace: active, owned by the super admin, on the locked Internal plan.
		tenant(OPERATOR.id, OPERATOR.name, 0, {
			is_operator: true,
			owner_emails: ["sv@punedigital.in"],
			member_count: 1,
			plan_id: "plan-internal",
			plan_name: "Internal",
			monthly_questions: null,
			effective_max_corpora: null,
			created_at: "2026-08-01T10:00:00Z",
		}),
		tenant(SHREE.id, SHREE.name, 2, {
			owner_emails: ["priya@shree-labs.in"],
			member_count: 3,
			questions_this_month: 8200,
			activation_note: "Paid ₹4,999 via UPI",
			description: "Pathology lab, Pune",
		}),
		tenant(RANGOLI.id, RANGOLI.name, 1, {
			owner_emails: ["meera@rangolisarees.in"],
			questions_this_month: 3860,
			created_at: "2026-09-02T10:00:00Z",
		}),
		tenant(VIDYA_ID, "Vidya Classes", 1, {
			owner_emails: ["admin@vidyaclasses.in"],
			member_count: 1,
			plan_id: "plan-starter",
			plan_name: "Starter",
			monthly_questions: 1000,
			effective_max_corpora: 1,
			questions_this_month: 1000,
			created_at: "2026-09-03T10:00:00Z",
		}),
		tenant(PENDING_ID, "Anand Dental Care", 0, {
			status: "pending",
			owner_emails: ["dr.anand@anandental.in"],
			member_count: 1,
			plan_id: "plan-starter",
			plan_name: "Starter",
			created_by_user_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
			created_at: "2026-09-29T08:00:00Z",
			description: "Dental clinic, Kothrud",
		}),
		tenant(KOTHRUD_ID, "Kothrud Physio Clinic", 0, {
			status: "pending",
			owner_emails: ["neha@kothrudphysio.in"],
			member_count: 1,
			plan_id: "plan-starter",
			plan_name: "Starter",
			created_by_user_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
			created_at: "2026-09-28T08:00:00Z",
		}),
		tenant(MEHTA_ID, "Mehta Opticals", 1, {
			status: "suspended",
			owner_emails: ["rakesh@mehtaopticals.in"],
			plan_id: "plan-starter",
			plan_name: "Starter",
			activation_note: "Payment overdue since 15 Sep",
			created_at: "2026-08-20T10:00:00Z",
		}),
	];
}

export const tenantsState = {
	items: seed(),
	smtpConfigured: true,
	rotations: 0,
	deletes: [] as { id: string; operatorConfirmed: boolean }[],
	restores: 0,
};

export function resetTenantsState(): void {
	tenantsState.items = seed();
	tenantsState.smtpConfigured = true;
	tenantsState.rotations = 0;
	tenantsState.deletes = [];
	tenantsState.restores = 0;
}

/** A4 active customers (the D3 pickers' fixture). */
export const ADMIN_TENANTS: AdminTenant[] = seed().filter((t) => t.status === "active");

/** A6 plan for Shree Labs. */
export const PLAN_ME: PlanUsageResponse = {
	plan: {
		id: "p-pro",
		name: "Pro",
		description: "₹1,499 / month",
		max_knowledge_bases: 3,
		monthly_questions: 5000,
		max_documents: 500,
		max_widget_keys: 3,
		max_upload_mb: null,
		max_document_tokens: null,
		count_cached_answers: true,
		is_default: false,
		is_active: true,
		is_builtin: false,
		is_internal: false,
		byok_allowed: true,
		self_service: false,
		website_learning_enabled: true,
		website_pages_max: null,
		dashboard_features: {
			advanced: true,
			models: true,
			retrieval: true,
			keys_agents: true,
			webhooks: true,
			quality: true,
			api_docs: true,
			usage_details: true,
			widget_code_only: false,
		},
		created_at: "2026-09-01T10:00:00Z",
		updated_at: "2026-09-01T10:00:00Z",
	},
	limits: {
		max_knowledge_bases: 3,
		monthly_questions: 5000,
		max_documents: 500,
		max_widget_keys: 3,
		max_upload_mb: null,
		max_document_tokens: null,
	},
	usage: { month: "2026-09", questions: 1904, knowledge_bases: 2, documents: 139, widget_keys: 1 },
	resets_at: "2026-10-01T00:00:00Z",
};

const find = (id: string) => tenantsState.items.find((t) => t.id === id);
const notFound = () => HttpResponse.json({ detail: "Tenant not found" }, { status: 404 });
const transition = (t: AdminTenant, from: Status, to: Status) =>
	t.status === from ? null : coded(409, "invalid_status_transition", { from: t.status, to });
const confirmOk = (t: AdminTenant, typed: unknown) => typeof typed === "string" && typed.trim() === t.name;

function applyPlan(t: AdminTenant, planId: string): Response | null {
	const p = planById(planId);
	if (!p) return coded(404, "plan_not_found");
	if (p.is_internal && !t.is_operator) return coded(409, "plan_not_assignable");
	if (!p.is_active) return coded(409, "plan_inactive");
	t.plan_id = p.id;
	t.plan_name = p.name;
	t.monthly_questions = p.monthly_questions;
	t.effective_max_corpora = t.max_corpora ?? p.max_knowledge_bases;
	return null;
}

function defaultPlan() {
	return plansState.items.find((p) => p.is_default) ?? plansState.items[0];
}

export const tenantsHandlers = [
	http.get(`${BASE}/api/v1/admin/tenants`, ({ request }) => {
		const u = new URL(request.url);
		const status = u.searchParams.get("status");
		const search = u.searchParams.get("search")?.toLowerCase();
		const op = u.searchParams.get("is_operator");
		const offset = Number(u.searchParams.get("offset") ?? 0);
		const limit = Number(u.searchParams.get("limit") ?? 100);
		const all = tenantsState.items
			.filter((t) => !status || t.status === status)
			.filter((t) => op === null || t.is_operator === (op === "true"))
			.filter((t) => !search || t.name.toLowerCase().includes(search))
			.sort((a, b) => b.created_at.localeCompare(a.created_at));
		const items = all.slice(offset, offset + limit);
		return HttpResponse.json({ items, count: items.length, total: all.length, offset, limit });
	}),
	http.post(`${BASE}/api/v1/admin/tenants`, async ({ request }) => {
		const b = (await request.json()) as {
			name: string;
			description?: string | null;
			plan_id?: string | null;
			max_corpora?: number | null;
		};
		const id = `77777777-7777-4777-8777-${String(tenantsState.items.length).padStart(12, "0")}`;
		const t = tenant(id, b.name, 0, {
			description: b.description ?? null,
			max_corpora: b.max_corpora ?? null,
			member_count: 0,
			created_at: new Date().toISOString(),
		});
		const err = applyPlan(t, b.plan_id ?? defaultPlan()?.id ?? "");
		if (err) return err;
		tenantsState.items.push(t);
		return HttpResponse.json(
			{
				id,
				name: t.name,
				description: t.description,
				api_key: `tk_${id.slice(0, 8)}_new_secret_key`,
				max_corpora: t.max_corpora,
				plan_id: t.plan_id,
				status: "active",
				is_active: true,
				created_at: t.created_at,
			},
			{ status: 201 },
		);
	}),
	http.get(`${BASE}/api/v1/admin/tenants/:id`, ({ params }) => {
		const t = find(String(params.id));
		return t ? HttpResponse.json(t) : notFound();
	}),
	http.put(`${BASE}/api/v1/admin/tenants/:id`, async ({ params, request }) => {
		const t = find(String(params.id));
		if (!t) return notFound();
		const b = (await request.json()) as Record<string, unknown>;
		if (typeof b.name === "string") t.name = b.name;
		if ("description" in b) t.description = (b.description as string | null) ?? null;
		if ("max_corpora" in b) {
			t.max_corpora = (b.max_corpora as number | null) ?? null;
			t.effective_max_corpora = t.max_corpora ?? planById(t.plan_id)?.max_knowledge_bases ?? null;
		}
		return HttpResponse.json(t);
	}),
	http.post(`${BASE}/api/v1/admin/operator-workspace/restore`, () => {
		if (tenantsState.items.some((x) => x.is_operator)) return coded(409, "operator_workspace_exists");
		tenantsState.restores += 1;
		const back = seed().find((x) => x.is_operator) as AdminTenant;
		tenantsState.items.unshift(back);
		return HttpResponse.json(back, { status: 201 });
	}),
	http.delete(`${BASE}/api/v1/admin/tenants/:id`, ({ params, request }) => {
		const t = find(String(params.id));
		if (!t) return notFound();
		const q = new URL(request.url).searchParams;
		if (!confirmOk(t, q.get("confirm_name"))) return coded(422, "confirm_mismatch");
		if (t.is_operator && q.get("confirm_operator_workspace") !== "true") {
			return coded(422, "operator_confirmation_required");
		}
		tenantsState.deletes.push({ id: t.id, operatorConfirmed: q.get("confirm_operator_workspace") === "true" });
		tenantsState.items = tenantsState.items.filter((x) => x.id !== t.id);
		return new HttpResponse(null, { status: 204 });
	}),
	http.post(`${BASE}/api/v1/admin/tenants/:id/activate`, async ({ params, request }) => {
		const t = find(String(params.id));
		if (!t) return notFound();
		const b = ((await request.json().catch(() => null)) ?? {}) as { plan_id?: string; note?: string | null };
		const bad = transition(t, "pending", "active");
		if (bad) return bad;
		if (b.plan_id) {
			const err = applyPlan(t, b.plan_id);
			if (err) return err;
		}
		t.status = "active";
		t.is_active = true;
		t.activated_at = new Date().toISOString();
		t.activation_note = b.note ?? null;
		return HttpResponse.json(t);
	}),
	http.post(`${BASE}/api/v1/admin/tenants/:id/decline`, ({ params }) => {
		const t = find(String(params.id));
		if (!t) return notFound();
		const bad = transition(t, "pending", "active");
		if (bad) return coded(409, "invalid_status_transition", { from: t.status, to: "declined" });
		tenantsState.items = tenantsState.items.filter((x) => x.id !== t.id);
		return new HttpResponse(null, { status: 204 });
	}),
	http.post(`${BASE}/api/v1/admin/tenants/:id/suspend`, async ({ params, request }) => {
		const t = find(String(params.id));
		if (!t) return notFound();
		const b = (await request.json()) as { confirm_name: string; reason?: string | null };
		if (t.is_operator) return coded(409, "operator_workspace_protected");
		if (!confirmOk(t, b.confirm_name)) return coded(422, "confirm_mismatch");
		const bad = transition(t, "active", "suspended");
		if (bad) return bad;
		t.status = "suspended";
		t.is_active = false;
		return HttpResponse.json(t);
	}),
	http.post(`${BASE}/api/v1/admin/tenants/:id/reactivate`, ({ params }) => {
		const t = find(String(params.id));
		if (!t) return notFound();
		const bad = transition(t, "suspended", "active");
		if (bad) return bad;
		t.status = "active";
		t.is_active = true;
		return HttpResponse.json(t);
	}),
	http.post(`${BASE}/api/v1/admin/tenants/:id/plan`, async ({ params, request }) => {
		const t = find(String(params.id));
		if (!t) return notFound();
		const b = (await request.json()) as { plan_id: string };
		const err = applyPlan(t, b.plan_id);
		return err ?? HttpResponse.json(t);
	}),
	http.get(`${BASE}/api/v1/admin/tenants/:id/plan`, ({ params }) => {
		const t = find(String(params.id));
		if (!t) return notFound();
		const p = planById(t.plan_id) ?? PLAN_ME.plan;
		const { tenant_count: _count, ...plan } = { tenant_count: 0, ...p };
		return HttpResponse.json({
			plan,
			limits: {
				max_knowledge_bases: t.effective_max_corpora,
				monthly_questions: p.monthly_questions,
				max_documents: p.max_documents,
				max_widget_keys: p.max_widget_keys,
				max_upload_mb: p.max_upload_mb ?? null,
				max_document_tokens: p.max_document_tokens ?? null,
			},
			usage: {
				month: "2026-09",
				questions: t.questions_this_month,
				knowledge_bases: t.corpus_count,
				documents: 139,
				widget_keys: 1,
			},
			resets_at: "2026-10-01T00:00:00Z",
		} satisfies PlanUsageResponse);
	}),
	http.post(`${BASE}/api/v1/admin/tenants/:id/rotate-key`, ({ params }) => {
		const t = find(String(params.id));
		if (!t) return notFound();
		tenantsState.rotations++;
		t.has_api_key = true;
		return HttpResponse.json({ api_key: `tk_${t.id.slice(0, 8)}_rotated_${tenantsState.rotations}` });
	}),
	http.get(`${BASE}/api/v1/admin/smtp`, () =>
		HttpResponse.json({
			configured: tenantsState.smtpConfigured,
			provider: tenantsState.smtpConfigured ? "smtp" : "log",
			host: tenantsState.smtpConfigured ? "smtp.sahayak.ai" : null,
			port: 587,
			security: "starttls",
			from_address: "Sahayak AI <no-reply@sahayak.ai>",
			username_set: true,
			password_set: true,
			dashboard_url: "http://127.0.0.1:5173",
		}),
	),
	http.get(`${BASE}/api/v1/tenants/me`, () => {
		const t = find(SHREE.id);
		if (!t) return notFound();
		return HttpResponse.json({
			id: t.id,
			name: t.name,
			description: t.description,
			corpus_count: t.corpus_count,
			max_corpora: t.max_corpora,
			effective_max_corpora: t.effective_max_corpora,
			plan_id: t.plan_id,
			plan_name: t.plan_name,
			status: t.status,
			is_active: t.is_active,
			activated_at: t.activated_at,
			created_at: t.created_at,
			has_api_key: t.has_api_key,
			is_operator: t.is_operator,
		});
	}),
	http.post(`${BASE}/api/v1/tenants/me/rotate-key`, () => {
		tenantsState.rotations++;
		const t = find(SHREE.id);
		if (t) t.has_api_key = true;
		return HttpResponse.json({ api_key: `tk_11111111_own_${tenantsState.rotations}` });
	}),
	http.get(`${BASE}/api/v1/tenants/me/plan`, () => HttpResponse.json(PLAN_ME)),
];
