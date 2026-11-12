import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { applyGenerated, mergeTexts, moveSection, moveSectionTo } from "@/features/landing/draft";
import { BASE, OPERATOR } from "../msw/data";
import { landingState, SAMPLE_CONVERSATION, stateOut } from "../msw/handlers/landing";
import { server } from "../msw/server";
import { renderApp } from "../render";
import { asAdmin } from "./landing-utils";

async function open() {
	asAdmin();
	const app = renderApp("/admin/landing");
	await screen.findByTestId("home-sections");
	return app;
}

const card = (id: string) => screen.getByTestId(`section-${id}`);
const sectionIds = () =>
	within(screen.getByTestId("home-sections"))
		.getAllByTestId(/^section-/)
		.map((el) => el.dataset.testid);

describe("home sections: on, off and order", () => {
	it("switching a section off updates the draft and greys its card", async () => {
		const app = await open();
		expect(card("faq")).toHaveAttribute("data-enabled", "true");
		await app.user.click(within(card("faq")).getByRole("switch", { name: "Show Questions and answers" }));
		expect(card("faq")).toHaveAttribute("data-enabled", "false");
		expect(screen.getByText("You have unsaved changes.")).toBeInTheDocument();
	});

	it("the first and last sections have no switch and cannot move past the ends", async () => {
		await open();
		for (const id of ["hero", "final-cta"]) {
			expect(within(card(id)).queryByRole("switch")).toBeNull();
			expect(within(card(id)).getByText("Always shown")).toBeInTheDocument();
			for (const b of within(card(id)).getAllByRole("button", { name: /^Move / })) expect(b).toBeDisabled();
		}
		expect(within(card("trust-strip")).getByRole("button", { name: /Move .* up/ })).toBeDisabled();
		expect(within(card("faq")).getByRole("button", { name: /Move .* down/ })).toBeDisabled();
	});

	it("Up and Down reorder and announce the new place", async () => {
		const app = await open();
		const before = sectionIds();
		await app.user.click(within(card("how-it-works")).getByRole("button", { name: "Move How it works down" }));
		const after = sectionIds();
		expect(after.indexOf("section-how-it-works")).toBe(before.indexOf("section-how-it-works") + 1);
		expect(screen.getByTestId("sections-live")).toHaveTextContent("How it works is now number 5 of 16.");
	});

	it("the pure moves keep the first and last in place", () => {
		const home = stateOut().draft.home;
		expect(moveSection(home, "hero", 1)).toBe(home);
		expect(moveSection(home, "final-cta", -1)).toBe(home);
		expect(moveSectionTo(home, "faq", 0)).toBe(home);
		expect(moveSectionTo(home, "faq", home.length - 1)).toBe(home);
		expect(moveSectionTo(home, "faq", 1).map((s) => s.id)[1]).toBe("faq");
	});
});

describe("texts: counters, limits, languages and warnings", () => {
	it("shows used of max from the engine's limits and stops typing at the limit", async () => {
		const app = await open();
		await app.user.click(within(card("hero")).getByRole("button", { name: "Edit Top of the page" }));
		const headline = await screen.findByLabelText(/^Headline/);
		expect(screen.getByTestId("counter-home.hero.headline")).toHaveTextContent(/of 60$/);
		fireEvent.change(headline, { target: { value: "x".repeat(90) } });
		expect((headline as HTMLInputElement).value).toHaveLength(60);
		expect(screen.getByTestId("counter-home.hero.headline")).toHaveTextContent("60 of 60");
	});

	it("says Missing where a switched-on language has no text", async () => {
		landingState.draft.languages = ["en", "hi"];
		const app = await open();
		await app.user.click(within(card("hero")).getByRole("button", { name: "Edit Top of the page" }));
		const field = await screen.findByTestId("field-home.hero.headline");
		await app.user.click(within(field).getByRole("button", { name: /हिंदी/ }));
		expect(within(field).getByText("Missing. Needed before publishing.")).toBeInTheDocument();
		await app.user.type(within(field).getByRole("textbox"), "नमस्ते");
		expect(within(field).queryByText("Missing. Needed before publishing.")).toBeNull();
	});

	it("shows the engine's copy warnings under the field they name", async () => {
		landingState.copyWarnings = [
			{ path: "home.hero.headline", word: "guaranteed", sentence: "Say what it does, not a promise." },
		];
		const app = await open();
		await app.user.click(within(card("hero")).getByRole("button", { name: "Edit Top of the page" }));
		const field = await screen.findByTestId("field-home.hero.headline");
		expect(within(field).getByRole("note")).toHaveTextContent("guaranteed");
	});

	it("keeps the conversation alternating: lines are added at the end with the next speaker", async () => {
		const app = await open();
		await app.user.click(within(card("hero")).getByRole("button", { name: "Edit Top of the page" }));
		const editor = await screen.findByTestId("conversation-editor");
		expect(within(editor).getAllByTestId(/^turn-/).length).toBeGreaterThanOrEqual(2);
		const before = within(editor).getAllByTestId(/^turn-/).length;
		await app.user.click(within(editor).getByRole("button", { name: /^Add a (visitor|assistant) line/ }));
		expect(within(editor).getAllByTestId(/^turn-/)).toHaveLength(before + 1);
	});
});

describe("saving", () => {
	it("Save draft sends the whole content with the loaded revision", async () => {
		const app = await open();
		await app.user.click(within(card("faq")).getByRole("switch", { name: "Show Questions and answers" }));
		await app.user.click(screen.getByTestId("landing-save"));
		await waitFor(() => expect(landingState.revision).toBe(2));
		const call = landingState.calls.find((c) => c.method === "PUT");
		const body = call?.body as { base_revision: number; content: { home: { id: string; enabled: boolean }[] } };
		expect(body.base_revision).toBe(1);
		expect(body.content.home.find((s) => s.id === "faq")?.enabled).toBe(false);
		expect(body.content.home).toHaveLength(16);
	});

	it("a save from another tab shows the reload banner and keeps the person's edits", async () => {
		const app = await open();
		await app.user.click(within(card("hero")).getByRole("button", { name: "Edit Top of the page" }));
		const headline = (await screen.findByLabelText(/^Headline/)) as HTMLInputElement;
		fireEvent.change(headline, { target: { value: "Answers at midnight" } });
		landingState.revision = 7; // saved elsewhere meanwhile
		await app.user.click(screen.getByTestId("landing-save"));
		expect(await screen.findByTestId("landing-conflict")).toHaveTextContent(
			"Someone saved this website in another tab.",
		);
		expect((screen.getByLabelText(/^Headline/) as HTMLInputElement).value).toBe("Answers at midnight");
	});
});

describe("starting examples", () => {
	it("merges only the ticked parts into the draft and saves nothing", async () => {
		const app = await open();
		await app.user.click(screen.getByTestId("preset-open"));
		await app.user.click(await screen.findByTestId("preset-use-clinic"));
		const parts = await screen.findByTestId("preset-parts");
		await app.user.click(within(parts).getByLabelText("Questions and answers"));
		await app.user.click(within(parts).getByLabelText("The sample conversation"));
		await app.user.click(screen.getByTestId("preset-apply"));
		// the late-night questions were replaced, the conversation and the questions were not
		await app.user.click(within(card("pain-wall")).getByRole("button", { name: /Edit / }));
		expect(await screen.findByDisplayValue("Is the clinic open on Sunday?")).toBeInTheDocument();
		await app.user.click(within(card("hero")).getByRole("button", { name: "Edit Top of the page" }));
		expect(screen.queryByDisplayValue(SAMPLE_CONVERSATION.turns[0]?.text.en ?? "")).toBeNull();
		await app.user.click(within(card("faq")).getByRole("button", { name: /Edit / }));
		expect(screen.queryByDisplayValue("Does it replace my staff?")).toBeNull();
		expect(landingState.calls.filter((c) => c.method === "PUT")).toHaveLength(0);
		expect(screen.getByText("You have unsaved changes.")).toBeInTheDocument();
	});
});

describe("write it from a knowledge base", () => {
	const operatorKb = http.get(`${BASE}/api/v1/corpora`, ({ request }) => {
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

	it("lists only the operator's knowledge bases, says what it costs, and previews before using", async () => {
		server.use(operatorKb);
		const app = await open();
		await app.user.click(screen.getByTestId("generate-open"));
		expect(await screen.findByTestId("generate-cost")).toHaveTextContent(
			"This uses one answer from your own AI model.",
		);
		const kb = await screen.findByLabelText("Knowledge base");
		await waitFor(() => expect(within(kb).getByRole("option", { name: "Sahayak help centre" })).toBeInTheDocument());
		expect(within(kb).queryByRole("option", { name: /Shree/ })).toBeNull();
		await app.user.selectOptions(kb, "11111111-1111-4111-8111-111111111111");
		await app.user.click(screen.getByTestId("generate-run"));
		const result = await screen.findByTestId("generate-result");
		expect(result).toHaveTextContent("Is Dr. Shree available on Saturday evening?");
		expect(result).toHaveTextContent("Timings › Saturday");
		// nothing changed yet
		expect(screen.queryByText("You have unsaved changes.")).toBeNull();
		await app.user.click(screen.getByTestId("generate-use"));
		expect(await screen.findByText("You have unsaved changes.")).toBeInTheDocument();
		expect(landingState.calls.filter((c) => c.method === "PUT")).toHaveLength(0);
	});

	it("Discard changes nothing", async () => {
		server.use(operatorKb);
		const app = await open();
		await app.user.click(screen.getByTestId("generate-open"));
		await app.user.selectOptions(
			await screen.findByLabelText("Knowledge base"),
			"11111111-1111-4111-8111-111111111111",
		);
		await app.user.click(screen.getByTestId("generate-run"));
		await screen.findByTestId("generate-result");
		await app.user.click(screen.getByTestId("generate-discard"));
		expect(screen.queryByText("You have unsaved changes.")).toBeNull();
	});

	it.each([
		[503, "landing_generate_unavailable", "No AI model is set up for this knowledge base"],
		[409, "landing_generate_no_content", "This knowledge base has nothing to read yet"],
		[502, "landing_generate_failed", "The AI model did not answer"],
	])("shows the message for %s", async (status, code, text) => {
		server.use(operatorKb);
		landingState.generateFails = { status, code };
		const app = await open();
		await app.user.click(screen.getByTestId("generate-open"));
		await app.user.selectOptions(
			await screen.findByLabelText("Knowledge base"),
			"11111111-1111-4111-8111-111111111111",
		);
		await app.user.click(screen.getByTestId("generate-run"));
		expect(await screen.findByRole("alert")).toHaveTextContent(text);
	});

	it("disables the button while it runs and when no knowledge base is chosen", async () => {
		server.use(operatorKb);
		const app = await open();
		await app.user.click(screen.getByTestId("generate-open"));
		await screen.findByLabelText("Knowledge base");
		expect(screen.getByTestId("generate-run")).toBeDisabled();
	});
});

describe("language helpers", () => {
	it("puts the texts of two languages side by side", () => {
		const merged = mergeTexts({ a: { en: "Hi" }, n: [{ en: "x" }] }, { a: { hi: "नमस्ते" }, n: [{ hi: "य" }] });
		expect(merged).toEqual({ a: { en: "Hi", hi: "नमस्ते" }, n: [{ en: "x", hi: "य" }] });
	});

	it("a written conversation replaces the sample in the main language and merges in another", () => {
		type Chat = { turns: { text: Record<string, string> }[]; generated: boolean };
		const existing: Chat = { turns: [{ text: { en: "a" } }, { text: { en: "b" } }], generated: false };
		const written: Chat = { turns: [{ text: { hi: "अ" } }, { text: { hi: "ब" } }], generated: false };
		expect(applyGenerated(existing, written, "en", "en")?.generated).toBe(true);
		expect(applyGenerated(existing, written, "hi", "en")?.turns[0]).toEqual({ text: { en: "a", hi: "अ" } });
		expect(applyGenerated(existing, { turns: written.turns.slice(0, 1), generated: false }, "hi", "en")).toBeNull();
	});
});
