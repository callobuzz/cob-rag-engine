import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { parentCopyIds } from "@/features/portal/ChunkEditor";
import { portalMessage } from "@/features/portal/messages";
import { moveItem } from "@/features/portal/ReorderList";
import { ruleSchema, toRuleBody } from "@/features/portal/RuleDialog";
import { conditionText } from "@/features/portal/RulesPage";
import { suggestSplit, validSplit } from "@/features/portal/SplitDialog";
import { ApiError } from "@/lib/api/errors";
import { KEY_KEY, RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, ROHAN, rt, SHREE, tokenResponse } from "../msw/data";
import { DOC_PRICE } from "../msw/handlers/documents";
import { TENANT_KEY } from "../msw/handlers/identity";
import {
	CH,
	DOC_ID,
	DOC_VERSION,
	PATIENT,
	portalHandlers,
	portalState,
	resetPortalState,
	STAGED_DOC,
	WS_DRAFT,
	WS_REVIEW,
} from "../msw/handlers/portal";
import { server } from "../msw/server";
import { renderApp } from "../render";

// jsdom's XHR can't carry a FormData File through MSW (as in add-content.test.tsx): fetch instead.
vi.mock("@/lib/api/upload", async () => {
	const { errorFromBody, toApiError } = await import("@/lib/api/errors");
	const { currentRagUrl } = await import("@/lib/auth/store");
	return {
		upload: async (opts: { path: string; buildForm: () => FormData; onProgress?: (p: number) => void }) => {
			const params = new URLSearchParams();
			for (const [k, v] of opts.buildForm().entries()) params.append(k, typeof v === "string" ? v : `file:${v.name}`);
			opts.onProgress?.(0.5);
			let res: Response;
			try {
				res = await fetch(`${currentRagUrl()}${opts.path}`, { method: "POST", body: params });
			} catch (e) {
				throw toApiError(e);
			}
			opts.onProgress?.(1);
			const text = await res.text();
			const body: unknown = text ? JSON.parse(text) : null;
			if (res.ok) return body;
			throw errorFromBody(res.status, body, res.headers);
		},
	};
});

const MEMBER_SHREE = { ...SHREE, role: "member" as const };

function signedInAs(user: typeof PRIYA, tenant: typeof SHREE = SHREE) {
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

const calls = (method: string, pathEnd: string) =>
	portalState.requests.filter((r) => r.method === method && r.path.endsWith(pathEnd));

beforeEach(() => {
	resetPortalState();
	server.use(...portalHandlers);
});

async function openWorkspace(id = WS_DRAFT, query = "", user: typeof PRIYA = PRIYA, tenant = SHREE) {
	signedInAs(user, tenant);
	const app = renderApp(`/portal/${id}?kb=${PATIENT}${query}`);
	await screen.findByTestId("lock-bar");
	return app;
}

async function startEditing(app: ReturnType<typeof renderApp>) {
	await app.user.click(screen.getByRole("button", { name: "Start editing" }));
	await waitFor(() => expect(screen.getByTestId("lock-bar")).toHaveAttribute("data-state", "mine"));
}

const card = (n: number) => screen.getByRole("article", { name: `Chunk ${n}` });

describe("feature gate", () => {
	it("says the portal is switched off when /info says so", async () => {
		signedInAs(PRIYA);
		server.use(
			http.get(`${BASE}/api/v1/info`, async () => {
				const { INFO } = await import("../msw/handlers/identity");
				return HttpResponse.json(INFO);
			}),
		);
		renderApp(`/portal?kb=${PATIENT}`);
		expect(await screen.findByTestId("portal-off")).toHaveTextContent(
			"The Content Portal is switched off on this engine (RAG_ENABLE_CONTENT_PORTAL)",
		);
		expect(calls("GET", "/portal/workspaces")).toHaveLength(0);
	});
});

describe("workspaces", () => {
	it("lists the knowledge base's workspaces and creates one with corpus_id and no user", async () => {
		signedInAs(PRIYA);
		const app = renderApp(`/portal?kb=${PATIENT}`);
		const table = await screen.findByRole("table", { name: "Workspaces" });
		expect(within(table).getByRole("link", { name: "Open Home collection policy" })).toBeInTheDocument();
		expect(within(table).getByText("Post-ingest review")).toBeInTheDocument();
		expect(within(table).getAllByText("In review").length).toBeGreaterThan(0);
		expect(calls("GET", "/portal/workspaces")[0]?.query).toContain(`corpus_id=${PATIENT}`);

		await app.user.click(screen.getByRole("button", { name: "New workspace" }));
		const dlg = await screen.findByRole("dialog", { name: "New workspace" });
		await app.user.click(within(dlg).getByRole("button", { name: "Create workspace" }));
		expect(await within(dlg).findByText("Give the workspace a title.")).toBeInTheDocument();
		await app.user.type(within(dlg).getByLabelText("Title"), "Report collection timings");
		await app.user.selectOptions(within(dlg).getByLabelText("Workflow"), "maker_checker");
		await app.user.click(within(dlg).getByRole("button", { name: "Create workspace" }));
		expect(await screen.findByRole("heading", { level: 1, name: "Report collection timings" })).toBeInTheDocument();
		const create = calls("POST", "/portal/workspaces")[0];
		expect(create?.query).toBe(`?corpus_id=${PATIENT}`);
		expect(create?.body).toEqual({
			title: "Report collection timings",
			workflow_mode: "maker_checker",
			assigned_reviewer: null,
		});
		expect(create?.body).not.toHaveProperty("user");
	});

	it("a member cannot delete a workspace", async () => {
		const app = await openWorkspace(WS_DRAFT, "", ROHAN, MEMBER_SHREE);
		const del = screen.getByRole("button", { name: "Delete workspace" });
		expect(del).toHaveAttribute("aria-disabled", "true");
		await app.user.click(del);
		expect(await screen.findByText("Only owners can delete portal workspaces")).toBeInTheDocument();
		expect(calls("DELETE", `/workspaces/${WS_DRAFT}`)).toHaveLength(0);
	});

	it("an owner types the workspace title to delete it", async () => {
		const owner = await openWorkspace();
		await owner.user.click(screen.getByRole("button", { name: "Delete workspace" }));
		const dlg = await screen.findByRole("alertdialog", { name: "Delete “Home collection policy”?" });
		const confirm = within(dlg).getByRole("button", { name: "Delete workspace" });
		expect(confirm).toBeDisabled();
		await owner.user.type(within(dlg).getByLabelText(/Type/), "Home collection policy");
		await owner.user.click(confirm);
		await waitFor(() => expect(owner.pathname()).toBe("/portal"));
		expect(calls("DELETE", `/workspaces/${WS_DRAFT}`)).toHaveLength(1);
	});
});

describe("editing lock", () => {
	it("start → edit → stop, sessions send no user, and leaving releases the lock", async () => {
		const app = await openWorkspace(WS_DRAFT, "&panel=chunks");
		expect(screen.getByTestId("lock-bar")).toHaveTextContent("Nobody is editing");
		await screen.findByRole("article", { name: "Chunk 1" });
		expect(within(card(1)).getByLabelText("Text of chunk 1")).toHaveAttribute("readonly");
		expect(within(card(1)).queryByRole("button", { name: "Save text" })).toBeNull();

		await startEditing(app);
		expect(calls("POST", "/lock")[0]?.body).toEqual({});
		const text = within(card(1)).getByLabelText("Text of chunk 1");
		await app.user.clear(text);
		await app.user.type(text, "Home collection: Kothrud, Baner and Aundh, 7 to 11 am.");
		await app.user.click(within(card(1)).getByRole("button", { name: "Save text" }));
		expect(await screen.findByText("Chunk 1 saved")).toBeInTheDocument();
		const patch = calls("PATCH", `/workspaces/${WS_DRAFT}/chunks/${CH(0)}`)[0];
		expect(patch?.body).toEqual({ text: "Home collection: Kothrud, Baner and Aundh, 7 to 11 am." });

		await app.user.click(screen.getByRole("button", { name: "Stop editing" }));
		await waitFor(() => expect(screen.getByTestId("lock-bar")).toHaveAttribute("data-state", "free"));
		expect(calls("POST", "/unlock")).toHaveLength(1);

		await startEditing(app);
		await app.router.navigate({ to: "/portal", search: { kb: PATIENT } });
		await waitFor(() => expect(calls("POST", "/unlock")).toHaveLength(2));
		expect(portalState.workspaces.find((w) => w.id === WS_DRAFT)?.locked_by).toBeNull();
	});

	it("someone else's lock: read-only with the holder's name; Start editing gets 423", async () => {
		const w = portalState.workspaces.find((x) => x.id === WS_DRAFT);
		if (w) {
			w.locked_by = ROHAN.email;
			w.locked_at = "2026-09-30T05:12:00Z";
		}
		const app = await openWorkspace(WS_DRAFT, "&panel=chunks");
		const bar = screen.getByTestId("lock-bar");
		expect(bar).toHaveAttribute("data-state", "other");
		expect(bar).toHaveTextContent(/rohan@shree-labs\.in is editing since .*You can read, not change\./);
		await app.user.click(screen.getByRole("button", { name: "Start editing" }));
		await waitFor(() => expect(calls("POST", "/lock")).toHaveLength(1));
		expect(screen.getByTestId("lock-bar")).toHaveAttribute("data-state", "other");
		expect(await screen.findByRole("article", { name: "Chunk 1" })).toBeInTheDocument();
		expect(within(card(1)).queryByRole("button", { name: "Approve" })).toBeNull();
	});

	it("an API key names its principal as the actor", async () => {
		sessionStorage.setItem(KEY_KEY, JSON.stringify({ key: TENANT_KEY, ragUrl: BASE }));
		const app = renderApp(`/portal/${WS_DRAFT}?kb=${PATIENT}&panel=chunks`);
		await screen.findByTestId("lock-bar");
		await startEditing(app);
		expect(calls("POST", "/lock")[0]?.body).toEqual({ user: "Shree Labs" });
		await app.user.click(await within(card(1)).findByRole("button", { name: "Approve" }));
		await screen.findByText("Chunk 1 approved");
		expect(calls("POST", `/chunks/${CH(0)}/approve`)[0]?.body).toEqual({ user: "Shree Labs" });
	});

	it("a lost lock (uncoded 409) says so", async () => {
		const app = await openWorkspace(WS_DRAFT, "&panel=chunks");
		await startEditing(app);
		const w = portalState.workspaces.find((x) => x.id === WS_DRAFT);
		if (w) w.locked_by = null;
		await app.user.click(within(card(1)).getByRole("button", { name: "Approve" }));
		expect(await screen.findByText("Your editing lock was lost. Start editing again.")).toBeInTheDocument();
	});
});

describe("staging", () => {
	it("adds pasted text, a page address and a file; polls the AI status every 3 s", async () => {
		const app = await openWorkspace();
		expect(await screen.findByText("Home collection rules")).toBeInTheDocument();
		expect(screen.getByText("Structured")).toBeInTheDocument();
		await app.user.click(screen.getByRole("radio", { name: "Paste text" }));
		await app.user.click(screen.getByRole("button", { name: "Add" }));
		expect(await screen.findByText("Paste some text.")).toBeInTheDocument();
		await app.user.type(screen.getByLabelText("Text"), "Samples reach the lab within 2 hours.");
		await app.user.type(screen.getByLabelText("Title (optional)"), "Sample transport");
		await app.user.click(screen.getByRole("button", { name: "Add" }));
		expect(await screen.findByText("Sample transport")).toBeInTheDocument();
		expect(calls("POST", "/content")[0]?.body).toEqual({
			source_type: "text_dump",
			content: "Samples reach the lab within 2 hours.",
			title: "Sample transport",
		});
		// pending → processing (first list) → completed (next poll, 3 s later).
		expect(await screen.findByText("Structuring")).toBeInTheDocument();
		expect(screen.getByTestId("staging-polling")).toBeInTheDocument();
		await waitFor(() => expect(screen.queryByTestId("staging-polling")).toBeNull(), { timeout: 6000 });
		expect(screen.getAllByText("Structured")).toHaveLength(2);

		await app.user.click(screen.getByRole("radio", { name: "Web page" }));
		await app.user.type(screen.getByLabelText("Page address"), "shree-labs.in/home");
		await app.user.click(screen.getByRole("button", { name: "Add" }));
		expect(await screen.findByText("Use a full address starting with http:// or https://.")).toBeInTheDocument();
		await app.user.clear(screen.getByLabelText("Page address"));
		await app.user.type(screen.getByLabelText("Page address"), "https://shree-labs.in/home-collection");
		await app.user.click(screen.getByRole("button", { name: "Add" }));
		await waitFor(() => expect(calls("POST", "/content")).toHaveLength(2));
		expect(calls("POST", "/content")[1]?.body).toEqual({
			source_type: "url",
			content: "https://shree-labs.in/home-collection",
			title: null,
		});

		await app.user.click(screen.getByRole("radio", { name: "File" }));
		const file = new File(["%PDF-1.4"], "home-collection.pdf", { type: "application/pdf" });
		await app.user.upload(screen.getByLabelText("File"), file);
		await app.user.click(screen.getByRole("button", { name: "Add" }));
		await waitFor(() => expect(calls("POST", "/content")).toHaveLength(3));
		expect(String(calls("POST", "/content")[2]?.body && JSON.stringify(calls("POST", "/content")[2]?.body))).toContain(
			"file%3Ahome-collection.pdf",
		);
	}, 20_000);
});

describe("chunk editor", () => {
	it("approve, reject with a reason, split, merge, reorder and delete, with this workspace's ids", async () => {
		const app = await openWorkspace(WS_DRAFT, "&panel=chunks");
		await startEditing(app);
		expect(screen.getByTestId("chunk-counts")).toHaveTextContent("3 chunks · 1 approved");
		expect(within(card(3)).getByText(/Very short chunk/)).toBeInTheDocument();

		await app.user.click(within(card(1)).getByRole("button", { name: "Approve" }));
		await screen.findByText("Chunk 1 approved");
		expect(calls("POST", `/workspaces/${WS_DRAFT}/chunks/${CH(0)}/approve`)).toHaveLength(1);

		await app.user.click(within(card(3)).getByRole("button", { name: "Reject" }));
		const rej = await screen.findByRole("alertdialog", { name: "Reject chunk 3" });
		await app.user.type(within(rej).getByLabelText("Reason (optional)"), "Duplicate of chunk 1");
		await app.user.click(within(rej).getByRole("button", { name: "Reject chunk" }));
		await screen.findByText("Chunk 3 rejected");
		expect(calls("POST", `/workspaces/${WS_DRAFT}/chunks/${CH(2)}/reject`)[0]?.body).toEqual({
			reason: "Duplicate of chunk 1",
		});

		// Keyboard reorder: chunk 1 moves down; the whole order is sent.
		await app.user.click(within(card(1)).getByRole("button", { name: "Move chunk 1 down" }));
		await screen.findByText("Chunk moved to position 2");
		expect(calls("POST", `/workspaces/${WS_DRAFT}/chunks/reorder`)[0]?.body).toEqual({
			chunk_ids: [CH(1), CH(0), CH(2)],
		});

		// Split chunk 2 (the old chunk 1) at a typed position.
		await waitFor(() =>
			expect(within(card(2)).getByLabelText("Text of chunk 2")).toHaveValue(
				"Home collection is available in Kothrud, Baner and Aundh from 7 am to 11 am. Book by phone on +91 20 2545 1234.",
			),
		);
		await app.user.click(within(card(2)).getByRole("button", { name: "Split" }));
		const split = await screen.findByRole("dialog", { name: "Split chunk 2" });
		const pos = within(split).getByLabelText("Split at character");
		await app.user.clear(pos);
		await app.user.type(pos, "0");
		await app.user.tab(); // the error shows once the field has been left
		expect(await within(split).findByText("Pick a position between 1 and 110.")).toBeInTheDocument();
		// The button stays enabled: it names the problem instead of going dead.
		expect(within(split).getByRole("button", { name: "Split chunk" })).not.toBeDisabled();
		await app.user.click(within(split).getByRole("button", { name: "Split chunk" }));
		expect(calls("POST", `/workspaces/${WS_DRAFT}/chunks/${CH(0)}/split`)).toHaveLength(0);
		await app.user.clear(pos);
		await app.user.type(pos, "77");
		expect(within(split).getByRole("region", { name: "Second part" })).toHaveTextContent("Book by phone");
		await app.user.click(within(split).getByRole("button", { name: "Split chunk" }));
		await screen.findByText("Chunk 2 split in two");
		expect(calls("POST", `/workspaces/${WS_DRAFT}/chunks/${CH(0)}/split`)[0]?.body).toEqual({ position: 77 });

		// Merge needs two.
		await screen.findByRole("article", { name: "Chunk 4" });
		await app.user.click(within(card(2)).getByRole("checkbox", { name: "Select chunk 2 for merging" }));
		const bar = screen.getByTestId("merge-bar");
		expect(within(bar).getByText("Select at least 2 chunks to merge.")).toBeInTheDocument();
		expect(within(bar).getByRole("button", { name: "Merge 1 chunks" })).toBeDisabled();
		await app.user.click(within(card(3)).getByRole("checkbox", { name: "Select chunk 3 for merging" }));
		await app.user.selectOptions(within(bar).getByLabelText("Join with"), "space");
		await app.user.click(within(bar).getByRole("button", { name: "Merge 2 chunks" }));
		await screen.findByText("2 chunks merged");
		const merge = calls("POST", `/workspaces/${WS_DRAFT}/chunks/merge`)[0]?.body as {
			chunk_ids: string[];
			separator: string;
		};
		expect(merge.chunk_ids).toHaveLength(2);
		expect(merge.separator).toBe(" ");

		await app.user.click(within(card(1)).getByRole("button", { name: "Delete" }));
		const del = await screen.findByRole("alertdialog", { name: "Delete chunk 1?" });
		await app.user.click(within(del).getByRole("button", { name: "Delete" }));
		await screen.findByText("Chunk 1 deleted");
		expect(calls("DELETE", `/workspaces/${WS_DRAFT}/chunks/${CH(1)}`)).toHaveLength(1);
		for (const r of portalState.requests.filter((x) => x.path.includes("/chunks/"))) {
			expect(r.path).toContain(`/workspaces/${WS_DRAFT}/`);
		}
	}, 20_000);

	it("adds a chunk linked to a staged document; filters by status", async () => {
		const app = await openWorkspace(WS_DRAFT, "&panel=chunks");
		await startEditing(app);
		await app.user.click(screen.getByRole("button", { name: "Add chunk" }));
		const dlg = await screen.findByRole("dialog", { name: "Add a chunk" });
		await app.user.type(within(dlg).getByLabelText("Text"), "Reports are e-mailed within 24 hours.");
		await app.user.selectOptions(within(dlg).getByLabelText("Position"), "Before chunk 2");
		await app.user.selectOptions(within(dlg).getByLabelText("Staged document (optional)"), "Same document as chunk 1");
		await app.user.click(within(dlg).getByRole("button", { name: "Add chunk" }));
		await screen.findByText("Chunk added");
		expect(calls("POST", `/workspaces/${WS_DRAFT}/chunks`)[0]?.body).toEqual({
			text: "Reports are e-mailed within 24 hours.",
			chunk_index: 1,
			staged_document_id: STAGED_DOC,
		});
		await app.user.click(screen.getByRole("radio", { name: "Approved" }));
		await waitFor(() => expect(app.router.state.location.search).toMatchObject({ status: "approved" }));
		await waitFor(() => expect(screen.getAllByRole("article")).toHaveLength(1));
		expect(calls("GET", `/workspaces/${WS_DRAFT}/chunks`).at(-1)?.query).toContain("status=approved");
		expect(within(screen.getByRole("article")).queryByRole("button", { name: /^Move chunk/ })).toBeNull();
	});

	it("refuses a parent chunk's edit with its message", async () => {
		const app = await openWorkspace(WS_DRAFT, "&panel=chunks");
		await startEditing(app);
		server.use(
			http.post(`${BASE}/api/v1/portal/workspaces/:id/chunks/:cid/approve`, () =>
				HttpResponse.json({ detail: "parent", code: "parent_chunk_derived" }, { status: 409 }),
			),
		);
		await app.user.click(within(card(1)).getByRole("button", { name: "Approve" }));
		expect(await screen.findByText(/That's a parent chunk: its text is rebuilt from its children/)).toBeInTheDocument();
	});
});

describe("review", () => {
	it("submit → approve with a comment → ingest; send back needs feedback", async () => {
		const app = await openWorkspace();
		const bar = screen.getByRole("region", { name: "Review" });
		await app.user.click(within(bar).getByRole("button", { name: "Submit for review" }));
		await screen.findByText("Submitted for review");
		await within(bar).findByRole("button", { name: "Approve" });

		await app.user.click(within(bar).getByRole("button", { name: "Send back" }));
		const back = await screen.findByRole("alertdialog", { name: "Send “Home collection policy” back to draft" });
		await app.user.click(within(back).getByRole("button", { name: "Send back" }));
		expect(await within(back).findByText("Say what needs to change.")).toBeInTheDocument();
		await app.user.keyboard("{Escape}");

		await app.user.click(within(bar).getByRole("button", { name: "Approve" }));
		const ok = await screen.findByRole("alertdialog", { name: "Approve “Home collection policy”" });
		await app.user.type(within(ok).getByLabelText("Comment (optional)"), "Checked the Baner timings.");
		await app.user.click(within(ok).getByRole("button", { name: "Approve" }));
		await screen.findByText("Workspace approved");
		expect(calls("POST", "/approve")[0]?.body).toEqual({ comment: "Checked the Baner timings." });

		await app.user.click(await within(bar).findByRole("button", { name: "Ingest" }));
		expect(await screen.findByText("Ingest started: 3 chunks")).toBeInTheDocument();
		expect(within(bar).getByRole("link", { name: "View the job" })).toHaveAttribute(
			"href",
			`/add?kb=${PATIENT}&tab=jobs`,
		);
		expect(calls("POST", "/ingest")[0]?.body).toEqual({});
	});

	it("ingest over the plan's documents shows document_limit_reached", async () => {
		const w = portalState.workspaces.find((x) => x.id === WS_DRAFT);
		if (w) w.status = "approved";
		server.use(
			http.post(`${BASE}/api/v1/portal/workspaces/:id/ingest`, () =>
				HttpResponse.json(
					{ detail: "limit", code: "document_limit_reached", limit: 50, used: 50, adding: 1 },
					{ status: 409 },
				),
			),
		);
		const app = await openWorkspace();
		await app.user.click(screen.getByRole("button", { name: "Ingest" }));
		expect(await screen.findByText("Your plan allows 50 documents (50 used; this adds 1).")).toBeInTheDocument();
	});

	it("publishes a post-ingest review as a new version and shows the summary", async () => {
		const app = await openWorkspace(WS_REVIEW);
		expect(await screen.findByTestId("review-counts")).toHaveTextContent("2 chunks · 2 kept · 0 rejected · 0 changed");
		expect(screen.getByRole("tab", { name: "Chunks" })).toHaveAttribute("aria-selected", "true");
		expect(screen.queryByRole("tab", { name: "Staging" })).toBeNull();
		await app.user.click(screen.getByRole("button", { name: "Publish as new version" }));
		const summary = await screen.findByTestId("post-ingest-summary");
		expect(summary).toHaveTextContent("7e500003-0000-4000-8000-000000000003");
		expect(summary).toHaveTextContent("Parent edits not published");
		expect(within(summary).getByRole("link", { name: "Open document" })).toHaveAttribute(
			"href",
			`/documents/${DOC_ID}?kb=${PATIENT}&tab=versions`,
		);
		expect(calls("POST", "/finalize-review")[0]?.body).toEqual({});
	});

	const FINALIZE_ERRORS: [number, Record<string, unknown>, string][] = [
		[409, { code: "version_not_active" }, "The document changed since this review started."],
		[409, { code: "wrong_workspace_kind" }, "That action doesn't apply to this kind of workspace."],
		[
			409,
			{ code: "workspace_locked", detail: "Workspace is locked by 'rohan@shree-labs.in'; they must unlock it" },
			"rohan@shree-labs.in is editing; ask them to finish.",
		],
		[409, { code: "invalid_status_transition" }, "Already published."],
		[422, { code: "nothing_to_finalize" }, "Every chunk was rejected or deleted."],
		[503, { code: "finalize_failed", stage: "embedding" }, "Nothing changed; the embedding step failed. Try again."],
		[409, { code: "document_busy" }, "The document is being changed by another operation"],
		[409, { code: "review_changed" }, "Chunks changed while the new version was being built."],
		[409, { code: "reviewed_document_deleted" }, "The document this review was made from was deleted"],
	];

	it("shows every coded finalize refusal", async () => {
		const app = await openWorkspace(WS_REVIEW);
		const bar = screen.getByRole("region", { name: "Review" });
		for (const [status, body, text] of FINALIZE_ERRORS) {
			portalState.finalizeError = { status, body: { detail: "refused", ...body } };
			await app.user.click(within(bar).getByRole("button", { name: "Publish as new version" }));
			await waitFor(() => expect(within(bar).getByRole("alert")).toHaveTextContent(text));
		}
	}, 20_000);
});

describe("post-ingest review from a document", () => {
	it("creates the review of the active version and opens it; review_exists links the open one", async () => {
		signedInAs(PRIYA);
		const app = renderApp(`/documents/${DOC_PRICE}?kb=${PATIENT}`);
		const button = await screen.findByRole("button", { name: "Review in Content Portal" });
		await app.user.click(button);
		expect(
			await screen.findByRole("heading", { level: 1, name: "Review: test-price-list-oct.pdf" }),
		).toBeInTheDocument();
		expect(calls("POST", "/from-ingestion")[0]?.body).toEqual({
			document_version_id: DOC_VERSION,
			title: "Review: test-price-list-oct.pdf",
		});

		// The refusal names the open review: opened directly, no listing lookup.
		const named = "9a0000ff-0000-4000-8000-000000000001";
		portalState.fromIngestionError = {
			status: 409,
			body: { detail: "exists", code: "review_exists", workspace_id: named },
		};
		await app.router.navigate({
			to: "/documents/$documentId",
			params: { documentId: DOC_PRICE },
			search: { kb: PATIENT },
		});
		const listings = () =>
			portalState.requests.filter((r) => r.method === "GET" && r.path.endsWith("/portal/workspaces")).length;
		const before = listings();
		await app.user.click(await screen.findByRole("button", { name: "Review in Content Portal" }));
		expect(await screen.findByText("A review of this version is already open.")).toBeInTheDocument();
		expect(listings()).toBe(before);
		await app.user.click(await screen.findByRole("button", { name: "Open that review" }));
		await waitFor(() => expect(app.pathname()).toBe(`/portal/${named}`));

		// An engine without workspace_id: the open review is found in the listing instead.
		portalState.fromIngestionError = { status: 409, body: { detail: "exists", code: "review_exists" } };
		await app.router.navigate({
			to: "/documents/$documentId",
			params: { documentId: DOC_PRICE },
			search: { kb: PATIENT },
		});
		await app.user.click(await screen.findByRole("button", { name: "Review in Content Portal" }));
		expect(await screen.findByText("A review of this version is already open.")).toBeInTheDocument();
		await app.user.click(await screen.findByRole("button", { name: "Open that review" }));
		await waitFor(() => expect(app.pathname()).toMatch(/^\/portal\/9a00000[23]-/));

		portalState.fromIngestionError = { status: 409, body: { detail: "old", code: "version_not_active" } };
		await app.router.navigate({
			to: "/documents/$documentId",
			params: { documentId: DOC_PRICE },
			search: { kb: PATIENT },
		});
		await app.user.click(await screen.findByRole("button", { name: "Review in Content Portal" }));
		expect(await screen.findByText("Only the active version can be reviewed.")).toBeInTheDocument();
	});

	it("is not offered when the portal is off", async () => {
		signedInAs(PRIYA);
		server.use(
			http.get(`${BASE}/api/v1/info`, async () => {
				const { INFO } = await import("../msw/handlers/identity");
				return HttpResponse.json(INFO);
			}),
		);
		renderApp(`/documents/${DOC_PRICE}?kb=${PATIENT}`);
		await screen.findByRole("heading", { level: 1, name: "test-price-list-oct.pdf" });
		expect(screen.queryByRole("button", { name: "Review in Content Portal" })).toBeNull();
	});
});

describe("AI chat and audit trail", () => {
	it("sends a non-streaming message and applies a proposed change", async () => {
		const app = await openWorkspace(WS_DRAFT, "&panel=chat");
		await app.user.type(await screen.findByLabelText("Message to the AI"), "Make chunk 3 clearer");
		await app.user.click(screen.getByRole("button", { name: "Send" }));
		const proposal = await screen.findByRole("region", { name: "Proposed change: Rewrite a chunk" });
		expect(calls("POST", "/chat")[0]?.body).toEqual({ message: "Make chunk 3 clearer", stream: false });
		expect(screen.getByText("Chunk 3 is very short. I suggest a rewrite:")).toBeInTheDocument();
		expect(within(proposal).getByText("Chunk 3")).toBeInTheDocument();
		expect(within(proposal).getByRole("button", { name: "Apply" })).toBeDisabled();
		expect(within(proposal).getByText("Start editing to apply changes.")).toBeInTheDocument();
		await startEditing(app);
		await app.user.click(within(proposal).getByRole("button", { name: "Apply" }));
		expect(await within(proposal).findByText("Applied: Chunk rewritten")).toBeInTheDocument();
		expect(calls("POST", "/chat/execute")[0]?.body).toMatchObject({
			action: { tool_name: "rewrite_chunk", arguments: { chunk_id: CH(2) } },
		});
	});

	it("audit trail marks removed and added text with − and +", async () => {
		await openWorkspace(WS_DRAFT, "&panel=audit");
		const table = await screen.findByRole("table", { name: "Audit trail" });
		const edit = within(table).getAllByRole("row")[1];
		if (!edit) throw new Error("expected an entry");
		expect(within(edit).getByText("Edited text")).toBeInTheDocument();
		expect(within(edit).getByText("#1")).toBeInTheDocument();
		expect(within(table).getByText("Home collection 7–11.").parentElement).toHaveTextContent(
			"− Removed text: Home collection 7–11.",
		);
		expect(within(table).getByText(/available from 7 am to 11 am/).parentElement).toHaveTextContent("+ Added text:");
	});

	it("audit trail: object details as a key/value list; an entry without a time last, with —", async () => {
		await openWorkspace(WS_DRAFT, "&panel=audit");
		const table = await screen.findByRole("table", { name: "Audit trail" });
		const [, , split, merged] = within(table).getAllByRole("row");
		if (!split || !merged) throw new Error("expected three entries");
		expect(within(split).getByText("Split")).toBeInTheDocument();
		const details = split.querySelector("dl");
		expect(details).toHaveTextContent("Split at character:120");
		expect(details).toHaveTextContent("From chunk:#2");
		expect(split).not.toHaveTextContent("{");
		expect(within(merged).getByText("Merged (AI)")).toBeInTheDocument();
		expect(within(merged).getByText("Time not recorded")).toBeInTheDocument();
		expect(merged.querySelector("dl")).toHaveTextContent("Merged chunks:#1, #2");
	});
});

describe("routing rules", () => {
	it("lists rules and creates one with the condition fields of its type", async () => {
		signedInAs(PRIYA);
		const app = renderApp(`/portal?kb=${PATIENT}&tab=rules`);
		const table = await screen.findByRole("table", { name: "Routing rules" });
		expect(within(table).getByText("Type is application/pdf")).toBeInTheDocument();
		expect(within(table).getByText("Stage in the portal")).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Add rule" }));
		const dlg = await screen.findByRole("dialog", { name: "Add a routing rule" });
		expect(within(dlg).getByLabelText("File types")).toBeInTheDocument();
		await app.user.selectOptions(within(dlg).getByLabelText("When"), "size");
		expect(within(dlg).queryByLabelText("File types")).toBeNull();
		await app.user.click(within(dlg).getByRole("button", { name: "Save rule" }));
		expect(await within(dlg).findByText("Give at least one size.")).toBeInTheDocument();
		await app.user.type(within(dlg).getByLabelText("Larger than (MB)"), "10");
		await app.user.selectOptions(within(dlg).getByLabelText("Then"), "direct_ingest");
		await app.user.clear(within(dlg).getByLabelText("Priority"));
		await app.user.type(within(dlg).getByLabelText("Priority"), "5");
		await app.user.click(within(dlg).getByRole("button", { name: "Save rule" }));
		await screen.findByText("Rule added");
		expect(calls("POST", `/corpus/${PATIENT}/rules`)[0]?.body).toEqual({
			rule_type: "size",
			condition: { size_gt_mb: 10 },
			action: "direct_ingest",
			priority: 5,
			enabled: true,
		});
		expect(await within(table).findByText("Larger than 10 MB")).toBeInTheDocument();

		await app.user.click(within(table).getByRole("button", { name: "Delete rule 1" }));
		const del = await screen.findByRole("alertdialog", { name: "Delete this rule?" });
		await app.user.click(within(del).getByRole("button", { name: "Delete" }));
		await screen.findByText("Rule deleted");
		expect(portalState.rules).toHaveLength(1);
	});
});

describe("helpers", () => {
	it("rule bodies carry only the condition keys of their type", () => {
		const base = {
			mime_types: "",
			size_gt_mb: "",
			size_lt_mb: "",
			confidence_below: "",
			action: "stage" as const,
			priority: "100",
			enabled: true,
		};
		expect(toRuleBody({ ...base, rule_type: "mime_type", mime_types: "text/html, application/pdf" }).condition).toEqual(
			{
				mime_types: ["text/html", "application/pdf"],
			},
		);
		expect(toRuleBody({ ...base, rule_type: "mime_type", mime_types: "application/pdf" }).condition).toEqual({
			mime_type: "application/pdf",
		});
		expect(toRuleBody({ ...base, rule_type: "confidence", confidence_below: "0.7" }).condition).toEqual({
			confidence_below: 0.7,
		});
		expect(toRuleBody({ ...base, rule_type: "always" }).condition).toEqual({});
		expect(ruleSchema.safeParse({ ...base, rule_type: "confidence", confidence_below: "1.5" }).success).toBe(false);
		expect(ruleSchema.safeParse({ ...base, rule_type: "always", priority: "10001" }).success).toBe(false);
		expect(conditionText({ rule_type: "size", condition: { size_gt_mb: 5, size_lt_mb: 50 } })).toBe(
			"Larger than 5 MB and smaller than 50 MB",
		);
	});

	it("split bounds, reorder moves and parent copies", () => {
		expect(validSplit(0, "abc")).toBe(false);
		expect(validSplit(3, "abc")).toBe(false);
		expect(validSplit(1, "abc")).toBe(true);
		expect(suggestSplit("First sentence. Second sentence here.")).toBe(16);
		expect(moveItem(["a", "b", "c"], 0, 2)).toEqual(["b", "c", "a"]);
		expect(moveItem(["a", "b", "c"], 2, -5)).toEqual(["c", "a", "b"]);
		const mk = (id: string, meta: Record<string, unknown>) =>
			({ id, metadata_: meta }) as unknown as Parameters<typeof parentCopyIds>[0][number];
		expect([...parentCopyIds([mk("p", { engine_chunk_id: "E1" }), mk("c", { parent_chunk_id: "E1" })])]).toEqual(["p"]);
	});

	it("portal messages depend on where the error happened", () => {
		const e = (status: number, code: string | null, detail = "") => new ApiError({ status, code, detail });
		expect(portalMessage(e(409, "version_not_active"), "finalize").title).toMatch(/changed since this review/);
		expect(portalMessage(e(409, "version_not_active")).title).toBe("Only the active version can be reviewed.");
		expect(portalMessage(e(423, null, "Workspace locked by 'rohan@shree-labs.in' until 11:12"), "lock").title).toBe(
			"rohan@shree-labs.in is editing; you can read, not change.",
		);
		expect(portalMessage(e(503, null), "chat").title).toBe("The AI isn't available right now. Try again in a minute.");
		expect(portalMessage(e(503, null), "upload").title).toBe(
			"Storage is unavailable; nothing was stored. Try again shortly.",
		);
	});
});
