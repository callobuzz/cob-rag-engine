import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { perThousand } from "@/features/benchmarks/api";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, ROHAN, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { createEngineMock, FULL_INFO } from "../msw/handlers/engine";
import { ANSWER_BENCHMARKS, FAST_BENCHMARKS } from "../msw/handlers/models";
import { server } from "../msw/server";
import { renderApp } from "../render";

const PATIENT = CORPORA[0]?.id ?? "";

afterEach(() => server.events.removeAllListeners());

function signedInAs(user: typeof PRIYA, tenant: typeof SHREE | null) {
	const tenants = tenant ? [tenant] : [];
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () => HttpResponse.json(tokenResponse({ user, tenant, tenants }, 2))),
		http.get(`${BASE}/api/v1/auth/me`, () => HttpResponse.json({ user, tenant, tenants, session_id: "s" })),
	);
	if (tenant) {
		server.use(
			http.get(`${BASE}/api/v1/corpora`, () => {
				const items = CORPORA.filter((c) => c.tenant_id === SHREE.id);
				return HttpResponse.json({ items, count: items.length, total: items.length, offset: 0, limit: 25 });
			}),
		);
	}
}

/** The real results, with the cheapest model pinned to a host, as the picker sends it. */
const PINNED = {
	available: true,
	benchmarks: {
		...FAST_BENCHMARKS.benchmarks,
		models: FAST_BENCHMARKS.benchmarks.models.map((m) =>
			m.model === "openai/gpt-oss-120b"
				? { ...m, key: `${m.model}@deepinfra/turbo`, provider_order: ["deepinfra/turbo"] }
				: m,
		),
	},
};

/** The fast-model input (the label also carries "optional"; hosts and the picker list share its words). */
const fastModelInput = () =>
	screen
		.getAllByLabelText(/Fast model/)
		.find((e) => e.id && e.tagName === "INPUT" && (e as HTMLInputElement).getAttribute("list")) as HTMLElement;

describe("fast-model picker", () => {
	async function openModelTab(user: typeof PRIYA = PRIYA, tenant: typeof SHREE = SHREE) {
		signedInAs(user, tenant);
		server.use(http.get(`${BASE}/api/v1/models/fast-benchmarks`, () => HttpResponse.json(PINNED)));
		const bodies: Record<string, unknown>[] = [];
		server.events.on("request:start", async ({ request }) => {
			if (request.method === "PUT" && request.url.includes("/api/v1/corpora/")) {
				bodies.push((await request.clone().json()) as Record<string, unknown>);
			}
		});
		const app = renderApp(`/kbs/${PATIENT}?tab=model`);
		const list = await screen.findByRole("list", { name: "Benchmarked fast models" });
		return { app, list, bodies };
	}

	it("shows badges, numbers and the below-the-bar warning with the failed bar", async () => {
		const { list } = await openModelTab();
		const rows = within(list).getAllByRole("listitem");
		expect(rows).toHaveLength(15);
		const luna = rows.find((r) => within(r).queryByText("openai/gpt-6-luna")) as HTMLElement;
		expect(within(luna).getByText("Recommended")).toBeInTheDocument();
		expect(within(luna).queryByText("Below the quality bar")).toBeNull();
		expect(within(luna).getByText(/Accuracy 98\.9%/)).toBeInTheDocument();
		expect(within(luna).getByText(/p90 1\.94 s/)).toBeInTheDocument();
		const gemma = rows.find((r) => within(r).queryByText("google/gemma-4-26b-a4b-it")) as HTMLElement;
		expect(within(gemma).getByText("Budget")).toBeInTheDocument();
		expect(within(gemma).getByText("Cheapest")).toBeInTheDocument();
		const lite = rows.find((r) => within(r).queryByText("google/gemini-2.5-flash-lite")) as HTMLElement;
		expect(within(lite).getByText("Below the quality bar")).toBeInTheDocument();
		expect(within(lite).getByText(/Misses: business questions kept/)).toBeInTheDocument();
		expect(within(lite).getByText(`${perThousand(0.134, "INR")} per 1,000 messages`)).toBeInTheDocument();
	});

	it("offers the budget model with the saving, from the data, and one click saves it", async () => {
		const { app, bodies } = await openModelTab();
		const callout = await screen.findByRole("region", { name: "Budget option" });
		expect(within(callout).getByRole("heading", { name: "Save 64%: switch to Gemma 4 26B" })).toBeInTheDocument();
		expect(
			within(callout).getByText(
				`${perThousand(0.058, "INR")} instead of ${perThousand(0.162, "INR")} per 1,000 messages, and it is 97.7% accurate.`,
			),
		).toBeInTheDocument();
		await app.user.click(within(callout).getByRole("button", { name: "Switch to budget" }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toEqual({
			llm_config: { fast_model: "google/gemma-4-26b-a4b-it", fast_provider_order: null },
		});
		expect(await screen.findByText("Fast model is now Gemma 4 26B")).toBeInTheDocument();
	});

	it("a member sees the callout but the button is locked", async () => {
		await openModelTab(ROHAN, { ...SHREE, role: "member" });
		const callout = await screen.findByRole("region", { name: "Budget option" });
		expect(within(callout).getByRole("button", { name: "Switch to budget" })).toHaveAttribute("aria-disabled", "true");
	});

	it("no budget pick in the data means no callout", async () => {
		signedInAs(PRIYA, SHREE);
		const none = {
			...PINNED,
			benchmarks: { ...PINNED.benchmarks, recommendations: { ...PINNED.benchmarks.recommendations, budget: null } },
		};
		server.use(http.get(`${BASE}/api/v1/models/fast-benchmarks`, () => HttpResponse.json(none)));
		renderApp(`/kbs/${PATIENT}?tab=model`);
		await screen.findByRole("list", { name: "Benchmarked fast models" });
		expect(screen.queryByRole("region", { name: "Budget option" })).toBeNull();
	});

	it("choosing a row fills the model and host, and Save sends both", async () => {
		const { app, bodies } = await openModelTab();
		await app.user.click(screen.getByRole("button", { name: "Use openai/gpt-oss-120b@deepinfra/turbo" }));
		expect(fastModelInput()).toHaveValue("openai/gpt-oss-120b");
		expect(screen.getByLabelText(/Fast model hosts/)).toHaveValue("deepinfra/turbo");
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toEqual({
			llm_config: { fast_model: "openai/gpt-oss-120b", fast_provider_order: ["deepinfra/turbo"] },
		});
	});

	it("typing a model that is below the bar warns under the field; emptied hosts send null", async () => {
		const { app, bodies } = await openModelTab();
		const hosts = screen.getByLabelText(/Fast model hosts/);
		await app.user.type(hosts, "Groq, cerebras");
		await app.user.type(fastModelInput(), "google/gemma-4-31b-it");
		expect(await screen.findAllByText("Below the quality bar")).toHaveLength(10); // nine failing rows + the field note
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toEqual({
			llm_config: { fast_model: "google/gemma-4-31b-it", fast_provider_order: ["groq", "cerebras"] },
		});
		await app.user.clear(screen.getByLabelText(/Fast model hosts/));
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(bodies).toHaveLength(2));
		expect(bodies[1]).toEqual({ llm_config: { fast_provider_order: null } });
	});
});

describe("answer-model picker", () => {
	it("shows the models that meet the bar with numbers and badges, and Use fills the main model", async () => {
		signedInAs(PRIYA, SHREE);
		const bodies: Record<string, unknown>[] = [];
		server.events.on("request:start", async ({ request }) => {
			if (request.method === "PUT" && request.url.includes("/api/v1/corpora/")) {
				bodies.push((await request.clone().json()) as Record<string, unknown>);
			}
		});
		const app = renderApp(`/kbs/${PATIENT}?tab=model`);
		const list = await screen.findByRole("list", { name: "Benchmarked answer models" });
		const rows = within(list).getAllByRole("listitem");
		expect(rows.length).toBeGreaterThanOrEqual(3);
		expect(rows.length).toBeLessThanOrEqual(5);
		const oss = rows.find((r) => within(r).queryByText("openai/gpt-oss-120b")) as HTMLElement;
		expect(within(oss).getByText("Recommended")).toBeInTheDocument();
		expect(within(oss).getByText("Cheapest")).toBeInTheDocument();
		expect(within(oss).getByText("Self-hostable")).toBeInTheDocument();
		expect(within(oss).getByText("Passed 93%")).toBeInTheDocument();
		expect(within(oss).getByText("Declines when unsure 100%")).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Show all 19" }));
		const haiku = within(list)
			.getAllByRole("listitem")
			.find((r) => within(r).queryByText("anthropic/claude-haiku-4.5")) as HTMLElement;
		expect(within(haiku).getByText("Below the quality bar")).toBeInTheDocument();
		expect(within(haiku).getByText(/invents answers to questions it cannot know/)).toBeInTheDocument();
		expect(within(haiku).getByText("Best Hindi tone")).toBeInTheDocument();
		await app.user.click(within(oss).getByRole("button", { name: "Use openai/gpt-oss-120b" }));
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toEqual({ llm_config: { model: "openai/gpt-oss-120b" } });
	});

	it("a language select shows the numbers of the chosen model and its badges", async () => {
		signedInAs(PRIYA, SHREE);
		const app = renderApp(`/kbs/${PATIENT}?tab=model`);
		await app.user.selectOptions(await screen.findByLabelText("Language switch"), "custom");
		const hindi = await screen.findByLabelText("Hindi");
		await app.user.selectOptions(hindi, "google/gemma-4-31b-it");
		const row = hindi.closest("fieldset")?.querySelectorAll("div.grid")[0]?.parentElement ?? document.body;
		expect(
			await within(row as HTMLElement).findByText(/85% passed, 100% declined correctly, 100% right register/),
		).toBeInTheDocument();
		expect(within(row as HTMLElement).getByText("Recommended for Hindi")).toBeInTheDocument();
	});
});

describe("engine fast-model card", () => {
	function openEngine(llm: Record<string, unknown>) {
		signedInAs(SV, null);
		server.use(
			http.get(`${BASE}/api/v1/info`, () => HttpResponse.json({ ...FULL_INFO, llm: { ...FULL_INFO.llm, ...llm } })),
			...createEngineMock().handlers,
		);
		return renderApp("/admin/engine");
	}

	it("shows the configured model's metrics, hosts and the benchmarks link", async () => {
		openEngine({ fast_model: "openai/gpt-6-luna", fast_provider_order: ["groq"] });
		const card = await screen.findByRole("region", { name: "Fast model benchmark" });
		expect(await within(card).findByText(/^\d+\.\d%$/)).toBeInTheDocument();
		expect(within(card).getByText("Recommended")).toBeInTheDocument();
		expect(within(card).getByText("groq")).toBeInTheDocument();
		expect(within(card).getByRole("link", { name: "See all benchmarks" })).toHaveAttribute("href", "/benchmarks");
	});

	it("offers the budget model when the engine's fast model is not it, with a line to copy", async () => {
		openEngine({ fast_model: "openai/gpt-6-luna", fast_provider_order: [] });
		const card = await screen.findByRole("region", { name: "Fast model benchmark" });
		const callout = await within(card).findByRole("region", { name: "Budget option" });
		expect(within(callout).getByRole("heading", { name: "Save 64%: switch to Gemma 4 26B" })).toBeInTheDocument();
		expect(within(callout).getByRole("button", { name: "Copy the budget fast model setting" })).toBeInTheDocument();
		expect(within(card).getByText(/RAG_LLM_FAST_MODEL=google\/gemma-4-26b-a4b-it/)).toBeInTheDocument();
	});

	it("no callout when the engine already runs the budget model", async () => {
		openEngine({ fast_model: "google/gemma-4-26b-a4b-it", fast_provider_order: [] });
		const card = await screen.findByRole("region", { name: "Fast model benchmark" });
		expect(await within(card).findByText("Budget")).toBeInTheDocument();
		expect(within(card).queryByRole("region", { name: "Budget option" })).toBeNull();
	});

	it("says so when the model has no benchmark row, and names the recommended one", async () => {
		openEngine({ fast_model: "acme/unknown-mini", fast_provider_order: [] });
		const card = await screen.findByRole("region", { name: "Fast model benchmark" });
		expect(await within(card).findByText(/Not benchmarked/)).toBeInTheDocument();
		expect(within(card).getByText("openai/gpt-6-luna")).toBeInTheDocument();
	});
});

describe("Benchmarks page", () => {
	it("leads with the recommended setup, computed from the data", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/benchmarks");
		const hero = await screen.findByRole("region", { name: "Recommended setup" });
		const tiles = within(hero).getAllByRole("listitem");
		expect(tiles).toHaveLength(4);
		expect(within(tiles[0] as HTMLElement).getByText("gpt-oss-120b", { selector: "strong" })).toBeInTheDocument();
		expect(within(tiles[0] as HTMLElement).getByText(/Best overall: 93% of answers passed/)).toBeInTheDocument();
		expect(within(tiles[1] as HTMLElement).getByText("Gemma 4 31B")).toBeInTheDocument();
		expect(
			within(tiles[1] as HTMLElement).getByText(/90% passed on Hindi and Hinglish.*100% in the right register/),
		).toBeInTheDocument();
		expect(within(tiles[2] as HTMLElement).getByText("GPT-6 Luna")).toBeInTheDocument();
		expect(within(tiles[3] as HTMLElement).getByText("Gemma 4 26B")).toBeInTheDocument();
		expect(within(tiles[3] as HTMLElement).getByText(/64% less than GPT-6 Luna/)).toBeInTheDocument();
	});

	it("answer models: badges, a language filter, sorting, dimmed rows with the reason, the currency toggle", async () => {
		signedInAs(PRIYA, SHREE);
		const app = renderApp("/benchmarks");
		const table = await screen.findByRole("table", { name: "Answer-model benchmark results: All" });
		const heads = within(table)
			.getAllByRole("columnheader")
			.map((h) => h.textContent?.trim());
		expect(heads).toEqual(
			expect.arrayContaining([
				"Model",
				"Passed",
				"Declines when unsure",
				"Tables",
				"First word",
				"Naturalness",
				"Register",
				"Result",
			]),
		);
		const rows = within(table).getAllByRole("row");
		expect(rows).toHaveLength(20);
		const first = rows[1] as HTMLElement;
		expect(within(first).getByText("openai/gpt-oss-120b")).toBeInTheDocument();
		for (const badge of ["Recommended", "Most accurate", "Cheapest", "Self-hostable"]) {
			expect(within(first).getByText(badge)).toBeInTheDocument();
		}
		expect(within(first).getByText("thinking low")).toBeInTheDocument();
		const haiku = rows.find((r) => within(r).queryByText("anthropic/claude-haiku-4.5")) as HTMLElement;
		expect(within(haiku).getByText("Best Hindi tone")).toBeInTheDocument();
		expect(within(haiku).getByText("Below the quality bar")).toBeInTheDocument();
		expect(haiku.className).toContain("bg-muted");
		const gemma = rows.find((r) => within(r).queryByText("google/gemma-4-31b-it")) as HTMLElement;
		expect(within(gemma).getByText("Recommended for Hindi")).toBeInTheDocument();
		expect(within(gemma).getByText("thinking off")).toBeInTheDocument();

		await app.user.click(screen.getByRole("radio", { name: "Hindi + Hinglish" }));
		const hindi = await screen.findByRole("table", { name: "Answer-model benchmark results: Hindi + Hinglish" });
		const hindiRows = within(hindi).getAllByRole("row");
		const g = hindiRows.find((r) => within(r).queryByText("google/gemma-4-31b-it")) as HTMLElement;
		expect(within(g).getByText("90%")).toBeInTheDocument();
		expect(within(g).getByText("3.8 / 5")).toBeInTheDocument();
		const oss = hindiRows.find((r) => within(r).queryByText("openai/gpt-oss-120b")) as HTMLElement;
		expect(within(oss).getByText(/Not for Hindi: register under 95%/)).toBeInTheDocument();

		await app.user.click(screen.getByRole("radio", { name: "English" }));
		const en = await screen.findByRole("table", { name: "Answer-model benchmark results: English" });
		expect(within(en).getAllByText("Not graded").length).toBeGreaterThan(0);

		await app.user.click(screen.getByRole("radio", { name: "$" }));
		expect(within(en).getAllByText(/^\$\d/).length).toBeGreaterThan(0);
		await app.user.click(screen.getByRole("radio", { name: "₹" }));
		expect(within(en).queryAllByText(/^\$\d/)).toHaveLength(0);

		await app.user.click(within(en).getByRole("button", { name: /Passed/ }));
		expect(within(en).getByRole("columnheader", { name: /Passed/ })).toHaveAttribute("aria-sort", "ascending");
	});

	it("the method drawer says what was tested, the bars, the date and what it cost", async () => {
		signedInAs(PRIYA, SHREE);
		const app = renderApp("/benchmarks");
		await screen.findByRole("table", { name: /Answer-model benchmark results/ });
		await app.user.click(screen.getByRole("button", { name: "Method" }));
		const drawer = await screen.findByRole("dialog", { name: "How this was measured" });
		expect(within(drawer).getByText(/94 frozen items: 54 English, 20 Hindi, 20 Hinglish/)).toBeInTheDocument();
		expect(within(drawer).getByText(/At least 80% of answers pass/)).toBeInTheDocument();
		expect(within(drawer).getAllByText(/Measured on 2026-10-03/).length).toBeGreaterThan(0);
		expect(within(drawer).getByText(/Cost of this benchmark: \$0\.\d\d/)).toBeInTheDocument();
		expect(within(drawer).getByRole("link", { name: /Open the harness/ })).toHaveAttribute(
			"href",
			ANSWER_BENCHMARKS.benchmarks.method_link,
		);
	});

	it("fast models: every bar, a turned-away warning and dimmed rows below the bar", async () => {
		signedInAs(PRIYA, SHREE);
		const app = renderApp("/benchmarks");
		await app.user.click(await screen.findByRole("tab", { name: "Fast models" }));
		const table = await screen.findByRole("table", { name: "Fast-model benchmark results" });
		const heads = within(table)
			.getAllByRole("columnheader")
			.map((h) => h.textContent?.trim());
		expect(heads).toEqual(
			expect.arrayContaining(["Model", "Overall", "English", "Hindi", "Hinglish", "Turned away", "p50", "p90", "p99"]),
		);
		const rows = within(table).getAllByRole("row");
		expect(rows).toHaveLength(16);
		const llama = rows.find((r) => within(r).queryByText("meta-llama/llama-3.3-70b-instruct @ groq")) as HTMLElement;
		expect(within(llama).getByText("Too many turned away")).toBeInTheDocument();
		expect(within(llama).getByText("Below the quality bar")).toBeInTheDocument();
		expect(llama.className).toContain("bg-muted");
		const luna = rows.find((r) => within(r).queryByText("openai/gpt-6-luna")) as HTMLElement;
		expect(within(luna).getByText("Recommended")).toBeInTheDocument();
		expect(within(luna).queryByText("Too many turned away")).toBeNull();
		const gemma = rows.find((r) => within(r).queryByText("google/gemma-4-26b-a4b-it")) as HTMLElement;
		expect(within(gemma).getByText("Budget")).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Method" }));
		const drawer = await screen.findByRole("dialog", { name: "How this was measured" });
		expect(within(drawer).getByText(/266 held-out turns/)).toBeInTheDocument();
	});

	it("retrieval: each summary section as a table", async () => {
		signedInAs(PRIYA, SHREE);
		const app = renderApp("/benchmarks");
		await app.user.click(await screen.findByRole("tab", { name: "Retrieval" }));
		const summary = await screen.findByRole("table", { name: "Retrieval quality on BEIR (nDCG@10)" });
		expect(within(summary).getByRole("columnheader", { name: "SciFact" })).toBeInTheDocument();
		expect(screen.getAllByRole("link", { name: /Method/ }).length).toBeGreaterThanOrEqual(2);
	});

	it("available=false shows the empty state", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.get(`${BASE}/api/v1/models/fast-benchmarks`, () =>
				HttpResponse.json({ available: false, benchmarks: null }),
			),
			http.get(`${BASE}/api/v1/models/answer-benchmarks`, () =>
				HttpResponse.json({ available: false, benchmarks: null }),
			),
			http.get(`${BASE}/api/v1/models/benchmark-summary`, () => HttpResponse.json({ available: false, summary: null })),
		);
		const app = renderApp("/benchmarks");
		expect(await screen.findByText("No benchmark results")).toBeInTheDocument();
		expect(screen.queryByRole("table")).toBeNull();
		expect(screen.queryByRole("region", { name: "Recommended setup" })).toBeNull();
		await app.user.click(screen.getByRole("tab", { name: "Fast models" }));
		expect(await screen.findByText("No benchmark results")).toBeInTheDocument();
	});
});
