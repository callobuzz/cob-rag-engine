import { act, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BASE, BRANDING } from "@/test/msw/data";
import { publicCobState, resetPublicCobState } from "@/test/msw/handlers/cob-public";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";
import { historyFrom } from "./usePublicCob";

beforeEach(() => {
	resetPublicCobState();
	sessionStorage.clear();
	localStorage.clear();
});

const launcher = () => screen.findByRole("button", { name: "Ask COB" });
const dialog = () => screen.findByRole("dialog", { name: /COB, ask about the engine/ });

async function open(path = "/login") {
	const app = renderApp(path);
	await app.user.click(await launcher());
	return { ...app, panel: await dialog() };
}

async function ask(app: Awaited<ReturnType<typeof open>>, text: string) {
	const box = await screen.findByRole("textbox", { name: "Ask COB" });
	await app.user.type(box, `${text}{Enter}`);
}

describe("Ask COB on the sign-in pages", () => {
	it("is hidden when the engine does not offer it", async () => {
		server.use(http.get(`${BASE}/api/v1/public/branding`, () => HttpResponse.json({ ...BRANDING, cob_public: false })));
		renderApp("/login");
		await screen.findByLabelText("Email");
		expect(screen.queryByRole("button", { name: "Ask COB" })).not.toBeInTheDocument();
	});

	it("is on every sign-in-side page", async () => {
		for (const path of ["/login", "/forgot"]) {
			const app = renderApp(path);
			expect(await launcher()).toBeInTheDocument();
			app.unmount();
		}
	});

	it("opens with the greeting and three public starters, and says nothing is saved", async () => {
		const app = await open();
		expect(within(app.panel).getByText(/I can't see any account/)).toBeInTheDocument();
		const starters = within(app.panel).getByRole("list", { name: "Questions to start with" });
		expect(
			within(starters)
				.getAllByRole("button")
				.map((b) => b.textContent),
		).toEqual(["What is cob-rag-engine?", "Can I run it on my own server?", "How do I sign up?"]);
		expect(within(app.panel).getByText(/isn't saved/)).toBeInTheDocument();
	});

	it("streams an answer with a public source and a link to a public screen; no thumbs, history or cost", async () => {
		const app = await open();
		await app.user.click(within(app.panel).getByRole("button", { name: "What is cob-rag-engine?" }));
		const turn = await within(app.panel).findByTestId("cob-turn");
		expect(await within(turn).findByText(/self-hosted engine/)).toBeInTheDocument();
		const chip = await within(turn).findByRole("link", { name: /Open What is cob-rag-engine\? \(new tab\)/ });
		expect(chip).toHaveAttribute("href", "https://callobuzz.github.io/cob-rag-engine/guide.html");
		expect(within(turn).getByRole("link", { name: "Open Log in →" })).toHaveAttribute("href", "/login");
		expect(within(app.panel).queryByRole("button", { name: /Good answer|Bad answer/ })).not.toBeInTheDocument();
		expect(within(app.panel).queryByRole("button", { name: /history/i })).not.toBeInTheDocument();
		expect(within(app.panel).queryByTestId("cob-cost-note")).not.toBeInTheDocument();
		for (const a of Array.from(app.panel.querySelectorAll("a"))) {
			expect(a.getAttribute("href")).not.toMatch(/^\/docs/);
		}
		expect(publicCobState.chats).toEqual([{ message: "What is cob-rag-engine?" }]);
	});

	it("sends the earlier messages of this page view with the next question, at most six", async () => {
		const app = await open();
		for (const q of ["one", "two", "three", "four"]) {
			await ask(app, q);
			await waitFor(() => expect(publicCobState.chats.map((c) => c.message)).toContain(q));
			await within(app.panel).findAllByText(/self-hosted engine/);
			await waitFor(() => expect(screen.queryByTestId("cob-stage")).not.toBeInTheDocument());
		}
		const last = publicCobState.chats[3];
		expect(last?.history).toHaveLength(6);
		expect(last?.history?.[0]).toEqual({ role: "user", content: "one" });
		expect(last?.history?.[5]?.role).toBe("assistant");
		expect(publicCobState.chats[0]?.history).toBeUndefined();
	});

	it("keeps nothing in the browser", async () => {
		const setItem = vi.spyOn(Storage.prototype, "setItem");
		const app = await open();
		await ask(app, "hello");
		await within(await within(app.panel).findByTestId("cob-turn")).findByText(/self-hosted engine/);
		expect(setItem.mock.calls.filter(([k]) => /cob/i.test(String(k)))).toEqual([]);
		expect(localStorage.length).toBe(0);
		setItem.mockRestore();
	});

	it("429: says how long to wait, from Retry-After", async () => {
		const app = await open();
		await ask(app, "limit please");
		const alert = await within(app.panel).findByRole("alert");
		expect(alert).toHaveTextContent(/You've asked a lot of questions/);
		expect(alert).toHaveTextContent("10 minutes");
	});

	it("429: the wait counts down and Try again stays off until it is over", async () => {
		const app = await open();
		await ask(app, "limit please");
		const alert = await within(app.panel).findByRole("alert");
		expect(alert).toHaveTextContent("Try again in 10 minutes");
		expect(within(alert).getByRole("button", { name: "Try again" })).toBeDisabled();
	});

	it("429: Try again works once the wait is over", async () => {
		vi.useFakeTimers({ shouldAdvanceTime: true });
		try {
			const app = await open();
			await ask(app, "limit please");
			const alert = await within(app.panel).findByRole("alert");
			expect(within(alert).getByRole("button", { name: "Try again" })).toBeDisabled();
			await act(async () => {
				await vi.advanceTimersByTimeAsync(601_000);
			});
			expect(within(alert).getByRole("button", { name: "Try again" })).toBeEnabled();
		} finally {
			vi.useRealTimers();
		}
	});

	it("429 for the whole day says to come back tomorrow", async () => {
		const app = await open();
		await ask(app, "daily please");
		expect(await within(app.panel).findByRole("alert")).toHaveTextContent(/tomorrow/);
	});

	it("503: COB is briefly unavailable", async () => {
		const app = await open();
		await ask(app, "down");
		expect(await within(app.panel).findByRole("alert")).toHaveTextContent(/briefly unavailable/);
	});

	it("syncing: the first-start message", async () => {
		const app = await open();
		await ask(app, "sync");
		expect(await within(app.panel).findByRole("alert")).toHaveTextContent(/reading the docs for the first time/);
	});

	it("search mode shows the matching public pages", async () => {
		const app = await open();
		await ask(app, "search");
		const results = await within(app.panel).findByTestId("cob-search-results");
		expect(within(results).getByRole("link", { name: "Open" })).toHaveAttribute("target", "_blank");
	});

	it("a question over 1000 characters is refused here, not sent", async () => {
		const app = await open();
		const box = await screen.findByRole("textbox", { name: "Ask COB" });
		await app.user.click(box);
		await app.user.paste("x".repeat(1200));
		await app.user.keyboard("{Enter}");
		expect(await within(app.panel).findByRole("alert")).toHaveTextContent(/under 1000 characters/);
		expect(publicCobState.chats).toEqual([]);
	});
});

describe("historyFrom", () => {
	it("keeps answered turns only, newest last, at most six messages", () => {
		const turn = (q: string, status: "done" | "error", text = `a-${q}`) =>
			({ question: q, text, status }) as unknown as Parameters<typeof historyFrom>[0][number];
		const items = historyFrom([
			turn("1", "done"),
			turn("2", "done"),
			turn("3", "error", ""),
			turn("4", "done"),
			turn("5", "done"),
		]);
		expect(items).toHaveLength(6);
		expect(items[0]).toEqual({ role: "user", content: "2" });
		expect(items[5]).toEqual({ role: "assistant", content: "a-5" });
	});
});
