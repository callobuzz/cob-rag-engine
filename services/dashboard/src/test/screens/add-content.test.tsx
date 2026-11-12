import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";
import { uploadQueue } from "@/features/add-content/upload-queue";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, rt, SHREE, tokenResponse } from "../msw/data";
import { DOC_PRICE, PATIENT } from "../msw/handlers/documents";
import { INFO } from "../msw/handlers/identity";
import { ingestState } from "../msw/handlers/ingest";
import { server } from "../msw/server";
import { renderApp } from "../render";

afterEach(() => {
	server.events.removeAllListeners();
	uploadQueue.reset();
});

/**
 * jsdom's XMLHttpRequest cannot send a FormData holding a File through MSW's interceptor
 * ("reading _buffer"), so these screen tests swap the XHR transport for fetch with the same
 * fields (the file as "file:<name>") and the same error handling. upload.ts itself is D1's.
 */
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

function signedIn() {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user: PRIYA, tenant: SHREE, tenants: [SHREE] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () =>
			HttpResponse.json({ user: PRIYA, tenant: SHREE, tenants: [SHREE], session_id: "s" }),
		),
		http.get(`${BASE}/api/v1/corpora`, () => {
			const items = CORPORA.filter((c) => c.tenant_id === SHREE.id);
			return HttpResponse.json({ items, count: items.length, total: items.length, offset: 0, limit: 500 });
		}),
	);
}

const pdf = (name = "test-price-list-oct.pdf", size = 2048) =>
	new File([new Uint8Array(size)], name, { type: "application/pdf" });

async function openUpload(path = `/add?kb=${PATIENT}`) {
	const app = renderApp(path);
	await screen.findByRole("heading", { level: 1, name: "Add content" });
	await screen.findByText(/Accepted: \.csv, \.docx, \.pdf, \.txt/);
	return app;
}

function choose(files: File[]) {
	fireEvent.change(screen.getByTestId("file-input"), { target: { files } });
}

const row = (name: string) => screen.getByRole("listitem", { name });

describe("upload", () => {
	it("the dropzone is a labelled button and refuses wrong types and oversize files before upload", async () => {
		signedIn();
		server.use(
			http.get(`${BASE}/api/v1/info`, () =>
				HttpResponse.json({
					...INFO,
					limits: { ...INFO.limits, max_upload_bytes: 1000, max_document_tokens: 10_000_000 },
				}),
			),
		);
		const app = renderApp(`/add?kb=${PATIENT}`);
		const zone = await screen.findByRole("button", { name: "Upload files. Drop files here or press Enter to choose." });
		expect(zone).toHaveAttribute("tabindex", "0");
		await screen.findByText(/Up to 1000 B each/);
		expect(screen.getByText(/tokens of text each/)).toBeInTheDocument();
		expect(screen.queryByText(/refuses files over about/)).toBeNull();
		choose([new File(["MZ"], "setup.exe", { type: "application/x-msdownload" }), pdf("big.pdf", 5000)]);
		expect(await screen.findByText(/This engine doesn't accept \.exe files\./)).toBeInTheDocument();
		expect(screen.getByText(/4\.9 KB is over the 1000 B limit\./)).toBeInTheDocument();
		expect(screen.queryByRole("button", { name: /^Upload \d+ files?$/ })).toBeNull();
		await app.user.click(screen.getByRole("button", { name: "Clear finished" }));
		expect(screen.queryByTestId("upload-row")).toBeNull();
		expect(ingestState.uploads).toHaveLength(0);
	});

	it("a file moves through the real stages to Done; the form carries corpus, key and mode", async () => {
		signedIn();
		const app = await openUpload();
		choose([pdf()]);
		await app.user.click(await screen.findByRole("button", { name: "Upload 1 file" }));
		const r = row("test-price-list-oct.pdf");
		const bar = await within(r).findByRole("progressbar", { name: "Uploading test-price-list-oct.pdf" });
		expect(bar).toHaveAttribute("aria-valuenow");
		await waitFor(() => expect(within(r).getByText("Done")).toBeInTheDocument(), { timeout: 12_000 });
		const sent = ingestState.uploads[0];
		expect(sent).toMatchObject({
			corpus_id: PATIENT,
			mode: "auto",
			auto_confirm: "false",
			file: "file:test-price-list-oct.pdf",
		});
		expect(sent?.idempotency_key).toMatch(/[0-9a-f-]{36}/);
		expect(sent?.target_document_id).toBeUndefined();
	}, 20_000);

	it("needs review in the row: Replace confirms with the similar document", async () => {
		signedIn();
		ingestState.script = ["queued", "pending_review"];
		const app = await openUpload();
		choose([pdf()]);
		await app.user.click(await screen.findByRole("button", { name: "Upload 1 file" }));
		const r = row("test-price-list-oct.pdf");
		const card = await within(r).findByTestId("pending-review", undefined, { timeout: 8000 });
		await app.user.click(within(card).getByRole("button", { name: "Replace test-price-list-oct.pdf" }));
		await waitFor(() =>
			expect(ingestState.confirms.at(-1)?.body).toEqual({ action: "replace", target_document_id: DOC_PRICE }),
		);
		await waitFor(() => expect(within(r).getByText("Done")).toBeInTheDocument(), { timeout: 12_000 });
	}, 25_000);

	it("needs review in the row: Skip (don't add) drops it and the row shows Skipped", async () => {
		signedIn();
		ingestState.script = ["queued", "pending_review"];
		const app = await openUpload();
		choose([pdf()]);
		await app.user.click(await screen.findByRole("button", { name: "Upload 1 file" }));
		const r = row("test-price-list-oct.pdf");
		const card = await within(r).findByTestId("pending-review", undefined, { timeout: 8000 });
		await app.user.click(within(card).getByRole("button", { name: "Skip (don't add)" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Skip test-price-list-oct.pdf?" });
		await app.user.click(within(dialog).getByRole("button", { name: "Skip" }));
		await waitFor(() =>
			expect(ingestState.confirms.at(-1)?.body).toEqual({ action: "skip", target_document_id: null }),
		);
		await waitFor(() => expect(within(r).getByText("Skipped")).toBeInTheDocument(), { timeout: 12_000 });
		expect(within(r).queryByTestId("pending-review")).toBeNull();
	}, 25_000);

	it("document_limit_reached explains the plan and links to it", async () => {
		signedIn();
		server.use(
			http.post(`${BASE}/api/v1/ingest`, () =>
				HttpResponse.json(
					{ detail: "Document limit reached (2)", code: "document_limit_reached", limit: 2, used: 2, adding: 1 },
					{ status: 409 },
				),
			),
		);
		const app = await openUpload();
		choose([pdf()]);
		await app.user.click(await screen.findByRole("button", { name: "Upload 1 file" }));
		expect(await screen.findByText("Your plan allows 2 documents (2 used; this adds 1).")).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "See plan" })).toHaveAttribute("href", "/plan");
	});

	it("Retry after a failure sends the same idempotency key", async () => {
		signedIn();
		let calls = 0;
		const keys: string[] = [];
		server.use(
			http.post(`${BASE}/api/v1/ingest`, async ({ request }) => {
				calls++;
				keys.push(String((await request.formData()).get("idempotency_key")));
				if (calls === 1)
					return HttpResponse.json({ detail: "Object storage is unavailable; nothing was stored" }, { status: 503 });
				return HttpResponse.json(
					{
						job_id: "10b09999-0000-4000-8000-000000000000",
						status: "queued",
						message: "Already accepted: this is the job",
					},
					{ status: 202 },
				);
			}),
			http.get(`${BASE}/api/v1/ingest/jobs/:id`, ({ params }) =>
				HttpResponse.json({
					id: params.id,
					status: "completed",
					processed_documents: 1,
					total_documents: 1,
					errors: null,
					created_at: "2026-09-30T09:00:00Z",
					finished_at: "2026-09-30T09:01:00Z",
				}),
			),
		);
		const app = await openUpload();
		choose([pdf()]);
		await app.user.click(await screen.findByRole("button", { name: "Upload 1 file" }));
		expect(await screen.findByText("Upload storage unavailable, try again.")).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Retry" }));
		expect(await screen.findByText("Already received")).toBeInTheDocument();
		expect(keys).toHaveLength(2);
		expect(keys[0]).toBe(keys[1]);
	});

	it("replace mode needs a target document, sent as target_document_id; metadata is validated", async () => {
		signedIn();
		const app = await openUpload();
		choose([pdf("test-price-list-nov.pdf")]);
		const r = await screen.findByRole("listitem", { name: "test-price-list-nov.pdf" });
		await app.user.selectOptions(within(r).getByRole("combobox", { name: "How to add it" }), "replace");
		expect(within(r).getByText("Pick the document it replaces or adds a version to.")).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Upload 0 files" })).toBeDisabled();
		await app.user.click(await within(r).findByRole("radio", { name: /test-price-list-oct\.pdf/ }));
		await app.user.click(within(r).getByRole("button", { name: "Advanced" }));
		const meta = within(r).getByRole("textbox", { name: "Metadata (JSON object of text values)" });
		fireEvent.change(meta, { target: { value: '{"branch": 7}' } });
		expect(within(r).getByText("Metadata must be a JSON object whose values are text.")).toBeInTheDocument();
		fireEvent.change(meta, { target: { value: '{"branch": "Kothrud"}' } });
		await app.user.click(within(r).getByRole("checkbox", { name: /Skip the duplicate check/ }));
		await app.user.click(screen.getByRole("button", { name: "Upload 1 file" }));
		await waitFor(() => expect(ingestState.uploads).toHaveLength(1));
		expect(ingestState.uploads[0]).toMatchObject({
			mode: "replace",
			target_document_id: DOC_PRICE,
			auto_confirm: "true",
			metadata: '{"branch":"Kothrud"}',
		});
	});

	it("runs at most 3 uploads at a time", async () => {
		signedIn();
		let inFlight = 0;
		let peak = 0;
		const release: (() => void)[] = [];
		server.use(
			http.post(`${BASE}/api/v1/ingest`, async () => {
				inFlight++;
				peak = Math.max(peak, inFlight);
				await new Promise<void>((r) => release.push(r));
				inFlight--;
				return HttpResponse.json({ job_id: crypto.randomUUID(), status: "queued" }, { status: 202 });
			}),
		);
		const app = await openUpload();
		choose(["a", "b", "c", "d", "e"].map((n) => pdf(`report-${n}.pdf`)));
		await app.user.click(await screen.findByRole("button", { name: "Upload 5 files" }));
		await waitFor(() => expect(release).toHaveLength(3));
		expect(screen.getAllByText("Waiting")).toHaveLength(2);
		for (const r of release.splice(0)) r();
		await waitFor(() => expect(release.length).toBeGreaterThan(0));
		for (const r of release.splice(0)) r();
		expect(peak).toBe(3);
	});
});

describe("records import", () => {
	const CSV =
		"faq_id,question,answer,category\nf1,रिपोर्ट कब मिलेगी?,Same day by 7 pm,reports\nf2,Is fasting needed?,10–12 hours for lipid profile,tests\nf3,,,\n,Open on Sundays?,7 am to 1 pm,timings\n";

	async function loadCsv(app: ReturnType<typeof renderApp>) {
		await screen.findByRole("heading", { level: 1, name: "Add content" });
		await app.user.click(screen.getByRole("tab", { name: "Import records" }));
		await screen.findByRole("textbox", { name: "Paste JSON" });
		fireEvent.change(screen.getByTestId("records-file"), {
			target: { files: [new File([CSV], "faq.csv", { type: "text/csv" })] },
		});
		return screen.findByRole("combobox", { name: /^ID/ });
	}

	it("CSV → mapping guessed → preview with validation → import with counts", async () => {
		signedIn();
		const app = renderApp(`/add?kb=${PATIENT}`);
		const idSelect = await loadCsv(app);
		expect(idSelect).toHaveValue("faq_id");
		expect(screen.getByRole("combobox", { name: /^Text/ })).toHaveValue("answer");
		expect(screen.getByRole("combobox", { name: "Title" })).toHaveValue("question");
		expect(screen.getByRole("checkbox", { name: "category" })).toBeChecked();
		await app.user.click(screen.getByRole("button", { name: "Next" }));
		expect(screen.getByTestId("records-count")).toHaveTextContent("2 records in 1 batch");
		const preview = screen.getByRole("table", { name: "The first 5 records as they will be sent" });
		expect(within(preview).getByText("रिपोर्ट कब मिलेगी?")).toBeInTheDocument();
		expect(within(preview).getByText("category: reports")).toBeInTheDocument();
		expect(screen.getByText("2 rows have problems and are left out of the import")).toBeInTheDocument();
		const errors = screen.getByRole("table", { name: "Rows with problems" });
		expect(within(errors).getByText("No id")).toBeInTheDocument();
		expect(within(errors).getByText("No text")).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Import 2 records" }));
		await waitFor(() => expect(ingestState.records).toHaveLength(1));
		expect(ingestState.records[0]).toEqual({
			corpus_id: PATIENT,
			records: [
				{ id: "f1", text: "Same day by 7 pm", title: "रिपोर्ट कब मिलेगी?", metadata: { category: "reports" } },
				{
					id: "f2",
					text: "10–12 hours for lipid profile",
					title: "Is fasting needed?",
					metadata: { category: "tests" },
				},
			],
		});
		expect(await screen.findByTestId("records-counts", undefined, { timeout: 8000 })).toHaveTextContent(
			"2 new · 0 updated · 0 unchanged",
		);
	});

	it("batches at the server's max_records_per_request", async () => {
		signedIn();
		server.use(
			http.get(`${BASE}/api/v1/info`, () =>
				HttpResponse.json({ ...INFO, limits: { ...INFO.limits, max_records_per_request: 1 } }),
			),
		);
		const app = renderApp(`/add?kb=${PATIENT}`);
		await loadCsv(app);
		await app.user.click(screen.getByRole("button", { name: "Next" }));
		expect(screen.getByTestId("records-count")).toHaveTextContent("2 records in 2 batches of up to 1");
		await app.user.click(screen.getByRole("button", { name: "Import 2 records" }));
		await waitFor(() => expect(ingestState.records).toHaveLength(2));
		expect(screen.getByText("Batch 1 of 2 · rows 1–1")).toBeInTheDocument();
		expect(screen.getByText("Batch 2 of 2 · rows 2–2")).toBeInTheDocument();
	});

	it("document_limit_reached on a batch stops the rest and explains the plan", async () => {
		signedIn();
		server.use(
			http.post(`${BASE}/api/v1/ingest/records`, () =>
				HttpResponse.json(
					{ detail: "Document limit reached (500)", code: "document_limit_reached", limit: 500, used: 499, adding: 2 },
					{ status: 409 },
				),
			),
		);
		const app = renderApp(`/add?kb=${PATIENT}`);
		await loadCsv(app);
		await app.user.click(screen.getByRole("button", { name: "Next" }));
		await app.user.click(screen.getByRole("button", { name: "Import 2 records" }));
		expect(await screen.findByText("Your plan allows 500 documents (499 used; this adds 2).")).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "See plan" })).toBeInTheDocument();
	});

	it("pasted JSON that isn't an array is explained", async () => {
		signedIn();
		const app = renderApp(`/add?kb=${PATIENT}&tab=records`);
		const box = await screen.findByRole("textbox", { name: "Paste JSON" });
		fireEvent.change(box, { target: { value: '{"id": "f1"}' } });
		await app.user.click(screen.getByRole("button", { name: "Read records" }));
		expect(await screen.findByText("Use an array of objects (one object per record).")).toBeInTheDocument();
	});
});

describe("jobs tab", () => {
	it("lists jobs with status badges and filters by status in the URL", async () => {
		signedIn();
		const seen: string[] = [];
		server.events.on("request:start", ({ request }) => {
			const u = new URL(request.url);
			if (u.pathname === "/api/v1/ingest/jobs") seen.push(u.searchParams.get("status") ?? "");
		});
		const app = renderApp(`/add?kb=${PATIENT}&tab=jobs`);
		const table = await screen.findByRole("table", { name: "Ingestion jobs" });
		expect(within(table).getByText("Needs review")).toBeInTheDocument();
		await app.user.selectOptions(screen.getByRole("combobox", { name: "Status" }), "failed");
		await waitFor(() => expect(seen).toContain("failed"));
		expect(app.router.state.location.search).toMatchObject({ tab: "jobs", status: "failed" });
		expect(await screen.findByText("No jobs with this status.")).toBeInTheDocument();
	});

	it("a row opens a drawer with the similar documents and the review decision", async () => {
		signedIn();
		const app = renderApp(`/add?kb=${PATIENT}&tab=jobs`);
		await app.user.click(await screen.findByRole("button", { name: "Details: test-price-list-oct (1).pdf" }));
		const drawer = await screen.findByRole("dialog", { name: "Job for test-price-list-oct (1).pdf" });
		expect(within(drawer).getByText("Similar documents")).toBeInTheDocument();
		expect(within(drawer).getByRole("button", { name: "Keep both (add as new)" })).toBeInTheDocument();
	});
});
