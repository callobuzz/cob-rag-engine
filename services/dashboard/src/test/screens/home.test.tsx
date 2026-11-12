import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WidgetKey } from "@/features/widget/api";
import { onCobOpen } from "@/lib/cob-bus";
import { BASE, PRIYA, SHREE } from "../msw/data";
import { homeFor, homeState } from "../msw/handlers/home";
import { WIDGET_KEYS } from "../msw/handlers/widget-keys";
import { server } from "../msw/server";
import { renderApp } from "../render";
import { PATIENT, signedInAs } from "./assistant-utils";

async function openHome() {
	const app = renderApp("/");
	await screen.findByRole("heading", { level: 1, name: "Home" });
	return app;
}

const noKbs = () =>
	http.get(`${BASE}/api/v1/corpora`, () => HttpResponse.json({ items: [], count: 0, total: 0, offset: 0, limit: 500 }));

describe("business Home", () => {
	it("a greeting by name, the assistant live with its own name, and the website it learned", async () => {
		signedInAs(PRIYA, SHREE);
		await openHome();
		expect(screen.getByTestId("greeting")).toHaveTextContent(/^Good (morning|afternoon|evening), Priya$/);
		const status = await screen.findByTestId("assistant-status");
		expect(status).toHaveTextContent("Shree Dental Assistant is live");
		expect(status).toHaveAttribute("data-state", "live");
		expect(screen.getByText("for Shree Dental Clinic, Pune")).toBeInTheDocument();
		const site = screen.getByTestId("website-status");
		expect(within(site).getByText("shreedental.in")).toBeInTheDocument();
		expect(within(site).getByText(/12 pages learned · last updated 2 hours ago/)).toBeInTheDocument();
	});

	it("today in the business's day: conversations, contact details and people waiting", async () => {
		signedInAs(PRIYA, SHREE);
		await openHome();
		expect(await screen.findByTestId("today-conversations")).toHaveTextContent("3People who chatted");
		expect(screen.getByTestId("today-leads")).toHaveTextContent("1Contact details left");
		expect(screen.getByTestId("today-waiting")).toHaveTextContent("2Waiting for a call back");
		expect(screen.getByTestId("today-leads")).toHaveAttribute("href", "/leads");
		expect(screen.getByText("2026-10-05")).toBeInTheDocument();
	});

	it("asks for the workspace's main knowledge base by id, so every page agrees", async () => {
		signedInAs(PRIYA, SHREE);
		await openHome();
		await waitFor(() => expect(homeState.requests).toContain(PATIENT));
	});

	it("an assistant that is not published says what to do", async () => {
		signedInAs(PRIYA, SHREE);
		homeState.over[PATIENT] = {
			assistant: {
				enabled: false,
				published: false,
				name: "Shree Dental Assistant",
				avatar_url: null,
				business_name: null,
			},
		};
		await openHome();
		expect(await screen.findByTestId("assistant-status")).toHaveTextContent("Your assistant is not published yet");
		expect(screen.getByRole("link", { name: "Set it up" })).toHaveAttribute("href", "/assistant");
	});

	it("a switched-off assistant is named as off", async () => {
		signedInAs(PRIYA, SHREE);
		homeState.over[PATIENT] = {
			assistant: { enabled: false, published: true, name: "Asha", avatar_url: null, business_name: null },
		};
		await openHome();
		expect(await screen.findByTestId("assistant-status")).toHaveTextContent("Asha is switched off");
	});

	it("no website yet invites the owner to add one; learning off says it is not in the plan", async () => {
		signedInAs(PRIYA, SHREE);
		homeState.over[PATIENT] = { website: { sites: [], learning: true, connected: false } };
		await openHome();
		const site = await screen.findByTestId("website-status");
		expect(within(site).getByText("No website connected yet")).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Add your website" })).toBeInTheDocument();
	});

	it("website learning off in the plan", async () => {
		signedInAs(PRIYA, SHREE);
		homeState.over[PATIENT] = { website: { sites: [], learning: false, connected: false } };
		await openHome();
		const site = await screen.findByTestId("website-status");
		expect(within(site).getByText("Website learning is not included in your plan")).toBeInTheDocument();
		expect(screen.queryByRole("link", { name: "Add your website" })).toBeNull();
	});

	it("a site being read or failing is described in words", async () => {
		signedInAs(PRIYA, SHREE);
		homeState.over[PATIENT] = {
			website: {
				learning: true,
				connected: false,
				sites: [
					{ origin: "https://shreedental.in", status: "learning", pages_learned: 3, last_updated_at: null },
					{ origin: "https://old.shreedental.in", status: "failed", pages_learned: 0, last_updated_at: null },
				],
			},
		};
		await openHome();
		const site = await screen.findByTestId("website-status");
		expect(within(site).getByText("Reading your website now")).toBeInTheDocument();
		expect(within(site).getByText("We couldn't read your website")).toBeInTheDocument();
	});

	it("the plan card shows the plan and what is left this month", async () => {
		signedInAs(PRIYA, SHREE);
		await openHome();
		const plan = await screen.findByTestId("home-plan");
		await waitFor(() => expect(plan).toHaveTextContent("Pro plan · 2 of 3 knowledge bases"));
		expect(plan).toHaveTextContent("1,904 of 5,000 questions this month");
		expect(
			within(plan.closest("section") as HTMLElement).getByRole("link", { name: "Plan & billing" }),
		).toHaveAttribute("href", "/plan");
	});

	it("quick actions: copy the website code, write an article, try the assistant", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await openHome();
		const copy = await screen.findByRole("button", { name: "Copy your website code" });
		expect(copy).toBeInTheDocument();
		await app.user.click(copy);
		expect(await screen.findByText(/Copied\. Paste it just before/)).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Write an article" })).toHaveAttribute(
			"href",
			"/knowledge?tab=articles&new=article",
		);
		// BIZ4: Try your assistant opens the chat in a dialog (it was a link to the playground)
		await app.user.click(screen.getByRole("button", { name: "Try your assistant" }));
		expect(await screen.findByRole("dialog", { name: "Try your assistant" })).toBeInTheDocument();
	});

	it("copies the code of a working key, not of a paused or empty one", async () => {
		signedInAs(PRIYA, SHREE);
		const key = (id: string, over: Partial<WidgetKey>): WidgetKey => ({
			...(WIDGET_KEYS[0] as WidgetKey),
			id,
			embed_snippet: `<script data-key="${id}"></script>`,
			...over,
		});
		server.use(
			http.get(`${BASE}/api/v1/corpora/:id/widget-keys`, () =>
				HttpResponse.json([
					key("paused", { is_active: false }),
					key("no-site", { allowed_origins: [] }),
					key("works", {}),
				]),
			),
		);
		const app = await openHome();
		const writeText = vi.spyOn(navigator.clipboard, "writeText");
		await app.user.click(await screen.findByRole("button", { name: "Copy your website code" }));
		await waitFor(() => expect(writeText).toHaveBeenCalledWith('<script data-key="works"></script>'));
	});

	it("with every key paused, Home links to the widget page instead of copying", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.get(`${BASE}/api/v1/corpora/:id/widget-keys`, () =>
				HttpResponse.json(WIDGET_KEYS.map((k) => ({ ...k, is_active: false }))),
			),
		);
		await openHome();
		expect(await screen.findByRole("link", { name: "Get your website code" })).toBeInTheDocument();
		expect(screen.queryByRole("button", { name: "Copy your website code" })).toBeNull();
	});

	it("tips from COB open COB with the question ready", async () => {
		signedInAs(PRIYA, SHREE);
		const opened: { prompt?: string; send?: boolean }[] = [];
		const off = onCobOpen((req) => opened.push(req));
		try {
			const app = await openHome();
			const tips = await screen.findByRole("region", { name: "Tips from COB" });
			expect(within(tips).getAllByRole("button", { name: /^Open “.+” in COB$/ })).toHaveLength(3);
			await app.user.click(within(tips).getByRole("button", { name: "Open “Add your most asked questions” in COB" }));
			expect(opened[0]?.prompt).toMatch(/best questions and answers/);
			expect(opened[0]?.send).toBe(false);
		} finally {
			off();
		}
	});

	it("a business with no knowledge base yet is sent to create one, in the product's words", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			noKbs(),
			http.get(`${BASE}/api/v1/home`, () => HttpResponse.json(homeFor(null))),
		);
		await openHome();
		expect(await screen.findByRole("heading", { name: "Set up your first assistant" })).toBeInTheDocument();
		expect(screen.getByText(/Give Sahayak AI your website/)).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Create a knowledge base" })).toHaveAttribute("href", "/kbs?new=1");
	});

	it("a failed load keeps the greeting and offers to try again", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.get(`${BASE}/api/v1/home`, () => HttpResponse.json({ detail: "boom", code: "internal" }, { status: 500 })),
		);
		await openHome();
		expect(await screen.findByRole("button", { name: "Try again" })).toBeInTheDocument();
		expect(screen.getByTestId("greeting")).toBeInTheDocument();
	});

	it("never says corpus, tenant or the engine's own name", async () => {
		signedInAs(PRIYA, SHREE);
		await openHome();
		await screen.findByTestId("assistant-status");
		expect(document.querySelector("main")?.textContent ?? "").not.toMatch(/corpus|corpora|tenant|cob-rag-engine/i);
	});
});

afterEach(() => {
	homeState.over = {};
});
