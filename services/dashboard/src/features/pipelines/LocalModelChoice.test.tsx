import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, rt, SV, tokenResponse } from "@/test/msw/data";
import { localLlmState } from "@/test/msw/handlers/local-llm";
import { ANSWER_BENCHMARKS } from "@/test/msw/handlers/models";
import { pipelinesState } from "@/test/msw/handlers/pipelines";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";

const ROWS = [
	{
		local: true,
		model: "gemma3:4b",
		size_mb: 3300,
		parameter_size: "4B",
		quantization: "Q4_K_M",
		items: 40,
		answered: 40,
		errors: 0,
		fact: { overall: 0.82, en: 0.9, hi: 0.78, "hi-Latn": 0.8 },
		refusal_on_answerable: 0.05,
		ttft_p50_ms: 3200,
		ttft_p90_ms: 5000,
		total_p50_ms: 21000,
		total_p90_ms: 33000,
		tokens_per_s: 9.4,
		passed: true,
		fails: [],
	},
	{
		local: true,
		model: "qwen3:0.6b",
		size_mb: 520,
		parameter_size: "0.6B",
		quantization: "Q4_K_M",
		items: 40,
		answered: 40,
		errors: 0,
		fact: { overall: 0.5, en: 0.6, hi: 0.4, "hi-Latn": 0.45 },
		refusal_on_answerable: 0.2,
		ttft_p50_ms: 1200,
		ttft_p90_ms: 2000,
		total_p50_ms: 9000,
		total_p90_ms: 14000,
		tokens_per_s: 20,
		passed: false,
		fails: ["fact_overall"],
	},
];

function openNew(local: unknown = { models: ROWS, best: "gemma3:4b", default: "gemma3:4b" }) {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user: SV, tenant: null, tenants: [] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () =>
			HttpResponse.json({ user: SV, tenant: null, tenants: [], session_id: "s" }),
		),
		http.get(`${BASE}/api/v1/models/answer-benchmarks`, () => HttpResponse.json({ ...ANSWER_BENCHMARKS, local })),
	);
	return renderApp("/admin/pipelines");
}

async function chooseLocal(user: ReturnType<typeof openNew>["user"]) {
	await user.click(await screen.findByRole("button", { name: "New profile" }));
	const dialog = within(await screen.findByRole("dialog"));
	await user.selectOptions(dialog.getByLabelText("Where answers are written"), "local");
	return dialog;
}

describe("Pipeline profile: the free local model", () => {
	it("lists the measured local models with speed and facts kept per language, and says which passed", async () => {
		localLlmState.status = {
			enabled: true,
			state: "ready",
			model: "gemma3:4b",
			models: ["gemma3:4b"],
			checked_at: "2026-10-05T08:00:00Z",
		};
		const { user } = openNew();
		const d = await chooseLocal(user);
		const list = await d.findByRole("list", { name: "Measured on this kind of server" });
		const rows = within(list).getAllByRole("listitem") as [HTMLElement, HTMLElement];
		expect(rows).toHaveLength(2);
		expect(within(rows[0]).getByText("gemma3:4b")).toBeInTheDocument();
		expect(within(rows[0]).getByText("Passed")).toBeInTheDocument();
		expect(within(rows[0]).getByText(/First words after 3 s, the whole answer in 21 s/)).toBeInTheDocument();
		expect(within(rows[0]).getByText(/English 90% · Hindi 78% · Hinglish 80%/)).toBeInTheDocument();
		expect(within(rows[1]).getByText("Slow and less accurate")).toBeInTheDocument();
	});

	it("choosing a row fills the model name", async () => {
		localLlmState.status = {
			enabled: true,
			state: "ready",
			model: "gemma3:4b",
			models: [],
			checked_at: "2026-10-05T08:00:00Z",
		};
		const { user } = openNew();
		const d = await chooseLocal(user);
		await user.click(await d.findByRole("button", { name: "Use this model: qwen3:0.6b" }));
		expect(d.getByLabelText("Answer model (on this server)")).toHaveValue("qwen3:0.6b");
		expect(d.getByRole("button", { name: "In use: qwen3:0.6b" })).toBeDisabled();
	});

	it("shows the live state and warns that businesses are paused until the model is ready", async () => {
		localLlmState.status = {
			enabled: true,
			state: "pulling",
			model: "gemma3:4b",
			models: [],
			checked_at: "2026-10-05T08:00:00Z",
		};
		const { user } = openNew();
		const d = await chooseLocal(user);
		const live = await d.findByTestId("local-state");
		expect(within(live).getByText("Downloading the model")).toBeInTheDocument();
		expect(within(live).getByText(/Downloading the model…/)).toBeInTheDocument();
		expect(
			await d.findByText("Businesses on this profile will see your pause message until the local model is ready."),
		).toBeInTheDocument();
	});

	it("does not warn when the local model is ready, and saves with the local route", async () => {
		localLlmState.status = {
			enabled: true,
			state: "ready",
			model: "gemma3:4b",
			models: ["gemma3:4b"],
			checked_at: "2026-10-05T08:00:00Z",
		};
		const { user } = openNew();
		const d = await chooseLocal(user);
		await d.findByTestId("local-state");
		expect(d.queryByText(/will see your pause message/)).not.toBeInTheDocument();
		await user.type(d.getByLabelText("Name"), "Free plus");
		await user.type(d.getByLabelText("What owners see"), "Economy plus");
		await user.click(d.getByRole("button", { name: "Create profile" }));
		await screen.findByRole("listitem", { name: "Free plus" });
		const sent = pipelinesState.sent.at(-1)?.body as { config: Record<string, unknown> };
		expect(sent.config).toMatchObject({ answer_route: "local", overrides_kb_settings: true });
	});

	it("says when there are no measured results yet", async () => {
		localLlmState.status = {
			enabled: false,
			state: "not_configured",
			model: "gemma3:4b",
			models: [],
			checked_at: "2026-10-05T08:00:00Z",
		};
		const { user } = openNew(null);
		const d = await chooseLocal(user);
		expect(await d.findByText(/No measured results are available yet/)).toBeInTheDocument();
	});
});
