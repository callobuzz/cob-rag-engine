import { HttpResponse, http } from "msw";
import type { Migration } from "@/features/migrations/api";
import { BASE, CORPORA, SHREE } from "../data";

/**
 * e0 B6 migrations, stateful, following migration/service.py: approve (pending_approval → queued),
 * cancel (pending_approval/queued/running), retry (failed), rollback (completed and before
 * prune_after, else 410), approve-all. A tenant sees its own knowledge bases' migrations only.
 * `tick()` moves every queued/running migration forward (the worker).
 */
const PATIENT = CORPORA[0]?.id ?? "";
const STAFF = CORPORA[1]?.id ?? "";
const STORE = CORPORA[2]?.id ?? "";
const M3 = { provider: "tei", model: "BAAI/bge-m3", dimensions: 1024 };
const BASE_EN = { provider: "tei", model: "BAAI/bge-base-en-v1.5", dimensions: 768 };

function mig(i: number, over: Partial<Migration>): Migration {
	return {
		id: `4d000000-0000-4000-8000-${String(i).padStart(12, "0")}`,
		corpus_id: PATIENT,
		strategy: "blue_green",
		status: "pending_approval",
		from_config: M3,
		to_config: BASE_EN,
		old_collection: `corpus_${i}_v1`,
		new_collection: `corpus_${i}_v2`,
		chunks_total: 0,
		chunks_done: 0,
		memories_total: 0,
		memories_done: 0,
		error: null,
		created_at: `2026-09-${String(20 + i).padStart(2, "0")}T10:00:00+00:00`,
		approved_at: null,
		started_at: null,
		completed_at: null,
		prune_after: null,
		...over,
	};
}

export const MIGRATION_IDS = {
	pending: mig(1, {}).id,
	running: mig(2, {}).id,
	completed: mig(3, {}).id,
	failed: mig(4, {}).id,
	expired: mig(5, {}).id,
	other: mig(6, {}).id,
};

function seed(): Migration[] {
	return [
		mig(1, { corpus_id: PATIENT }),
		mig(2, {
			corpus_id: STAFF,
			status: "running",
			approved_at: "2026-09-22T10:05:00+00:00",
			started_at: "2026-09-22T10:06:00+00:00",
			chunks_total: 3100,
			chunks_done: 1240,
			memories_total: 40,
			memories_done: 12,
		}),
		mig(3, {
			corpus_id: PATIENT,
			status: "completed",
			from_config: BASE_EN,
			to_config: M3,
			chunks_total: 164,
			chunks_done: 164,
			approved_at: "2026-09-23T10:05:00+00:00",
			started_at: "2026-09-23T10:06:00+00:00",
			completed_at: "2026-09-23T10:20:00+00:00",
			prune_after: "2099-01-01T00:00:00+00:00",
		}),
		mig(4, {
			corpus_id: STAFF,
			status: "failed",
			error: "TEI returned 503 while embedding batch 7",
			chunks_total: 3100,
			chunks_done: 700,
		}),
		mig(5, {
			corpus_id: PATIENT,
			status: "completed",
			completed_at: "2026-09-01T10:20:00+00:00",
			prune_after: "2026-09-08T10:20:00+00:00",
			chunks_total: 10,
			chunks_done: 10,
		}),
		mig(6, { corpus_id: STORE }),
	];
}

export function createMigrationsMock(opts: { tenant?: boolean } = {}) {
	const state = { items: seed(), log: [] as { method: string; path: string; query?: Record<string, string> }[] };
	const visible = () =>
		opts.tenant
			? state.items.filter((m) => CORPORA.find((c) => c.id === m.corpus_id)?.tenant_id === SHREE.id)
			: state.items;
	const find = (id: string) => state.items.find((m) => m.id === id);
	const conflict = (detail: string) => HttpResponse.json({ detail }, { status: 409 });
	const act = (id: string, f: (m: Migration) => Response | Migration) => {
		const m = find(id);
		if (!m) return HttpResponse.json({ detail: "Migration not found" }, { status: 404 });
		if (opts.tenant) return HttpResponse.json({ detail: "Admin only" }, { status: 403 });
		const r = f(m);
		return r instanceof Response ? r : HttpResponse.json(r);
	};
	const tick = () => {
		for (const m of state.items) {
			if (m.status === "queued") {
				m.status = "running";
				m.started_at = "2026-10-01T10:01:00+00:00";
				m.chunks_total ||= 164;
			} else if (m.status === "running") {
				m.chunks_done = m.chunks_total;
				m.memories_done = m.memories_total;
				m.status = "completed";
				m.completed_at = "2026-10-01T10:05:00+00:00";
				m.prune_after = "2099-01-01T00:00:00+00:00";
			} else if (m.status === "rolling_back") {
				m.status = "rolled_back";
			}
		}
	};
	const handlers = [
		http.get(`${BASE}/api/v1/migrations`, ({ request }) => {
			const q = Object.fromEntries(new URL(request.url).searchParams);
			state.log.push({ method: "GET", path: "/api/v1/migrations", query: q });
			let items = visible();
			if (q.corpus_id) items = items.filter((m) => m.corpus_id === q.corpus_id);
			if (q.status) items = items.filter((m) => m.status === q.status);
			const offset = Number(q.offset ?? 0);
			const limit = Number(q.limit ?? 50);
			return HttpResponse.json({ items: items.slice(offset, offset + limit), total: items.length, offset, limit });
		}),
		http.get(`${BASE}/api/v1/migrations/:id`, ({ params }) => {
			const m = visible().find((x) => x.id === params.id);
			return m ? HttpResponse.json(m) : HttpResponse.json({ detail: "Migration not found" }, { status: 404 });
		}),
		http.post(`${BASE}/api/v1/migrations/:id/:action`, ({ params }) => {
			const id = String(params.id);
			const action = String(params.action);
			state.log.push({ method: "POST", path: `/api/v1/migrations/${id}/${action}` });
			return act(id, (m) => {
				if (action === "approve") {
					if (m.status !== "pending_approval")
						return conflict(`Cannot approve migration in status '${m.status}' (expected pending_approval)`);
					m.status = "queued";
					m.approved_at = "2026-10-01T10:00:00+00:00";
				} else if (action === "cancel") {
					if (!["pending_approval", "queued", "running"].includes(m.status))
						return conflict(`Cannot cancel migration in status '${m.status}'`);
					m.status = "cancelled";
				} else if (action === "retry") {
					if (m.status !== "failed")
						return conflict(`Cannot retry migration in status '${m.status}' (expected failed)`);
					m.status = "queued";
					m.error = null;
				} else if (action === "rollback") {
					if (m.status !== "completed")
						return conflict(`Cannot rollback migration in status '${m.status}' (expected completed)`);
					if (m.prune_after && new Date(m.prune_after).getTime() < Date.now())
						return HttpResponse.json(
							{
								detail: "Rollback window expired (old collection may have been pruned). Run a fresh migration instead.",
							},
							{ status: 410 },
						);
					m.status = "rolling_back";
				}
				return m;
			});
		}),
		http.post(`${BASE}/api/v1/admin/migrations/approve-all`, () => {
			state.log.push({ method: "POST", path: "/api/v1/admin/migrations/approve-all" });
			const ids: string[] = [];
			for (const m of state.items)
				if (m.status === "pending_approval") {
					m.status = "queued";
					ids.push(m.id);
				}
			return HttpResponse.json({ approved_ids: ids, count: ids.length });
		}),
	];
	return { state, handlers, tick };
}
