import { HttpResponse, http } from "msw";
import { BASE, SHREE } from "../data";
import { SELF_KEY_ID } from "./api-keys";

export const ADMIN_KEY = "admin-key-for-tests-only";
export const TENANT_KEY = "tk_shree_test_key";
export const CORPUS_KEY = "ck_patient_help_test_key";

/** GET /info `retrieval_defaults`: the engine's `Settings` defaults (core/config.py). */
export const RETRIEVAL_DEFAULTS = {
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
	rerank_provider: "local" as const,
	rerank_llm_model: "openai/gpt-oss-20b",
	pipeline_order_enabled: false,
	boilerplate_strip: true,
	collapse_duplicates: true,
};

export const INFO = {
	version: "0.1.0",
	features: {
		content_portal: false,
		streaming: true,
		transcription: { enabled: false },
		reranker: { available: false },
		web_search: { enabled: false },
		accounts: true,
		cob: { enabled: true, public: true, corpus_id: null, docs_version: null },
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
	public_url: BASE,
	public_url_configured: true,
	limits: {
		max_upload_bytes: 52428800,
		max_document_tokens: 500000,
		allowed_mime_types: [
			"application/pdf",
			"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
			"text/csv",
			"text/plain",
		],
		allowed_extensions: [".csv", ".docx", ".pdf", ".txt"],
		max_records_per_request: 1000,
		max_record_chars: 100000,
		max_query_chars: 2000,
	},
	/** The engine's `Settings` defaults, as GET /info reports them. */
	retrieval_defaults: RETRIEVAL_DEFAULTS,
};

export const identityHandlers = [
	http.get(`${BASE}/api/v1/whoami`, ({ request }) => {
		const key = request.headers.get("x-api-key");
		const bearer = request.headers.get("authorization");
		if (key === ADMIN_KEY) return HttpResponse.json({ kind: "admin", auth: "api_key" });
		if (key === TENANT_KEY) {
			return HttpResponse.json({
				kind: "tenant",
				tenant_id: SHREE.id,
				tenant_name: SHREE.name,
				auth: "api_key",
				role: "owner",
				permission: "full",
				key_name: "Default key",
				key_id: SELF_KEY_ID,
			});
		}
		if (key === CORPUS_KEY) {
			return HttpResponse.json({
				kind: "corpus",
				corpus_id: "7b221003-0000-4000-8000-000000000001",
				corpus_name: "Patient help",
				tenant_id: SHREE.id,
				tenant_name: SHREE.name,
			});
		}
		if (bearer?.startsWith("Bearer ")) {
			return HttpResponse.json({
				kind: "tenant",
				tenant_id: SHREE.id,
				tenant_name: SHREE.name,
				auth: "session",
				role: "owner",
			});
		}
		if (key) return HttpResponse.json({ detail: "Invalid API key", code: "invalid_api_key" }, { status: 401 });
		return HttpResponse.json({ detail: "API key required", code: "auth_required" }, { status: 401 });
	}),
	http.get(`${BASE}/api/v1/info`, () => HttpResponse.json(INFO)),
];
