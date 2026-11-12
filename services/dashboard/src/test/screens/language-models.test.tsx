import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { corpusDetail } from "../msw/handlers/corpora";
import { createEngineMock, FULL_INFO } from "../msw/handlers/engine";
import { server } from "../msw/server";
import { renderApp } from "../render";

const PATIENT = CORPORA[0]?.id ?? "";
const GEMMA = "google/gemma-4-31b-it";
const GPT_OSS = "openai/gpt-oss-120b";

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

/** The Model & keys tab, with the engine's language routes and the knowledge base's stored config. */
async function openModelTab(opts: { engine?: Record<string, string>; stored?: Record<string, unknown> | null } = {}) {
	signedInAs(PRIYA, SHREE);
	server.use(
		http.get(`${BASE}/api/v1/info`, () =>
			HttpResponse.json({ ...FULL_INFO, llm: { ...FULL_INFO.llm, language_models: opts.engine ?? {} } }),
		),
	);
	if (opts.stored) {
		const base = corpusDetail(PATIENT);
		server.use(
			http.get(`${BASE}/api/v1/corpora/${PATIENT}`, () =>
				HttpResponse.json({ ...base, llm_config: { ...base?.llm_config, language_models: opts.stored } }),
			),
		);
	}
	const bodies: Record<string, unknown>[] = [];
	server.events.on("request:start", async ({ request }) => {
		if (request.method === "PUT" && request.url.includes("/api/v1/corpora/")) {
			bodies.push((await request.clone().json()) as Record<string, unknown>);
		}
	});
	const app = renderApp(`/kbs/${PATIENT}?tab=model`);
	const mode = await screen.findByLabelText("Language switch");
	return { app, mode, bodies };
}

describe("Model per language on Model & keys", () => {
	it("starts on the engine's setting with no rows and sends nothing on save", async () => {
		const { app, mode, bodies } = await openModelTab();
		expect(mode).toHaveValue("default");
		expect(screen.queryByLabelText("Hindi")).toBeNull();
		expect(screen.getByText(/Engine setting: no language switch/)).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		expect(bodies).toHaveLength(0);
	});

	it("names the engine's routes in the hint", async () => {
		await openModelTab({ engine: { hi: GEMMA, "hi-Latn": GEMMA } });
		expect(
			screen.getByText(/Engine setting: Hindi: google\/gemma-4-31b-it, Hinglish: google\/gemma-4-31b-it/),
		).toBeInTheDocument();
	});

	it("choosing a model per language saves exactly that map", async () => {
		const { app, mode, bodies } = await openModelTab();
		await app.user.selectOptions(mode, "custom");
		const rows = await Promise.all(["English", "Hindi", "Hinglish"].map((n) => screen.findByLabelText(n)));
		for (const r of rows) expect(r).toHaveValue("");
		expect(within(rows[1] as HTMLElement).getByRole("option", { name: /Gemma 4 31B: 85% passed/ })).toBeInTheDocument();
		await app.user.selectOptions(rows[1] as HTMLElement, GEMMA);
		await app.user.selectOptions(rows[2] as HTMLElement, GEMMA);
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toEqual({ llm_config: { language_models: { hi: GEMMA, "hi-Latn": GEMMA } } });
	});

	it("a row left on the main model sends nothing for it", async () => {
		const { app, mode, bodies } = await openModelTab();
		await app.user.selectOptions(mode, "custom");
		await app.user.selectOptions(await screen.findByLabelText("English"), GPT_OSS);
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toEqual({ llm_config: { language_models: { en: GPT_OSS } } });
	});

	it("off sends an empty map: no language switch for this knowledge base", async () => {
		const { app, mode, bodies } = await openModelTab({ engine: { hi: GEMMA } });
		await app.user.selectOptions(mode, "off");
		expect(screen.queryByLabelText("Hindi")).toBeNull();
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toEqual({ llm_config: { language_models: {} } });
	});

	it("shows a stored map and keeps languages the form has no row for", async () => {
		const { app, mode, bodies } = await openModelTab({ stored: { hi: GEMMA, mr: "acme/marathi-1" } });
		expect(mode).toHaveValue("custom");
		expect(await screen.findByLabelText("Hindi")).toHaveValue(GEMMA);
		await app.user.selectOptions(screen.getByLabelText("Hinglish"), GEMMA);
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toEqual({
			llm_config: { language_models: { hi: GEMMA, "hi-Latn": GEMMA, mr: "acme/marathi-1" } },
		});
	});

	it("a stored empty map shows as off, and going back to the engine setting sends null", async () => {
		const { app, mode, bodies } = await openModelTab({ stored: {} });
		expect(mode).toHaveValue("off");
		await app.user.selectOptions(mode, "default");
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toEqual({ llm_config: { language_models: null } });
	});

	it("where the engine routes a language and the knowledge base has no model of its own, the main model is named", async () => {
		const base = corpusDetail(PATIENT);
		server.use(
			http.get(`${BASE}/api/v1/corpora/${PATIENT}`, () =>
				HttpResponse.json({ ...base, llm_config: { api_key_encrypted: "***" } }),
			),
		);
		const { app, mode, bodies } = await openModelTab({ engine: { hi: GEMMA } });
		await app.user.selectOptions(mode, "custom");
		await app.user.selectOptions(await screen.findByLabelText("English"), GPT_OSS);
		await app.user.click(screen.getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toEqual({
			llm_config: { language_models: { en: GPT_OSS, hi: FULL_INFO.llm.model } },
		});
	});
});

describe("engine page", () => {
	function openEngine(languageModels: Record<string, string>) {
		signedInAs(SV, null);
		server.use(
			http.get(`${BASE}/api/v1/info`, () =>
				HttpResponse.json({ ...FULL_INFO, llm: { ...FULL_INFO.llm, language_models: languageModels } }),
			),
			...createEngineMock().handlers,
		);
		return renderApp("/admin/engine");
	}

	it("lists the engine's answer model per language, read only", async () => {
		openEngine({ hi: GEMMA, "hi-Latn": GEMMA });
		expect(await screen.findByText("Answer model per language")).toBeInTheDocument();
		expect(screen.getByText(`Hindi: ${GEMMA}, Hinglish: ${GEMMA}`)).toBeInTheDocument();
	});

	it("says so when there is no language switch", async () => {
		openEngine({});
		expect(await screen.findByText("None: every language uses the answer model")).toBeInTheDocument();
	});
});
