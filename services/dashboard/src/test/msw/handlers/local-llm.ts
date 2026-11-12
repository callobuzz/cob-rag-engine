import { HttpResponse, http } from "msw";
import type { LocalLlmStatus, LocalLlmTest } from "@/features/engine/local-llm-api";
import { BASE } from "../data";

/** PIP2: `GET /admin/local-llm` and `POST /admin/local-llm/test` (the Engine page's local model card). */
const NOW = "2026-10-05T08:00:00Z";

export const NOT_SET_UP: LocalLlmStatus = {
	enabled: false,
	state: "not_configured",
	model: "gemma3:4b",
	models: [],
	checked_at: NOW,
};

export const localLlmState: {
	status: LocalLlmStatus;
	test: LocalLlmTest;
	testError: { status: number; code: string } | null;
	tests: number;
} = {
	status: NOT_SET_UP,
	test: { ok: true, first_token_ms: 3200, total_ms: 6400, tokens_per_s: 9.4, answer: "नमस्ते! OK" },
	testError: null,
	tests: 0,
};

export function resetLocalLlmState() {
	localLlmState.status = NOT_SET_UP;
	localLlmState.test = { ok: true, first_token_ms: 3200, total_ms: 6400, tokens_per_s: 9.4, answer: "नमस्ते! OK" };
	localLlmState.testError = null;
	localLlmState.tests = 0;
}

export const localLlmHandlers = [
	http.get(`${BASE}/api/v1/admin/local-llm`, () => HttpResponse.json(localLlmState.status)),
	http.post(`${BASE}/api/v1/admin/local-llm/test`, () => {
		localLlmState.tests++;
		const err = localLlmState.testError;
		if (err) return HttpResponse.json({ detail: "x", code: err.code }, { status: err.status });
		return HttpResponse.json(localLlmState.test);
	}),
];
