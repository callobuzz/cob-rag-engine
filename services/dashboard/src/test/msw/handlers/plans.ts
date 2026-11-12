import { HttpResponse, http } from "msw";
import type { Plan } from "@/lib/api/accounts-types";
import { BASE } from "../data";
import { coded } from "./auth";
import { pipelinesState } from "./pipelines"; // PIP4

/**
 * A6 plans CRUD, stateful. Rules like `accounts/plans.py`: one default (making one the default
 * un-defaults the others), the default can't be un-defaulted, switched off or deleted
 * (`plan_is_default`), a plan in use can't be deleted (`plan_in_use` + `tenant_count`), names are
 * unique (`plan_name_taken`).
 */
function plan(id: string, name: string, over: Partial<Plan>): Plan {
	return {
		id,
		name,
		description: null,
		max_knowledge_bases: null,
		monthly_questions: null,
		max_documents: null,
		max_widget_keys: null,
		max_upload_mb: null,
		max_document_tokens: null,
		count_cached_answers: true,
		is_default: false,
		is_active: true,
		is_builtin: id === "plan-standard",
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
		created_at: "2026-09-10T10:00:00Z",
		updated_at: "2026-09-10T10:00:00Z",
		tenant_count: 0,
		...over,
	};
}

function seed(): Plan[] {
	return [
		plan("plan-standard", "Standard", {
			description: "No price · for your own projects and tests",
			created_at: "2026-09-01T10:00:00Z",
		}),
		plan("plan-starter", "Starter", {
			description: "₹1,499 / month · billed by you",
			max_knowledge_bases: 1,
			monthly_questions: 1000,
			max_documents: 200,
			max_widget_keys: 1,
			is_default: true,
			tenant_count: 4,
		}),
		plan("plan-pro", "Pro", {
			description: "₹4,999 / month · billed by you",
			max_knowledge_bases: 3,
			monthly_questions: 10000,
			max_documents: 2000,
			max_widget_keys: 5,
			tenant_count: 2,
		}),
		plan("plan-pilot", "Pilot", {
			description: "Free trial for a first customer",
			max_knowledge_bases: 1,
			monthly_questions: 300,
			max_documents: 50,
			max_widget_keys: 1,
		}),
		plan("plan-old", "Diwali offer", { is_active: false, max_knowledge_bases: 2 }),
		// Built in and locked; only the operator's own workspace is on it.
		plan("plan-internal", "Internal", {
			description: "The operator's own workspace: unlimited and free",
			is_internal: true,
			tenant_count: 1,
		}),
	];
}

export const plansState = { items: seed(), sent: [] as { method: string; body: unknown }[] };

export function resetPlansState(): void {
	plansState.items = seed();
	plansState.sent = [];
}

export const planById = (id: string) => plansState.items.find((p) => p.id === id);

const EDITABLE = [
	"name",
	"description",
	"max_knowledge_bases",
	"monthly_questions",
	"max_documents",
	"max_widget_keys",
	"max_upload_mb",
	"max_document_tokens",
	"count_cached_answers",
	"is_default",
	"is_active",
	"website_learning_enabled", // BIZ3
	"website_pages_max",
	"dashboard_features",
	"pipeline_profile_id", // PIP4
	"fair_use_limits", // PIP4
	"fair_use_policy", // PIP4
] as const;

export const plansHandlers = [
	http.get(`${BASE}/api/v1/admin/plans`, ({ request }) => {
		const all = new URL(request.url).searchParams.get("include_inactive") !== "false";
		// `include_inactive=false`: only assignable plans, never the Internal one.
		const items = plansState.items.filter((p) => all || (p.is_active && !p.is_internal));
		return HttpResponse.json({ items, count: items.length });
	}),
	http.post(`${BASE}/api/v1/admin/plans`, async ({ request }) => {
		const b = (await request.json()) as Partial<Plan> & { name: string };
		plansState.sent.push({ method: "POST", body: b });
		if (plansState.items.some((p) => p.name === b.name)) return coded(409, "plan_name_taken");
		if (b.is_default) for (const p of plansState.items) p.is_default = false;
		const p = plan(`plan-${plansState.items.length + 1}`, b.name, { ...b, created_at: new Date().toISOString() });
		plansState.items.push(p);
		return HttpResponse.json(p, { status: 201 });
	}),
	http.patch(`${BASE}/api/v1/admin/plans/:id`, async ({ params, request }) => {
		const p = planById(String(params.id));
		if (!p) return coded(404, "plan_not_found");
		const b = (await request.json()) as Partial<Plan>;
		plansState.sent.push({ method: "PATCH", body: b });
		if (p.is_internal) return coded(409, "plan_internal_locked");
		if (p.is_default && b.is_default === false) return coded(409, "plan_is_default");
		const willDefault = b.is_default ?? p.is_default;
		const willActive = b.is_active ?? p.is_active;
		if (willDefault && !willActive) return coded(409, "plan_is_default");
		if (b.name && b.name !== p.name && plansState.items.some((x) => x.name === b.name))
			return coded(409, "plan_name_taken");
		if (b.is_default && !p.is_default) for (const x of plansState.items) x.is_default = false;
		for (const k of EDITABLE) if (k in b) Object.assign(p, { [k]: b[k] });
		// PIP4: the label of the profile the plan now answers with (the default's when none is picked)
		if ("pipeline_profile_id" in b) {
			const picked = pipelinesState.profiles.find((x) => x.id === b.pipeline_profile_id);
			p.pipeline_profile_label = (picked ?? pipelinesState.profiles.find((x) => x.is_default))?.label ?? null;
		}
		// /PIP4
		return HttpResponse.json(p);
	}),
	http.delete(`${BASE}/api/v1/admin/plans/:id`, ({ params }) => {
		const p = planById(String(params.id));
		if (!p) return coded(404, "plan_not_found");
		if (p.is_internal) return coded(409, "plan_internal_locked");
		if (p.is_default) return coded(409, "plan_is_default");
		if (p.tenant_count > 0) return coded(409, "plan_in_use", { tenant_count: p.tenant_count });
		plansState.items = plansState.items.filter((x) => x.id !== p.id);
		return new HttpResponse(null, { status: 204 });
	}),
];
