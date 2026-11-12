import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, rt, SHREE, tokenResponse } from "../msw/data";
import { PATIENT } from "../msw/handlers/documents";
import { server } from "../msw/server";
import { renderApp } from "../render";

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

const job = (over: Record<string, unknown>) => ({
	id: "j1",
	corpus_id: PATIENT,
	status: "failed",
	file_name: "menu.pdf",
	mode: "auto",
	created_at: "2026-10-04T09:00:00Z",
	finished_at: "2026-10-04T09:01:00Z",
	expires_at: null,
	processed_documents: 0,
	total_documents: 1,
	errors: { error: "The PDF has no readable text" },
	records: null,
	target_document_id: null,
	similar_documents: null,
	retried: false,
	...over,
});

function listJobs(items: Record<string, unknown>[]) {
	server.use(
		http.get(`${BASE}/api/v1/ingest/jobs`, () =>
			HttpResponse.json({ items, count: items.length, total: items.length, offset: 0, limit: 20 }),
		),
	);
}

describe("retry a failed upload", () => {
	it("a failed upload that already runs again offers no second Retry", async () => {
		signedIn();
		listJobs([job({ retried: true })]);
		renderApp(`/add?kb=${PATIENT}&tab=jobs`);
		const table = await screen.findByRole("table", { name: "Ingestion jobs" });
		expect(within(table).queryByRole("button", { name: /^Retry/ })).toBeNull();
	});

	it("a failed upload has a Retry button; it posts to the retry route and confirms", async () => {
		signedIn();
		listJobs([job({}), job({ id: "j2", file_name: null, mode: "records" }), job({ id: "j3", status: "completed" })]);
		let retried: string | null = null;
		server.use(
			http.post(`${BASE}/api/v1/ingest/jobs/:id/retry`, ({ params }) => {
				retried = String(params.id);
				return HttpResponse.json({ job_id: "j9", status: "queued" }, { status: 202 });
			}),
		);
		const app = renderApp(`/add?kb=${PATIENT}&tab=jobs`);
		const table = await screen.findByRole("table", { name: "Ingestion jobs" });
		// Only the failed upload offers it: not the records import, not a finished job.
		expect(within(table).getAllByRole("button", { name: /^Retry/ })).toHaveLength(1);
		await app.user.click(within(table).getByRole("button", { name: "Retry menu.pdf" }));
		expect(await screen.findByText("Running menu.pdf again.")).toBeInTheDocument();
		expect(retried).toBe("j1");
		// The row did not open its drawer.
		expect(screen.queryByRole("dialog")).toBeNull();
	});

	it("says why when the engine cannot retry it", async () => {
		signedIn();
		listJobs([job({})]);
		server.use(
			http.post(`${BASE}/api/v1/ingest/jobs/:id/retry`, () =>
				HttpResponse.json(
					{
						detail: "The stored file is gone, so please upload it again.",
						code: "job_not_retryable",
						reason: "file_missing",
					},
					{ status: 409 },
				),
			),
		);
		const app = renderApp(`/add?kb=${PATIENT}&tab=jobs`);
		await app.user.click(await screen.findByRole("button", { name: "Retry menu.pdf" }));
		expect(await screen.findByText("The stored file is gone, so please upload it again.")).toBeInTheDocument();
	});

	it("the drawer of a failed upload has it too", async () => {
		signedIn();
		listJobs([job({})]);
		const app = renderApp(`/add?kb=${PATIENT}&tab=jobs`);
		await app.user.click(await screen.findByRole("button", { name: "Details: menu.pdf" }));
		const drawer = await screen.findByRole("dialog", { name: "Job for menu.pdf" });
		expect(within(drawer).getByRole("button", { name: "Retry menu.pdf" })).toBeInTheDocument();
	});
});
