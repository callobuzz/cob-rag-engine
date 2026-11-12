import { HttpResponse, http } from "msw";
import type { CorpusDetail } from "@/features/kbs/api";
import { BASE, CORPORA } from "../data";

export const PRESETS: Record<string, Record<string, number | boolean>> = {
	economy: {
		top_k: 10,
		final_k: 3,
		multi_query_count: 1,
		hyde_enabled: false,
		query_classification_enabled: false,
		reranking_enabled: false,
		mmr_enabled: false,
		mmr_lambda: 0.7,
		temporal_decay_enabled: false,
		validation_enabled: false,
		cache_enabled: true,
	},
	standard: {
		top_k: 20,
		final_k: 5,
		multi_query_count: 2,
		hyde_enabled: false,
		query_classification_enabled: false,
		reranking_enabled: true,
		mmr_enabled: true,
		mmr_lambda: 0.7,
		temporal_decay_enabled: true,
		validation_enabled: false,
		cache_enabled: true,
	},
	premium: {
		top_k: 40,
		final_k: 8,
		multi_query_count: 3,
		hyde_enabled: true,
		query_classification_enabled: true,
		reranking_enabled: true,
		mmr_enabled: true,
		mmr_lambda: 0.5,
		temporal_decay_enabled: true,
		validation_enabled: true,
		cache_enabled: true,
	},
};

/** PUT results per corpus id, so a refetch after a save sees the change (reset between tests). */
const saved = new Map<string, CorpusDetail>();
export function resetCorporaState(): void {
	saved.clear();
}

/** Detail for any corpus in CORPORA (Patient help carries the richest config). */
export function corpusDetail(id: string): CorpusDetail | null {
	const kept = saved.get(id);
	if (kept) return kept;
	const c = CORPORA.find((x) => x.id === id);
	if (!c) return null;
	const rich = c.name === "Patient help";
	return {
		...c,
		description: rich ? "Answers patients on shree-labs.in" : c.description,
		llm_config: rich
			? {
					provider: "openrouter",
					model: "openai/gpt-oss-20b",
					temperature: 0.2,
					max_tokens: 1024,
					api_key_encrypted: "***",
				}
			: null,
		prompt_config: rich
			? { system_prompt: "You are the assistant for Shree Labs, a pathology lab in Pune.", response_language: "Hindi" }
			: null,
		retrieval_config: rich ? { ...PRESETS.standard } : null,
		embedding_config: null,
		applied_embedding: { provider: "tei", model: "BAAI/bge-m3", dimensions: 1024 },
		migration_id: null,
	};
}

/** PUT semantics inside a section: a value sets the field, null removes it. */
function merge(existing: Record<string, unknown> | null | undefined, changes: Record<string, unknown>) {
	const out: Record<string, unknown> = { ...(existing ?? {}) };
	for (const [k, v] of Object.entries(changes)) {
		if (v === null) delete out[k];
		else out[k] = v;
	}
	return Object.keys(out).length ? out : null;
}

export const corporaHandlers = [
	http.get(`${BASE}/api/v1/corpora`, ({ request }) => {
		const tenant = new URL(request.url).searchParams.get("tenant_id");
		const items = tenant ? CORPORA.filter((c) => c.tenant_id === tenant) : CORPORA;
		return HttpResponse.json({ items, count: items.length, total: items.length, offset: 0, limit: 500 });
	}),
	http.get(`${BASE}/api/v1/corpora/:id`, ({ params }) => {
		const d = corpusDetail(String(params.id));
		return d ? HttpResponse.json(d) : HttpResponse.json({ detail: "Corpus not found" }, { status: 404 });
	}),
	http.post(`${BASE}/api/v1/corpora/:id/rotate-key`, ({ params }) =>
		HttpResponse.json({ api_key: `ck_${String(params.id).slice(0, 8)}_rotated_secret` }),
	),
	http.post(`${BASE}/api/v1/corpora`, async ({ request }) => {
		const body = (await request.json()) as { name: string; description?: string | null };
		return HttpResponse.json(
			{
				id: "5a5a5a5a-0000-4000-8000-00000000000a",
				name: body.name,
				description: body.description ?? null,
				qdrant_collection: "corpus_5a5a5a5a",
				api_key: "ck_5a5a5a5a_test_secret_value",
				created_at: "2026-09-30T10:00:00Z",
			},
			{ status: 201 },
		);
	}),
	http.put(`${BASE}/api/v1/corpora/:id`, async ({ params, request }) => {
		const d = corpusDetail(String(params.id));
		if (!d) return HttpResponse.json({ detail: "Corpus not found" }, { status: 404 });
		const body = (await request.json()) as Record<string, unknown>;
		const next: CorpusDetail = { ...d };
		for (const section of ["llm_config", "prompt_config", "retrieval_config"] as const) {
			const v = body[section];
			if (!v || typeof v !== "object") continue;
			const changes: Record<string, unknown> = { ...(v as Record<string, unknown>) };
			if (section === "retrieval_config" && typeof changes.preset === "string") {
				const { preset, ...rest } = changes;
				Object.assign(changes, PRESETS[preset as string], rest);
				delete changes.preset;
			}
			if (section === "llm_config" && "api_key" in changes) {
				changes.api_key_encrypted = changes.api_key === null ? null : "***";
				delete changes.api_key;
			}
			next[section] = merge(d[section], changes);
		}
		if (typeof body.tenant_id === "string") next.tenant_id = body.tenant_id;
		saved.set(d.id, next);
		return HttpResponse.json(next);
	}),
	http.delete(`${BASE}/api/v1/corpora/:id`, () => new HttpResponse(null, { status: 204 })),
];
