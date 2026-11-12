import { act, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { askSentence } from "@/features/landing/AskCard";
import { fieldId } from "@/features/landing/draft";
import { previewSrc } from "@/features/landing/PreviewPane";
import { BASE, OPERATOR } from "../msw/data";
import { landingState, seedPublished } from "../msw/handlers/landing";
import { server } from "../msw/server";
import { renderApp } from "../render";
import { asAdmin } from "./landing-utils";

async function open(path = "/admin/landing") {
	asAdmin();
	const app = renderApp(path);
	await screen.findByTestId("landing-status");
	return app;
}

const previewCalls = () => landingState.calls.filter((c) => c.path.endsWith("/preview-link"));

describe("preview", () => {
	it("shows the draft at phone and desktop size from one signed link", async () => {
		const app = await open();
		await app.user.click(screen.getByTestId("landing-preview"));
		const phone = (await screen.findByTestId("preview-phone")) as HTMLIFrameElement;
		const desktop = screen.getByTestId("preview-desktop") as HTMLIFrameElement;
		expect(phone.src).toContain("/_landing/preview/home?t=token1&lang=en");
		expect(phone.src).toContain("preview=1");
		expect(desktop.src).toBe(phone.src);
		expect(phone.style.width).toBe("390px");
		expect(desktop.style.width).toBe("1440px");
	});

	it("swaps page, language and light or dark", async () => {
		landingState.draft.languages = ["en", "hi"];
		const app = await open();
		await app.user.click(screen.getByTestId("landing-preview"));
		await screen.findByTestId("preview-phone");
		await app.user.click(screen.getByRole("radio", { name: "Pricing" }));
		await waitFor(() =>
			expect((screen.getByTestId("preview-phone") as HTMLIFrameElement).src).toContain("/preview/pricing"),
		);
		await app.user.click(await screen.findByRole("radio", { name: "हिंदी" }));
		await waitFor(() => expect((screen.getByTestId("preview-phone") as HTMLIFrameElement).src).toContain("lang=hi"));
		await app.user.click(screen.getByRole("radio", { name: "Dark" }));
		expect((screen.getByTestId("preview-phone") as HTMLIFrameElement).src).toContain("theme=dark");
		expect(previewSrc("http://x/p?t=1", "dark")).toBe("http://x/p?t=1&preview=1&theme=dark");
	});

	it("gets a fresh link after the draft changes and shortly before the old one runs out", async () => {
		const app = await open();
		landingState.previewTtlMs = 61_200; // runs out in a little over a minute: refresh in about a second
		await app.user.click(screen.getByTestId("landing-preview"));
		await screen.findByTestId("preview-phone");
		expect(previewCalls()).toHaveLength(1);
		await waitFor(() => expect(previewCalls().length).toBeGreaterThanOrEqual(2), { timeout: 4000 });
		const before = previewCalls().length;
		landingState.revision = 5;
		await act(async () => {
			await app.queryClient.invalidateQueries({ queryKey: ["admin", "landing", "state"] });
		});
		await waitFor(() => expect(previewCalls().length).toBeGreaterThan(before));
		await waitFor(() => expect((screen.getByTestId("preview-phone") as HTMLIFrameElement).src).toContain("t=token5"));
	});

	it("warns when the phone preview reports a headline over three lines", async () => {
		const app = await open();
		await app.user.click(screen.getByTestId("landing-preview"));
		const phone = (await screen.findByTestId("preview-phone")) as HTMLIFrameElement;
		const send = (lines: number, source: Window | null) =>
			act(() => {
				window.dispatchEvent(
					new MessageEvent("message", {
						data: { type: "landing-preview", headlineLines: lines },
						origin: BASE,
						source,
					}),
				);
			});
		await send(2, phone.contentWindow);
		expect(screen.queryByTestId("fit-check")).toBeNull();
		await send(5, window); // not the phone frame: ignored
		expect(screen.queryByTestId("fit-check")).toBeNull();
		await send(4, phone.contentWindow);
		expect(await screen.findByTestId("fit-check")).toHaveTextContent("more than three lines");
	});
});

describe("publish", () => {
	it("sends the revision and the note, and the version appears", async () => {
		const app = await open();
		await app.user.click(screen.getByTestId("landing-publish"));
		await app.user.type(await screen.findByLabelText("What changed (optional)"), "First version");
		await app.user.click(screen.getByTestId("landing-publish-confirm"));
		await waitFor(() => expect(landingState.versions).toHaveLength(1));
		const call = landingState.calls.find((c) => c.path.endsWith("/publish"));
		expect(call?.body).toEqual({ base_revision: 1, change_note: "First version" });
		expect(await screen.findByText(/Last published/)).toBeInTheDocument();
	});

	it("saves unsaved edits first, then publishes the new revision", async () => {
		const app = await open();
		await app.user.click(
			within(screen.getByTestId("section-faq")).getByRole("switch", { name: "Show Questions and answers" }),
		);
		await app.user.click(screen.getByTestId("landing-publish"));
		await app.user.click(await screen.findByTestId("landing-publish-confirm"));
		await waitFor(() => expect(landingState.versions).toHaveLength(1));
		const methods = landingState.calls.map((c) => `${c.method} ${c.path.split("/").at(-1)}`);
		expect(methods.indexOf("PUT draft")).toBeLessThan(methods.indexOf("POST publish"));
		expect(landingState.calls.find((c) => c.path.endsWith("/publish"))?.body).toMatchObject({ base_revision: 2 });
	});

	it("lists the missing texts as links that open the right field in the right language", async () => {
		landingState.draft.languages = ["en", "hi"];
		const app = await open();
		await app.user.click(screen.getByTestId("landing-publish"));
		await app.user.click(await screen.findByTestId("landing-publish-confirm"));
		const gaps = await screen.findByTestId("publish-gaps");
		expect(await screen.findByRole("alert")).toHaveTextContent("Some texts are missing");
		await app.user.click(within(gaps).getByRole("button", { name: "Top of the page · Headline" }));
		await waitFor(() =>
			expect(screen.getByRole("tab", { name: "Home page" })).toHaveAttribute("aria-selected", "true"),
		);
		await waitFor(() => expect(document.activeElement?.id).toBe(fieldId("home.hero.headline")));
		// the field shows the language that is missing
		expect(
			within(screen.getByTestId("field-home.hero.headline")).getByText("Missing. Needed before publishing."),
		).toBeInTheDocument();
	});
});

describe("switching on, the address and versions", () => {
	it("the switch waits for a published version and an address, with the reason", async () => {
		const app = await open();
		const toggle = screen.getByRole("switch", { name: "Show the website to visitors" });
		expect(toggle).toBeDisabled();
		expect(screen.getAllByText("Publish your website once before switching it on.").length).toBeGreaterThan(0);
		seedPublished();
		await app.queryClient.invalidateQueries({ queryKey: ["admin", "landing", "state"] });
		await waitFor(() =>
			expect(screen.getByText("Set your website's address before switching it on.")).toBeInTheDocument(),
		);
		expect(toggle).toBeDisabled();
		const card = within(screen.getByTestId("address-card"));
		await app.user.type(card.getByLabelText("Website address"), "https://www.sharmadental.in");
		await app.user.click(card.getByRole("button", { name: "Save address" }));
		await waitFor(() => expect(toggle).toBeEnabled());
		await app.user.click(toggle);
		await waitFor(() => expect(screen.getByTestId("landing-on-state")).toHaveTextContent("Website is on"));
	});

	it("checks the address is https://host and shows the DNS and Caddy help", async () => {
		const app = await open();
		const card = within(screen.getByTestId("address-card"));
		const field = card.getByLabelText("Website address");
		await app.user.type(field, "www.sharmadental.in/pricing");
		expect(card.getByText("Use an address like https://www.example.in")).toBeInTheDocument();
		expect(card.getByRole("button", { name: "Save address" })).toBeDisabled();
		await app.user.click(card.getByText("How to set it up"));
		expect(card.getByText(/keeps this dashboard at \/app/)).toBeInTheDocument();
		expect(card.getByText(/points the name \(like www\)/)).toBeInTheDocument();
		expect(card.getByText(/While the website is off/)).toBeInTheDocument();
		expect(card.getByText(/port \(8000 by default\)/)).toBeInTheDocument();
	});

	it("lists versions, newest first, and restores one after a confirmation", async () => {
		seedPublished("First version");
		seedPublished("Hindi added", "2026-10-09T09:00:00Z");
		const app = await open("/admin/landing?tab=versions");
		const list = await screen.findByTestId("versions-tab");
		const items = within(list).getAllByTestId(/^version-/);
		expect(items.map((i) => i.dataset.testid)).toEqual(["version-2", "version-1"]);
		expect(items[0]).toHaveTextContent("Live now");
		await app.user.click(within(list).getByRole("button", { name: "Go back to version 1" }));
		expect(await screen.findByText("Go back to version 1?")).toBeInTheDocument();
		expect(landingState.calls.filter((c) => c.path.includes("restore"))).toHaveLength(0);
		await app.user.click(screen.getByRole("button", { name: "Go back to this" }));
		await waitFor(() => expect(landingState.calls.some((c) => c.path.includes("/versions/1/restore"))).toBe(true));
		expect(landingState.revision).toBe(2);
	});
});

describe("ask it yourself", () => {
	const kb = http.get(`${BASE}/api/v1/corpora`, ({ request }) => {
		const tenant = new URL(request.url).searchParams.get("tenant_id");
		const items =
			tenant === OPERATOR.id
				? [
						{
							id: "11111111-1111-4111-8111-111111111111",
							name: "Sahayak help centre",
							tenant_id: OPERATOR.id,
							tenant_name: OPERATOR.name,
							description: null,
							document_count: 4,
							chunk_count: 60,
							created_at: "2026-09-01T10:00:00Z",
							qdrant_collection: "c1",
						},
					]
				: [];
		return HttpResponse.json({ items, count: items.length, total: items.length, limit: 100, offset: 0 });
	});

	it.each([
		["ready", "Connected. Visitors can ask your assistant on the website."],
		["not_connected", "Not connected. The button stays hidden until you connect a knowledge base."],
		["key_inactive", "The key behind the button is switched off."],
		["origin_missing", "The key does not allow your website's address."],
		["kb_missing", "The knowledge base was deleted."],
	] as const)("says what %s means", async (status, sentence) => {
		landingState.askStatus = status;
		await open();
		expect(await screen.findByTestId("ask-status")).toHaveTextContent(sentence);
		expect(askSentence(status)).toContain(sentence);
	});

	it("connects to an operator knowledge base and disconnects", async () => {
		server.use(kb);
		landingState.address = "https://www.sharmadental.in";
		const app = await open();
		const card = within(screen.getByTestId("ask-card"));
		await app.user.selectOptions(
			await card.findByLabelText("Knowledge base the assistant answers from"),
			"11111111-1111-4111-8111-111111111111",
		);
		await app.user.click(card.getByTestId("ask-connect"));
		await waitFor(() => expect(card.getByTestId("ask-status")).toHaveAttribute("data-status", "ready"));
		expect(landingState.calls.find((c) => c.method === "POST" && c.path.endsWith("/assistant"))?.body).toEqual({
			corpus_id: "11111111-1111-4111-8111-111111111111",
		});
		await app.user.click(card.getByTestId("ask-disconnect"));
		await waitFor(() => expect(card.getByTestId("ask-status")).toHaveAttribute("data-status", "not_connected"));
	});

	it("will not connect without a website address", async () => {
		server.use(kb);
		const app = await open();
		const card = within(screen.getByTestId("ask-card"));
		await app.user.selectOptions(
			await card.findByLabelText("Knowledge base the assistant answers from"),
			"11111111-1111-4111-8111-111111111111",
		);
		expect(card.getByTestId("ask-connect")).toBeDisabled();
		expect(card.getByText("Set the website's address first.")).toBeInTheDocument();
	});

	it("shows the engine's message when the plan does not allow another key", async () => {
		server.use(
			kb,
			http.post(`${BASE}/api/v1/admin/landing/assistant`, () =>
				HttpResponse.json(
					{ detail: "Your plan allows no more widget keys.", code: "widget_key_limit_reached" },
					{ status: 409 },
				),
			),
		);
		landingState.address = "https://www.sharmadental.in";
		const app = await open();
		const card = within(screen.getByTestId("ask-card"));
		await app.user.selectOptions(
			await card.findByLabelText("Knowledge base the assistant answers from"),
			"11111111-1111-4111-8111-111111111111",
		);
		await app.user.click(card.getByTestId("ask-connect"));
		expect(await card.findByRole("alert")).toBeInTheDocument();
	});
});
