import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { BASE, PRIYA, ROHAN, SHREE } from "../msw/data";
import { articlesState, releaseHeld, seedArticle } from "../msw/handlers/home";
import { resetWebsiteState, websiteHandlers } from "../msw/handlers/website";
import { server } from "../msw/server";
import { renderApp } from "../render";
import { MEMBER_SHREE, signedInAs } from "./assistant-utils";

async function openKnowledge(path = "/knowledge?tab=articles") {
	const app = renderApp(path);
	await screen.findByRole("heading", { level: 1, name: "Knowledge" });
	return app;
}

describe("Knowledge page", () => {
	beforeEach(() => {
		resetWebsiteState();
		server.use(...websiteHandlers);
	});

	it("two tabs, the website one first; the tab lives in the URL", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await openKnowledge("/knowledge");
		const tabs = await screen.findByRole("tablist", { name: "Knowledge sections" });
		expect(
			within(tabs)
				.getAllByRole("tab")
				.map((t) => t.textContent),
		).toEqual(["From your website", "Articles & files"]);
		expect(within(tabs).getByRole("tab", { name: "From your website", selected: true })).toBeInTheDocument();
		// the website tab is the real one (BIZ1): the site the assistant reads, with its pages
		expect(await screen.findByText(/shreedental\.in/)).toBeInTheDocument();
		await app.user.click(within(tabs).getByRole("tab", { name: "Articles & files" }));
		await waitFor(() => expect(app.router.state.location.search).toEqual({ tab: "articles" }));
		expect(await screen.findByRole("button", { name: "Write an article" })).toBeInTheDocument();
	});

	it("says when the website was last read, with a way to read it again", async () => {
		signedInAs(PRIYA, SHREE);
		await openKnowledge("/knowledge?tab=articles");
		const line = await screen.findByTestId("website-last-read");
		expect(line).toHaveTextContent(/Your website was last read/);
		expect(within(line).getByRole("link", { name: "Read everything again" })).toHaveAttribute(
			"href",
			"/knowledge?tab=website",
		);
	});

	it("the knowledge base switcher appears only with more than one", async () => {
		signedInAs(PRIYA, SHREE); // Patient help and Staff SOPs
		await openKnowledge();
		expect(await screen.findByLabelText("Knowledge base")).toBeInTheDocument();
	});

	it("one knowledge base: no switcher", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.get(`${BASE}/api/v1/corpora`, async () => {
				const { CORPORA } = await import("../msw/data");
				return HttpResponse.json({ items: [CORPORA[0]], count: 1, total: 1, offset: 0, limit: 500 });
			}),
		);
		await openKnowledge();
		await screen.findByRole("tablist", { name: "Knowledge sections" });
		expect(screen.queryByLabelText("Knowledge base")).toBeNull();
	});

	it("lists articles, questions and files with their kind, newest first", async () => {
		signedInAs(PRIYA, SHREE);
		seedArticle("article", "Root canal treatment", "## Cost\nFrom ₹4,500.");
		seedArticle("faq", "Do you open on Sunday?", "Yes, 10 am to 2 pm.");
		await openKnowledge();
		const rows = await screen.findAllByTestId("knowledge-row");
		expect(rows).toHaveLength(3);
		const texts = rows.map((r) => r.textContent ?? "");
		expect(texts.some((t) => t.includes("Root canal treatment") && t.includes("Article"))).toBe(true);
		expect(texts.some((t) => t.includes("Do you open on Sunday?") && t.includes("Question & answer"))).toBe(true);
		expect(texts.some((t) => t.includes("fees-brochure-2026.pdf") && t.includes("File"))).toBe(true);
		// files have no editor
		const file = rows.find((r) => r.textContent?.includes("fees-brochure"));
		expect(within(file as HTMLElement).queryByRole("button", { name: /^Edit/ })).toBeNull();
	});

	it("an empty knowledge base explains what to add", async () => {
		signedInAs(PRIYA, SHREE);
		articlesState.files = [];
		await openKnowledge();
		expect(await screen.findByRole("heading", { name: "Nothing here yet" })).toBeInTheDocument();
		expect(screen.getByText(/root canal fees|your fees, timings/i)).toBeInTheDocument();
	});

	it("write an article: formatting buttons, live preview, saved with its kind", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await openKnowledge();
		await app.user.click(await screen.findByRole("button", { name: "Write an article" }));
		const dialog = await screen.findByRole("dialog", { name: "Write an article" });
		await app.user.type(within(dialog).getByLabelText("Title"), "Root canal treatment");
		const text = within(dialog).getByLabelText(/^Text/);
		await app.user.type(text, "Cost");
		await app.user.click(within(dialog).getByRole("button", { name: "Heading" }));
		await waitFor(() => expect(text).toHaveValue("## Cost"));
		await app.user.type(text, "{Enter}From ₹4,500 per tooth.");
		const preview = within(dialog).getByTestId("article-preview");
		expect(await within(preview).findByRole("heading", { name: "Cost" })).toBeInTheDocument();
		expect(within(preview).getByText(/₹4,500 per tooth/)).toBeInTheDocument();
		await app.user.click(within(dialog).getByRole("button", { name: "Save and teach my assistant" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(articlesState.sent[0]).toMatchObject({
			method: "POST",
			body: { kind: "article", title: "Root canal treatment", body: "## Cost\nFrom ₹4,500 per tooth." },
		});
		expect(await screen.findByText("Saved. Your assistant is learning it.")).toBeInTheDocument();
		expect(await screen.findByText("Root canal treatment")).toBeInTheDocument();
	});

	it("a saved article shows as 'learning' until its document exists", async () => {
		signedInAs(PRIYA, SHREE);
		articlesState.held = true;
		const app = await openKnowledge();
		await app.user.click(await screen.findByRole("button", { name: "Write an article" }));
		const dialog = await screen.findByRole("dialog", { name: "Write an article" });
		await app.user.type(within(dialog).getByLabelText("Title"), "Cleaning");
		await app.user.type(within(dialog).getByLabelText(/^Text/), "Scaling and polishing, ₹800.");
		await app.user.click(within(dialog).getByRole("button", { name: "Save and teach my assistant" }));
		expect(await screen.findByTestId("knowledge-learning")).toHaveTextContent("Your assistant is learning Cleaning");
		releaseHeld();
		await waitFor(() => expect(screen.queryByTestId("knowledge-learning")).toBeNull(), { timeout: 8000 });
		expect(await screen.findByText("Cleaning")).toBeInTheDocument();
	}, 20_000);

	it("add a question and answer: the question is the title", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await openKnowledge();
		await app.user.click(await screen.findByRole("button", { name: "Add a question & answer" }));
		const dialog = await screen.findByRole("dialog", { name: "Add a question & answer" });
		expect(within(dialog).queryByRole("button", { name: "Heading" })).toBeNull();
		await app.user.type(within(dialog).getByLabelText("Question"), "Do you open on Sunday?");
		await app.user.type(within(dialog).getByLabelText(/^Answer/), "Yes, 10 am to 2 pm.");
		await app.user.click(within(dialog).getByRole("button", { name: "Save and teach my assistant" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(articlesState.sent[0]).toMatchObject({
			method: "POST",
			body: { kind: "faq", title: "Do you open on Sunday?", body: "Yes, 10 am to 2 pm." },
		});
	});

	it("the editor validates as you go, with plain messages", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await openKnowledge();
		await app.user.click(await screen.findByRole("button", { name: "Write an article" }));
		const dialog = await screen.findByRole("dialog", { name: "Write an article" });
		await app.user.click(within(dialog).getByRole("button", { name: "Save and teach my assistant" }));
		expect(await within(dialog).findByText("Add a title.")).toBeInTheDocument();
		expect(within(dialog).getByText("Add some text.")).toBeInTheDocument();
		expect(articlesState.sent).toHaveLength(0);
		await app.user.type(within(dialog).getByLabelText("Title"), "Fees");
		await waitFor(() => expect(within(dialog).queryByText("Add a title.")).toBeNull());
	});

	it("a full plan is explained in words and nothing is added", async () => {
		signedInAs(PRIYA, SHREE);
		articlesState.limitReached = true;
		const app = await openKnowledge();
		await app.user.click(await screen.findByRole("button", { name: "Add a question & answer" }));
		const dialog = await screen.findByRole("dialog", { name: "Add a question & answer" });
		await app.user.type(within(dialog).getByLabelText("Question"), "Do you take cards?");
		await app.user.type(within(dialog).getByLabelText(/^Answer/), "Yes, UPI and cards.");
		await app.user.click(within(dialog).getByRole("button", { name: "Save and teach my assistant" }));
		expect(await within(dialog).findByText(/Your plan allows 50 documents/)).toBeInTheDocument();
		expect(screen.getByRole("dialog")).toBeInTheDocument();
	});

	it("edit opens with the stored text and saves a new version under the same id", async () => {
		signedInAs(PRIYA, SHREE);
		const article = seedArticle("article", "Root canal treatment", "## Cost\nFrom ₹4,500.");
		const app = await openKnowledge();
		await app.user.click(await screen.findByRole("button", { name: "Edit Root canal treatment" }));
		const dialog = await screen.findByRole("dialog", { name: "Edit article" });
		const text = await within(dialog).findByLabelText(/^Text/);
		await waitFor(() => expect(text).toHaveValue("## Cost\nFrom ₹4,500."));
		expect(within(dialog).getByLabelText("Title")).toHaveValue("Root canal treatment");
		await app.user.clear(text);
		await app.user.type(text, "From ₹5,000.");
		await app.user.click(within(dialog).getByRole("button", { name: "Save and teach my assistant" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(articlesState.sent[0]).toMatchObject({ method: "PUT", body: { kind: "article", body: "From ₹5,000." } });
		expect(articlesState.sent[0]?.id).toBe(article.document_id);
		expect(await screen.findByText("Saved. Your assistant is learning the new version.")).toBeInTheDocument();
	});

	it("owners delete after confirming; members see the control locked", async () => {
		signedInAs(PRIYA, SHREE);
		const article = seedArticle("faq", "Do you open on Sunday?", "Yes.");
		const app = await openKnowledge();
		await app.user.click(await screen.findByRole("button", { name: "Delete Do you open on Sunday?" }));
		const confirm = await screen.findByRole("alertdialog", { name: "Delete Do you open on Sunday??" });
		// The person types the title: the button stays off until it matches exactly.
		const button = within(confirm).getByRole("button", { name: "Delete" });
		expect(button).toBeDisabled();
		await app.user.type(within(confirm).getByRole("textbox"), "Do you open on Sunda");
		expect(button).toBeDisabled();
		await app.user.type(within(confirm).getByRole("textbox"), "y?");
		expect(button).toBeEnabled();
		await app.user.click(button);
		await waitFor(() => expect(articlesState.deleted).toEqual([article.document_id]));
		await waitFor(() => expect(screen.queryByText("Do you open on Sunday?")).toBeNull());
	});

	it("a member sees delete locked with the reason", async () => {
		signedInAs(ROHAN, MEMBER_SHREE);
		seedArticle("faq", "Do you open on Sunday?", "Yes.");
		await openKnowledge();
		const del = await screen.findByRole("button", { name: "Delete Do you open on Sunday?" });
		expect(del).toHaveAttribute("aria-disabled", "true");
	});

	it("?new=faq opens the editor straight away and is then dropped from the URL", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await openKnowledge("/knowledge?tab=articles&new=faq");
		expect(await screen.findByRole("dialog", { name: "Add a question & answer" })).toBeInTheDocument();
		await waitFor(() => expect(app.router.state.location.search).toEqual({ tab: "articles" }));
	});

	it("no knowledge base yet: a plain next step, with the deployer's wording kept out of the way", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.get(`${BASE}/api/v1/corpora`, () =>
				HttpResponse.json({ items: [], count: 0, total: 0, offset: 0, limit: 500 }),
			),
		);
		await openKnowledge("/knowledge");
		expect(await screen.findByRole("heading", { name: "Create a knowledge base first" })).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Create a knowledge base" })).toHaveAttribute("href", "/kbs?new=1");
	});

	it("never says corpus, records or the engine's own name", async () => {
		signedInAs(PRIYA, SHREE);
		seedArticle("article", "Root canal treatment", "x");
		const app = await openKnowledge();
		await app.user.click(await screen.findByRole("button", { name: "Write an article" }));
		await screen.findByRole("dialog");
		// the "Powered by" footer is the one place the engine's own name stays
		const text = [document.querySelector("main"), document.querySelector('[role="dialog"]')]
			.map((el) => el?.textContent ?? "")
			.join(" ");
		expect(text).not.toMatch(/corpus|corpora|records import|cob-rag-engine/i);
	});

	it("a failed knowledge base list says so and can be retried", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.get(`${BASE}/api/v1/corpora`, () =>
				HttpResponse.json({ detail: "boom", code: "internal" }, { status: 500 }),
			),
		);
		await openKnowledge("/knowledge");
		expect(await screen.findByText("We couldn't load your knowledge.")).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
	});
});
