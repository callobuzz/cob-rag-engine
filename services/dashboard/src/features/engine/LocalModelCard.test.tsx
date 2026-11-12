import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, rt, SV, tokenResponse } from "@/test/msw/data";
import { createEngineMock } from "@/test/msw/handlers/engine";
import { localLlmState } from "@/test/msw/handlers/local-llm";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";
import type { LocalLlmStatus } from "./local-llm-api";

type Status = LocalLlmStatus;

const status = (over: Partial<Status>): Status => ({
	enabled: true,
	state: "ready",
	model: "gemma3:4b",
	models: ["gemma3:4b"],
	checked_at: "2026-10-05T08:00:00Z",
	...over,
});

function open(over: Partial<Status>) {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user: SV, tenant: null, tenants: [] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () =>
			HttpResponse.json({ user: SV, tenant: null, tenants: [], session_id: "s" }),
		),
		...createEngineMock().handlers,
	);
	localLlmState.status = status(over);
	return renderApp("/admin/engine");
}

const card = () => screen.findByRole("region", { name: "Free local model" });

describe("Engine info: free local model card", () => {
	it("says not set up, with the one-line way to turn it on", async () => {
		open({ enabled: false, state: "not_configured", models: [] });
		const c = await card();
		expect(await within(c).findByText("Not set up")).toBeInTheDocument();
		expect(within(c).getByText(/run the setup wizard/)).toBeInTheDocument();
		expect(within(c).queryByRole("button", { name: "Try it" })).not.toBeInTheDocument();
	});

	it("says the model is downloading, in plain words", async () => {
		open({ state: "pulling", models: [] });
		const c = await card();
		expect(await within(c).findByText("Downloading the model")).toBeInTheDocument();
		expect(within(c).getByText(/this can take a few minutes the first time/)).toBeInTheDocument();
		expect(within(c).queryByRole("button", { name: "Try it" })).not.toBeInTheDocument();
	});

	it("says it is starting", async () => {
		open({ state: "starting", models: [] });
		const c = await card();
		expect(await within(c).findByText("Starting")).toBeInTheDocument();
	});

	it("says it is ready and answers on the CPU", async () => {
		open({});
		const c = await card();
		expect(await within(c).findByText("Ready")).toBeInTheDocument();
		expect(within(c).getByText(/answers on this server's CPU/)).toBeInTheDocument();
		expect(within(c).getAllByText("gemma3:4b")).toHaveLength(2); // the model, and the models held
		expect(within(c).getByRole("button", { name: "Try it" })).toBeEnabled();
	});

	it("says it is not reachable", async () => {
		open({ state: "down", models: [] });
		const c = await card();
		expect(await within(c).findByText("Not reachable")).toBeInTheDocument();
	});

	it("shows how fast the model answered and waits before it can be asked again", async () => {
		const app = open({});
		const c = await card();
		await app.user.click(await within(c).findByRole("button", { name: "Try it" }));
		expect(await within(c).findByText("First words after 3.2 s · 9 words a second")).toBeInTheDocument();
		expect(within(c).getByText(/नमस्ते! OK/)).toBeInTheDocument();
		const waiting = within(c).getByRole("button", { name: "Try again in a few seconds" });
		expect(waiting).toBeDisabled(); // the engine allows one test every ten seconds
		expect(localLlmState.tests).toBe(1);
	});

	it("explains a refused test in words, not codes", async () => {
		const app = open({});
		localLlmState.testError = { status: 429, code: "local_llm_test_too_soon" };
		const c = await card();
		await app.user.click(await within(c).findByRole("button", { name: "Try it" }));
		await waitFor(() => expect(within(c).getByRole("alert")).toHaveTextContent("The test just ran."));
	});
});
