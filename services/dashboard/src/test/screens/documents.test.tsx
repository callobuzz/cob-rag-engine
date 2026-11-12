import { act, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, ROHAN, rt, SHREE, tokenResponse } from "../msw/data";
import { DOC_FAQ, DOC_HOME, DOC_PRICE, documentsLog, PATIENT, V1 } from "../msw/handlers/documents";
import { ingestState, REVIEW_JOB } from "../msw/handlers/ingest";
import { server } from "../msw/server";
import { renderApp } from "../render";

afterEach(() => server.events.removeAllListeners());
const MEMBER_SHREE = { ...SHREE, role: "member" as const };

function signedInAs(user: typeof PRIYA, tenant: typeof SHREE) {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user, tenant, tenants: [tenant] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () => HttpResponse.json({ user, tenant, tenants: [tenant], session_id: "s" })),
		http.get(`${BASE}/api/v1/corpora`, () => {
			const items = CORPORA.filter((c) => c.tenant_id === SHREE.id);
			return HttpResponse.json({ items, count: items.length, total: items.length, offset: 0, limit: 500 });
		}),
	);
}

const docList = () => screen.findByRole("table", { name: "Documents in this knowledge base" });

describe("documents list", () => {
	it("shows columns from the list, sources, versions and live status", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp(`/documents?kb=${PATIENT}`);
		const table = await docList();
		expect(screen.getByRole("heading", { level: 1, name: "Documents" })).toBeInTheDocument();
		expect(await screen.findByText("Patient help · 3 documents")).toBeInTheDocument();
		const headers = within(table)
			.getAllByRole("columnheader")
			.map((h) => h.textContent);
		expect(headers).toEqual(["Document", "Source", "Versions", "Size", "Added", "Status"]);
		const price = within(table).getByRole("link", { name: "test-price-list-oct.pdf" }).closest("tr") as HTMLElement;
		expect(within(price).getByText("upload")).toBeInTheDocument();
		expect(within(price).getByText("Live")).toBeInTheDocument();
		const faq = within(table).getByRole("link", { name: "faq-fasting" }).closest("tr") as HTMLElement;
		expect(within(faq).getByText("record")).toBeInTheDocument();
		expect(within(faq).getByText("No active version")).toBeInTheDocument();
		expect(within(screen.getByRole("main")).getByRole("link", { name: "Add content" })).toHaveAttribute(
			"href",
			`/add?kb=${PATIENT}`,
		);
	});

	it("search is debounced (300 ms) and kept in the URL", async () => {
		signedInAs(PRIYA, SHREE);
		const app = renderApp(`/documents?kb=${PATIENT}`);
		await docList();
		const box = screen.getByRole("searchbox", { name: "Search documents" });
		await app.user.type(box, "home");
		const searched = () => documentsLog.filter((l) => l.path === "/api/v1/documents" && l.search.includes("search="));
		expect(searched()).toHaveLength(0);
		await waitFor(() => expect(app.router.state.location.search).toMatchObject({ q: "home" }));
		await waitFor(() => expect(searched()).toHaveLength(1));
		expect(searched()[0]?.search).toContain("search=home");
		const table = await docList();
		await waitFor(() => expect(within(table).queryByText("test-price-list-oct.pdf")).toBeNull());
		expect(within(table).getByRole("link", { name: "home-collection.pdf" })).toBeInTheDocument();
	});

	it("pages with ?page= using the list's count", async () => {
		signedInAs(PRIYA, SHREE);
		const offsets: string[] = [];
		server.use(
			http.get(`${BASE}/api/v1/documents`, ({ request }) => {
				const u = new URL(request.url);
				const offset = Number(u.searchParams.get("offset"));
				offsets.push(String(offset));
				const items = Array.from({ length: 25 }, (_, i) => ({
					id: `d1${String(offset + i).padStart(6, "0")}-0000-4000-8000-000000000000`,
					file_name: `report-${offset + i + 1}.pdf`,
					mime_type: "application/pdf",
					size_bytes: 1000,
					ingested_at: "2026-09-29T10:00:00Z",
					external_id: null,
					active_version_id: "7e500001-0000-4000-8000-000000000001",
					version_count: 1,
				}));
				return HttpResponse.json({ items, count: 60 });
			}),
		);
		const app = renderApp(`/documents?kb=${PATIENT}`);
		await screen.findByText("report-1.pdf");
		expect(screen.getByText("1–25 of 60")).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Next" }));
		await screen.findByText("report-26.pdf");
		expect(offsets).toContain("25");
		expect(app.router.state.location.search).toMatchObject({ page: 2 });
	});

	it("an empty knowledge base teaches the ways to add content", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(http.get(`${BASE}/api/v1/documents`, () => HttpResponse.json({ items: [], count: 0 })));
		renderApp(`/documents?kb=${PATIENT}`);
		expect(await screen.findByRole("heading", { name: "Give Patient help something to know" })).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Upload files" })).toHaveAttribute("href", `/add?kb=${PATIENT}`);
		expect(screen.getByRole("link", { name: /Records/ })).toHaveAttribute("href", `/add?kb=${PATIENT}&tab=records`);
	});
});

describe("needs-review strip", () => {
	it("shows the similar document and replaces the one picked", async () => {
		signedInAs(PRIYA, SHREE);
		const app = renderApp(`/documents?kb=${PATIENT}`);
		const card = await screen.findByTestId("pending-review");
		expect(within(card).getByRole("heading", { name: "Possible duplicate" })).toBeInTheDocument();
		expect(card).toHaveTextContent(
			"test-price-list-oct (1).pdf looks like test-price-list-oct.pdf, which is already in this knowledge base (94% similar). Keep both, replace test-price-list-oct.pdf with this file, or skip it?",
		);
		await app.user.selectOptions(
			within(card).getByRole("combobox", { name: "Which existing document would it replace?" }),
			DOC_HOME,
		);
		await app.user.click(within(card).getByRole("button", { name: "Replace home-collection.pdf" }));
		await waitFor(() =>
			expect(ingestState.confirms).toEqual([
				{ jobId: REVIEW_JOB, body: { action: "replace", target_document_id: DOC_HOME } },
			]),
		);
		expect(await screen.findByText("test-price-list-oct (1).pdf will replace home-collection.pdf")).toBeInTheDocument();
		await waitFor(() => expect(screen.queryByTestId("pending-review")).toBeNull());
	});

	it("Keep both (add as new) sends action new", async () => {
		signedInAs(PRIYA, SHREE);
		const app = renderApp(`/documents?kb=${PATIENT}`);
		const card = await screen.findByTestId("pending-review");
		await app.user.click(within(card).getByRole("button", { name: "Keep both (add as new)" }));
		await waitFor(() => expect(ingestState.confirms[0]?.body).toEqual({ action: "new", target_document_id: null }));
	});

	it("Skip (don't add) asks first, then sends action skip and the row goes away", async () => {
		signedInAs(PRIYA, SHREE);
		const app = renderApp(`/documents?kb=${PATIENT}`);
		const card = await screen.findByTestId("pending-review");
		await app.user.click(within(card).getByRole("button", { name: "Skip (don't add)" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Skip test-price-list-oct (1).pdf?" });
		expect(dialog).toHaveTextContent("The upload is removed and nothing is added.");
		expect(ingestState.confirms).toHaveLength(0); // nothing is sent until it is confirmed
		await app.user.click(within(dialog).getByRole("button", { name: "Skip" }));
		await waitFor(() => expect(ingestState.confirms[0]?.body).toEqual({ action: "skip", target_document_id: null }));
		expect(await screen.findByText("test-price-list-oct (1).pdf was skipped")).toBeInTheDocument();
		await waitFor(() => expect(screen.queryByTestId("pending-review")).toBeNull());
	});

	it("cancelling the Skip question sends nothing", async () => {
		signedInAs(PRIYA, SHREE);
		const app = renderApp(`/documents?kb=${PATIENT}`);
		const card = await screen.findByTestId("pending-review");
		await app.user.click(within(card).getByRole("button", { name: "Skip (don't add)" }));
		const dialog = await screen.findByRole("alertdialog");
		await app.user.click(within(dialog).getByRole("button", { name: "Cancel" }));
		await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
		expect(ingestState.confirms).toHaveLength(0);
		expect(screen.getByTestId("pending-review")).toBeInTheDocument();
	});

	it.each([
		[410, { detail: "Review period has expired" }, "This review expired; upload the file again."],
		[409, { detail: "Job is not pending review (current status: queued)" }, "Already handled"],
		[
			409,
			{ detail: "Document limit reached (500)", code: "document_limit_reached", limit: 500, used: 500, adding: 1 },
			"Your plan allows 500 documents (500 used; this adds 1).",
		],
	])("confirm %i shows its message", async (status, body, text) => {
		signedInAs(PRIYA, SHREE);
		server.use(http.post(`${BASE}/api/v1/ingest/:id/confirm`, () => HttpResponse.json(body, { status })));
		const app = renderApp(`/documents?kb=${PATIENT}`);
		const card = await screen.findByTestId("pending-review");
		await app.user.click(within(card).getByRole("button", { name: "Keep both (add as new)" }));
		expect(await within(card).findByText(text)).toBeInTheDocument();
	});
});

describe("document detail", () => {
	it("header, metadata, passages paged by 50 and expandable text", async () => {
		signedInAs(PRIYA, SHREE);
		const app = renderApp(`/documents/${DOC_PRICE}?kb=${PATIENT}`);
		expect(await screen.findByRole("heading", { level: 1, name: "test-price-list-oct.pdf" })).toBeInTheDocument();
		expect(screen.getByText("PDF")).toBeInTheDocument();
		expect(screen.getByText("Kothrud")).toBeInTheDocument();
		const list = screen.getByRole("list", { name: /Passages of version 2/ });
		expect(within(list).getAllByRole("listitem")).toHaveLength(50);
		expect(within(list).getAllByText("Test prices › Blood tests")).toHaveLength(50);
		expect(within(list).getByText("Table")).toBeInTheDocument();
		expect(within(list).getByRole("link", { name: "Part of passage 1" })).toHaveAttribute(
			"href",
			expect.stringMatching(/^#chunk-/),
		);
		const show = within(list).getAllByRole("button", { name: "Show all" })[0] as HTMLElement;
		expect(show).toHaveAttribute("aria-expanded", "false");
		await app.user.click(show);
		expect(within(list).getByRole("button", { name: "Show less" })).toHaveAttribute("aria-expanded", "true");
		await app.user.click(screen.getByRole("button", { name: "Next" }));
		await waitFor(() =>
			expect(within(screen.getByRole("list", { name: /Passages/ })).getAllByRole("listitem")).toHaveLength(10),
		);
		expect(documentsLog.some((l) => l.search.includes("chunk_offset=50"))).toBe(true);
		expect(app.router.state.location.search).toMatchObject({ chunkPage: 2 });
	});

	it("versions: view an older version's passages, then make it active", async () => {
		signedInAs(PRIYA, SHREE);
		const app = renderApp(`/documents/${DOC_PRICE}?kb=${PATIENT}&tab=versions`);
		const table = await screen.findByRole("table", { name: "Versions of this document" });
		const rows = within(table).getAllByRole("row");
		expect(rows[1]).toHaveTextContent("v2");
		expect(rows[1]).toHaveTextContent("Active");
		await app.user.click(within(table).getByRole("button", { name: "View passages: v1" }));
		expect(await screen.findByText("Showing passages of version 1")).toBeInTheDocument();
		expect(documentsLog.some((l) => l.search.includes(`version_id=${V1}`))).toBe(true);
		expect(screen.getByText("Old September price list: CBC ₹320.")).toBeInTheDocument();
		await app.user.click(screen.getByRole("tab", { name: /Versions/ }));
		await app.user.click(await screen.findByRole("button", { name: "Make active: v1" }));
		expect(await screen.findByText("Version 1 is now active")).toBeInTheDocument();
	});

	it.each([
		[
			409,
			{ detail: "Document limit reached (2)", code: "document_limit_reached", limit: 2, used: 2, adding: 1 },
			"Your plan allows 2 documents (2 used; this adds 1).",
		],
		[503, { detail: "Embedding provider unavailable" }, "Couldn't rebuild the index"],
	])("restore %i shows its message", async (status, body, text) => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.post(`${BASE}/api/v1/documents/:id/versions/:vid/restore`, () => HttpResponse.json(body, { status })),
		);
		const app = renderApp(`/documents/${DOC_PRICE}?kb=${PATIENT}&tab=versions`);
		await app.user.click(await screen.findByRole("button", { name: "Make active: v1" }));
		expect(await screen.findByText(text)).toBeInTheDocument();
	});

	it("owner deletes with a plain confirmation naming the file", async () => {
		signedInAs(PRIYA, SHREE);
		// an API-key caller must name the file (the engine's delete guardrail); the dashboard always does
		const deletes: string[] = [];
		server.events.on("request:start", ({ request }) => {
			if (request.method === "DELETE") deletes.push(new URL(request.url).search);
		});
		const app = renderApp(`/documents/${DOC_HOME}?kb=${PATIENT}`);
		await screen.findByRole("heading", { level: 1, name: "home-collection.pdf" });
		expect(screen.getByRole("link", { name: /shree-labs\.in\/home-collection/ })).toHaveAttribute(
			"rel",
			"noopener noreferrer",
		);
		await app.user.click(screen.getByRole("button", { name: "Document actions" }));
		await app.user.click(await screen.findByRole("menuitem", { name: /Delete document/ }));
		const dialog = await screen.findByRole("alertdialog", { name: "Delete home-collection.pdf?" });
		await app.user.click(within(dialog).getByRole("button", { name: "Delete document" }));
		expect(await screen.findByText("Deleted home-collection.pdf")).toBeInTheDocument();
		expect(deletes).toHaveLength(1);
		expect(new URLSearchParams(deletes[0]).get("confirm_name")).toBe("home-collection.pdf");
		expect(new URLSearchParams(deletes[0]).get("corpus_id")).toBe(PATIENT);
		await waitFor(() => expect(app.pathname()).toBe("/documents"));
		const table = await docList();
		await waitFor(() => expect(within(table).queryByText("home-collection.pdf")).toBeNull());
	});

	it("a document that is already gone is removed with a note", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.delete(`${BASE}/api/v1/documents/:id`, () =>
				HttpResponse.json({ detail: "Document not found" }, { status: 404 }),
			),
		);
		const app = renderApp(`/documents/${DOC_HOME}?kb=${PATIENT}`);
		await screen.findByRole("heading", { level: 1, name: "home-collection.pdf" });
		await app.user.click(screen.getByRole("button", { name: "Document actions" }));
		await app.user.click(await screen.findByRole("menuitem", { name: /Delete document/ }));
		await app.user.click(
			within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete document" }),
		);
		expect(await screen.findByText("home-collection.pdf was already deleted")).toBeInTheDocument();
		await waitFor(() => expect(app.pathname()).toBe("/documents"));
	});

	it("owner merges into another document and lands on it", async () => {
		signedInAs(PRIYA, SHREE);
		const paths: string[] = [];
		server.events.on("request:start", ({ request }) => {
			if (request.method === "POST") paths.push(new URL(request.url).pathname);
		});
		const app = renderApp(`/documents/${DOC_HOME}?kb=${PATIENT}`);
		await screen.findByRole("heading", { level: 1, name: "home-collection.pdf" });
		await app.user.click(screen.getByRole("button", { name: "Document actions" }));
		await app.user.click(await screen.findByRole("menuitem", { name: /Merge into another document/ }));
		const dialog = await screen.findByRole("dialog", { name: "Merge home-collection.pdf into another document" });
		await app.user.click(within(dialog).getByRole("button", { name: "Merge" }));
		expect(within(dialog).getByText("Pick the document to keep.")).toBeInTheDocument();
		await app.user.click(await within(dialog).findByRole("radio", { name: /test-price-list-oct\.pdf/ }));
		expect(within(dialog).queryByRole("radio", { name: /home-collection\.pdf/ })).toBeNull();
		await app.user.click(within(dialog).getByRole("button", { name: "Merge" }));
		await waitFor(() => expect(paths).toContain(`/api/v1/documents/${DOC_HOME}/replace/${DOC_PRICE}`));
		expect(await screen.findByText("Merged into test-price-list-oct.pdf")).toBeInTheDocument();
		await waitFor(() => expect(app.pathname()).toBe(`/documents/${DOC_PRICE}`));
	});

	it("a member sees merge and delete locked with the owner text", async () => {
		signedInAs(ROHAN, MEMBER_SHREE);
		const app = renderApp(`/documents/${DOC_PRICE}?kb=${PATIENT}`);
		await screen.findByRole("heading", { level: 1, name: "test-price-list-oct.pdf" });
		await app.user.click(screen.getByRole("button", { name: "Document actions" }));
		const del = await screen.findByRole("menuitem", { name: /Delete document/ });
		expect(del).toHaveAttribute("aria-disabled", "true");
		expect(within(del).getByText("Only owners can delete documents")).toBeInTheDocument();
		expect(screen.getByRole("menuitem", { name: /Merge/ })).toHaveAttribute("aria-disabled", "true");
		await app.user.click(del);
		expect((await screen.findAllByText("Only owners can delete documents")).length).toBeGreaterThan(0);
		expect(screen.queryByRole("alertdialog")).toBeNull();
	});

	it("owner_required from the server is shown even if the UI allowed the click", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.delete(`${BASE}/api/v1/documents/:id`, () =>
				HttpResponse.json({ detail: "Only a workspace owner can do this", code: "owner_required" }, { status: 403 }),
			),
		);
		const app = renderApp(`/documents/${DOC_FAQ}?kb=${PATIENT}`);
		await screen.findByRole("heading", { level: 1, name: "faq-fasting" });
		expect(screen.getByText("This version has no passages.")).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Document actions" }));
		await app.user.click(await screen.findByRole("menuitem", { name: /Delete document/ }));
		await app.user.click(
			within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete document" }),
		);
		expect(await screen.findByText("Only workspace owners can do this. Ask an owner.")).toBeInTheDocument();
	});

	it("an unknown document says it may have been deleted", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp(`/documents/d0c09999-0000-4000-8000-000000000009?kb=${PATIENT}`);
		expect(await screen.findByRole("heading", { level: 1, name: "Document not found" })).toBeInTheDocument();
		expect(screen.getByText("It may have been deleted, or the link is wrong.")).toBeInTheDocument();
		await act(async () => {});
	});
});
