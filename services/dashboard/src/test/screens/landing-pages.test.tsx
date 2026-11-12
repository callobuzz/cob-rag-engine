import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { describePath, missingIn } from "@/features/landing/draft";
import { landingState, stateOut } from "../msw/handlers/landing";
import { renderApp } from "../render";
import { asAdmin } from "./landing-utils";

async function open(tab: string, testId: string) {
	asAdmin();
	const app = renderApp(`/admin/landing?tab=${tab}`);
	await screen.findByTestId(testId);
	return app;
}

const save = async (app: Awaited<ReturnType<typeof open>>) => {
	await app.user.click(screen.getByTestId("landing-save"));
	await waitFor(() => expect(landingState.calls.some((c) => c.method === "PUT")).toBe(true));
	return landingState.calls.filter((c) => c.method === "PUT").at(-1)?.body as {
		base_revision: number;
		content: ReturnType<typeof stateOut>["draft"];
	};
};

describe("pricing page", () => {
	it("builds a card from the plans API and saves the plan ids per currency and period", async () => {
		const app = await open("pricing", "pricing-tab");
		await app.user.click(screen.getByTestId("add-price-card"));
		const card = await screen.findByTestId("price-card-0");
		expect(within(card).getByText("Choose at least one plan for this card.")).toBeInTheDocument();
		await app.user.type(within(card).getByLabelText(/Who it is for/), "Clinics with one doctor");
		const monthly = within(card).getByLabelText("Rupees, Monthly");
		await waitFor(() => expect(within(monthly).getByRole("option", { name: "Starter" })).toBeInTheDocument());
		const starter = within(monthly).getByRole<HTMLOptionElement>("option", { name: "Starter" }).value;
		await app.user.selectOptions(monthly, starter);
		expect(within(card).queryByText("Choose at least one plan for this card.")).toBeNull();
		const body = await save(app);
		const saved = body.content.pricing.cards?.[0];
		expect(saved?.variants).toEqual({ INR: { monthly: starter } });
		expect(saved?.who_for).toEqual({ en: "Clinics with one doctor" });
	});

	it("shows the engine's warning about plans with a placeholder price", async () => {
		asAdmin();
		const original = stateOut;
		expect(original).toBeTypeOf("function");
		landingState.placeholderNames = ["Starter"];
		renderApp("/admin/landing?tab=pricing");
		expect(await screen.findByTestId("placeholder-price")).toHaveTextContent("Starter");
	});

	it("sends the cost of one answer as whole paise, never a fraction", async () => {
		const app = await open("pricing", "pricing-tab");
		const field = screen.getByLabelText(/Estimated price of one answer \(₹\)/);
		fireEvent.change(field, { target: { value: "2.5" } });
		expect(screen.getByText("₹2.50")).toBeInTheDocument();
		const body = await save(app);
		expect(body.content.pricing.calculator?.est_answer_minor).toEqual({ INR: 250 });
		expect(Number.isInteger(body.content.pricing.calculator?.est_answer_minor?.INR)).toBe(true);
	});

	it("refuses an amount that is not a price", async () => {
		await open("pricing", "pricing-tab");
		const field = screen.getByLabelText(/Estimated price of one answer \(₹\)/);
		fireEvent.change(field, { target: { value: "abc" } });
		expect(screen.getByText("Type an amount like 2 or 2.50.")).toBeInTheDocument();
		fireEvent.change(field, { target: { value: "0" } });
		expect(screen.getByText("Type an amount like 2 or 2.50.")).toBeInTheDocument();
	});

	it("edits the credit box and the examples", async () => {
		const app = await open("pricing", "pricing-tab");
		const box = screen.getByLabelText("What is a credit?");
		fireEvent.change(box, { target: { value: "A credit pays for one answer." } });
		await app.user.click(screen.getByRole("button", { name: "Add example" }));
		const body = await save(app);
		expect(body.content.pricing.credit_box).toEqual({ en: "A credit pays for one answer." });
	});
});

describe("other pages", () => {
	it("edits Features, About and Contact", async () => {
		const app = await open("pages", "pages-tab");
		fireEvent.change(within(screen.getByTestId("field-features.headline")).getByRole("textbox"), {
			target: { value: "Everything your front desk does at night" },
		});
		fireEvent.change(within(screen.getByTestId("field-about.headline")).getByRole("textbox"), {
			target: { value: "A small team in Pune" },
		});
		fireEvent.change(within(screen.getByTestId("field-contact.topics.sales")).getByRole("textbox"), {
			target: { value: "New enquiry" },
		});
		await app.user.click(screen.getByLabelText("Show your address"));
		const body = await save(app);
		expect(body.content.features.headline).toEqual({ en: "Everything your front desk does at night" });
		expect(body.content.about.headline).toEqual({ en: "A small team in Pune" });
		expect(body.content.contact.topics).toMatchObject({ sales: { en: "New enquiry" } });
		expect(body.content.contact.show_address).toBe(false);
	});
});

describe("menu, footer and search texts", () => {
	it("adds a menu link to a page and a link to an address", async () => {
		const app = await open("menu", "menu-tab");
		const links = screen.getByTestId("links-nav.links");
		const before = within(links).queryAllByRole("textbox").length;
		await app.user.click(within(links).getByRole("button", { name: "Add link" }));
		const fields = within(links).getAllByRole("textbox");
		expect(fields.length).toBeGreaterThan(before);
		await app.user.type(within(links).getAllByRole("textbox").at(-1) as HTMLElement, "Our clinic");
		const target = within(links).getAllByLabelText("Goes to").at(-1) as HTMLElement;
		await app.user.selectOptions(target, "An address");
		const address = within(links).getByLabelText("Address");
		await app.user.clear(address);
		await app.user.type(address, "https://www.sharmadental.in");
		const body = await save(app);
		const last = body.content.nav?.links?.at(-1);
		expect(last).toMatchObject({ label: { en: "Our clinic" }, page: null, href: "https://www.sharmadental.in" });
	});

	it("edits the title of a page for search engines within its limit", async () => {
		const app = await open("menu", "menu-tab");
		const field = screen.getByTestId("field-seo.home.title");
		fireEvent.change(within(field).getByRole("textbox"), { target: { value: "x".repeat(90) } });
		expect(within(field).getByRole("textbox")).toHaveValue("x".repeat(60));
		const body = await save(app);
		expect(body.content.seo.home.title.en).toHaveLength(60);
	});
});

describe("languages", () => {
	it("switches Hindi on, lists the missing texts per language and links each to its field", async () => {
		const app = await open("languages", "languages-tab");
		expect(screen.queryByTestId("missing-hi")).toBeNull();
		await app.user.click(screen.getByRole("switch", { name: "Offer the website in हिंदी" }));
		const section = await screen.findByTestId("missing-hi");
		expect(within(section).getByRole("heading")).toHaveTextContent(/texts still missing in हिंदी/);
		const rows = within(section).getAllByRole("button", { name: "Write it" });
		expect(rows.length).toBeGreaterThan(5);
		// "Write it" opens the right tab, on the right language, in the right field
		await app.user.click(rows[0] as HTMLElement);
		await waitFor(() =>
			expect(screen.getByRole("tab", { name: "Home page" })).toHaveAttribute("aria-selected", "true"),
		);
		const first = screen.getByTestId("home-sections");
		expect(first).toBeInTheDocument();
	});

	it("English cannot be switched off and Hinglish is a separate choice", async () => {
		await open("languages", "languages-tab");
		expect(screen.getByRole("switch", { name: "Offer the website in English" })).toBeDisabled();
		expect(screen.getByRole("switch", { name: "Offer the website in Hinglish" })).not.toBeChecked();
	});

	it("saving with two languages sends the language list", async () => {
		const app = await open("languages", "languages-tab");
		await app.user.click(screen.getByRole("switch", { name: "Offer the website in Hinglish" }));
		const body = await save(app);
		expect(body.content.languages).toEqual(["en", "hi-Latn"]);
	});

	it("names a missing text in words", () => {
		const content = stateOut().draft;
		content.languages = ["en", "hi"];
		const gaps = missingIn(content, "hi");
		expect(gaps).toContain("home.hero.headline");
		expect(describePath("home.hero.headline", content)).toBe("Top of the page · Headline");
		expect(describePath("pricing.credit_box", content)).toBe("Pricing page · Credit box");
		expect(describePath("home.pain-wall.questions[0].text", content)).toBe(
			"Questions your customers ask late at night · Questions 1 › text",
		);
	});
});
