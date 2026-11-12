import { HttpResponse, http } from "msw";
import type { AuditEntry } from "@/features/audit/api";
import { BASE, SHREE } from "../data";

/**
 * A8 `GET /admin/audit`: filters like api/routes/audit.py (tenant, exact action or a catalog
 * prefix ending in ".", actor substring, range ≤ 366 days) and offset paging. Every request's
 * query is logged for assertions.
 */
export const AUDIT_CATALOG = [
	"settings.updated",
	"superadmin.bootstrapped",
	"user.password_reset",
	"user.password_changed",
	"user.login_locked",
	"session.refresh_reused",
	"tenant.signed_up",
	"tenant.activated",
	"tenant.suspended",
	"tenant.reactivated",
	"tenant.declined",
	"tenant.deleted",
	"tenant.created",
	"member.invited",
	"invitation.revoked",
	"invitation.accepted",
	"member.role_changed",
	"member.removed",
	"plan.created",
	"plan.updated",
	"plan.deleted",
	"tenant.plan_changed",
];

const MEHTA = "66666666-6666-4666-8666-666666666666";

function entry(i: number, over: Partial<AuditEntry>): AuditEntry {
	return {
		id: `a0000000-0000-4000-8000-${String(i).padStart(12, "0")}`,
		created_at: new Date(Date.UTC(2026, 8, 30, 12, 0) - i * 3_600_000).toISOString(),
		actor_kind: "user",
		actor_user_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
		actor_label: "sv@punedigital.in",
		action: "tenant.activated",
		tenant_id: SHREE.id,
		tenant_label: "Shree Labs",
		target_type: "tenant",
		target_id: SHREE.id,
		details: {},
		ip: "203.0.113.10",
		...over,
	};
}

export function seedAudit(extra = 0): AuditEntry[] {
	const rows: AuditEntry[] = [
		entry(1, {
			action: "settings.updated",
			tenant_id: null,
			tenant_label: null,
			target_type: "settings",
			target_id: "engine",
			details: { changed: ["branding.product_name", "branding.primary_color"] },
		}),
		entry(2, { action: "tenant.activated", details: { plan: "Starter", note: "Paid ₹4,999 via UPI" } }),
		entry(3, {
			action: "tenant.suspended",
			tenant_id: MEHTA,
			tenant_label: "Mehta Opticals",
			target_id: MEHTA,
			details: { reason: "Unpaid for 2 months" },
		}),
		entry(4, {
			action: "member.role_changed",
			actor_label: "priya@shree-labs.in",
			details: { email: "meera@shree-labs.in", from: "member", to: "owner" },
		}),
		entry(5, {
			action: "user.login_locked",
			actor_kind: "system",
			actor_user_id: null,
			actor_label: "system",
			tenant_id: null,
			tenant_label: null,
			target_type: "user",
			target_id: "rohan@shree-labs.in",
			details: { failures: 5 },
		}),
		entry(6, {
			action: "plan.created",
			actor_kind: "admin_key",
			actor_user_id: null,
			actor_label: "admin key",
			tenant_id: null,
			tenant_label: null,
			target_type: "plan",
			target_id: "plan-pro",
			details: { name: "Pro" },
			ip: null,
		}),
	];
	for (let i = 0; i < extra; i++)
		rows.push(entry(10 + i, { action: "member.invited", details: { email: `staff${i}@shree-labs.in` } }));
	return rows;
}

export function createAuditMock(rows: AuditEntry[] = seedAudit()) {
	const log: Record<string, string>[] = [];
	const prefixes = new Set(
		AUDIT_CATALOG.flatMap((a) => [...a].flatMap((c, i) => (c === "." ? [a.slice(0, i + 1)] : []))),
	);
	const handlers = [
		http.get(`${BASE}/api/v1/admin/audit`, ({ request }) => {
			const q = Object.fromEntries(new URL(request.url).searchParams);
			log.push(q);
			if (q.action && !AUDIT_CATALOG.includes(q.action) && !prefixes.has(q.action))
				return HttpResponse.json({ detail: `Unknown audit action '${q.action}'` }, { status: 422 });
			if (q.start && q.end && new Date(q.end).getTime() - new Date(q.start).getTime() > 366 * 86_400_000)
				return HttpResponse.json({ detail: "The time range is at most 366 days" }, { status: 422 });
			let items = rows;
			if (q.tenant_id) items = items.filter((r) => r.tenant_id === q.tenant_id);
			if (q.action)
				items = items.filter((r) => (q.action?.endsWith(".") ? r.action.startsWith(q.action) : r.action === q.action));
			if (q.actor) items = items.filter((r) => r.actor_label.toLowerCase().includes(String(q.actor).toLowerCase()));
			const offset = Number(q.offset ?? 0);
			const limit = Number(q.limit ?? 50);
			return HttpResponse.json({ items: items.slice(offset, offset + limit), total: items.length, offset, limit });
		}),
	];
	return { handlers, log };
}
