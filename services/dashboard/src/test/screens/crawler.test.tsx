import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { newCrawlSchema, toScrapeRequest } from "@/features/crawler/NewCrawlDialog";
import { isJobActive, jobBadge, jobStatusLabel, pageBadge, pageStatusLabel } from "@/features/crawler/statuses";
import { KEY_KEY, RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, ROHAN, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { TENANT_KEY } from "../msw/handlers/identity";
import {
	JOB_FAQ,
	JOB_SOPS,
	JOB_VIDYA,
	LIMIT_ERROR,
	MARKDOWN_HOME,
	PAGE_CAREERS,
	PAGE_HOME,
	PAGE_PRICES,
	PAGE_TIMINGS,
	REVIEW_REQUIRED,
	SCRAPER,
	scraperState,
} from "../msw/handlers/scraper";
import { server } from "../msw/server";
import { renderApp } from "../render";

const PATIENT = CORPORA[0]?.id ?? "";
const STAFF = CORPORA[1]?.id ?? "";
const MEMBER_SHREE = { ...SHREE, role: "member" as const };

function signedInAs(user: typeof PRIYA, tenant: typeof SHREE | null) {
	const tenants = tenant ? [tenant] : [];
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () => HttpResponse.json(tokenResponse({ user, tenant, tenants }, 2))),
		http.get(`${BASE}/api/v1/auth/me`, () => HttpResponse.json({ user, tenant, tenants, session_id: "s" })),
	);
	if (tenant) {
		server.use(
			http.get(`${BASE}/api/v1/corpora`, () => {
				const items = CORPORA.filter((c) => c.tenant_id === SHREE.id);
				return HttpResponse.json({ items, count: items.length, total: items.length, offset: 0, limit: 500 });
			}),
		);
	}
}

function tenantKey() {
	sessionStorage.setItem(KEY_KEY, JSON.stringify({ key: TENANT_KEY, ragUrl: BASE }));
}

async function openList(path = `/crawler?kb=${PATIENT}`, who: [typeof PRIYA, typeof SHREE | null] = [PRIYA, SHREE]) {
	signedInAs(...who);
	const app = renderApp(path);
	await screen.findByRole("heading", { level: 1, name: "Crawler" });
	return app;
}

async function openJob(id = JOB_FAQ, query = "", who: [typeof PRIYA, typeof SHREE | null] = [PRIYA, SHREE]) {
	signedInAs(...who);
	const app = renderApp(`/crawler/${id}${query}`);
	await screen.findByRole("heading", { level: 1, name: "shree-labs.in/faq" });
	await screen.findByRole("table", { name: "Pages of this crawl" });
	return app;
}

describe("statuses", () => {
	it("labels and tones: never colour alone, verifying means ready for review", () => {
		expect(jobStatusLabel({ status: "verifying", review_mode: true })).toBe("Ready for review");
		expect(jobStatusLabel({ status: "verifying", review_mode: false })).toBe("Delivering");
		expect(jobBadge({ status: "verifying", review_mode: true })).toBe("pending");
		expect(jobBadge({ status: "completed", review_mode: true })).toBe("completed");
		expect(jobBadge({ status: "crawling", review_mode: true })).toBe("running");
		expect(pageStatusLabel("extracted")).toBe("Read");
		expect(pageBadge("rejected")).toBe("suspended");
		expect(pageBadge("delivered")).toBe("completed");
	});

	it("still moving: crawling, or verifying with review mode off; not a job waiting for review", () => {
		expect(isJobActive({ status: "crawling", review_mode: true })).toBe(true);
		expect(isJobActive({ status: "pending", review_mode: true })).toBe(true);
		expect(isJobActive({ status: "verifying", review_mode: false })).toBe(true);
		expect(isJobActive({ status: "verifying", review_mode: true })).toBe(false);
		expect(isJobActive({ status: "completed", review_mode: false })).toBe(false);
		expect(isJobActive({ status: "failed", review_mode: true })).toBe(false);
	});
});

describe("new crawl form validation", () => {
	const base = { sitemap_url: "", urls: "", kb: PATIENT, max_pages: "50", review_mode: true };
	const issues = (v: Partial<typeof base>) => {
		const r = newCrawlSchema.safeParse({ ...base, ...v });
		return r.success ? [] : r.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`);
	};

	it("needs a sitemap or at least one page address", () => {
		expect(issues({})).toEqual(["urls: Enter a sitemap URL or at least one page address."]);
		expect(issues({ sitemap_url: "https://vidyaclasses.in/sitemap.xml" })).toEqual([]);
		expect(issues({ urls: "https://vidyaclasses.in/fees" })).toEqual([]);
	});

	it("http(s) only", () => {
		expect(issues({ urls: "https://vidyaclasses.in/fees\nftp://vidyaclasses.in/x" })).toEqual([
			"urls: Use an http:// or https:// address: ftp://vidyaclasses.in/x",
		]);
		expect(issues({ sitemap_url: "vidyaclasses.in/sitemap.xml" })[0]).toBe(
			"sitemap_url: Use an http:// or https:// address.",
		);
	});

	it("page limit 1 to 1000, whole numbers", () => {
		for (const bad of ["0", "1001", "2.5", "", "-3"]) {
			expect(issues({ urls: "https://a.in", max_pages: bad })).toEqual([
				"max_pages: Use a whole number from 1 to 1,000.",
			]);
		}
		expect(issues({ urls: "https://a.in", max_pages: "1000" })).toEqual([]);
		expect(issues({ urls: "https://a.in", max_pages: "1" })).toEqual([]);
	});

	it("a knowledge base is required; seeds are trimmed and de-duplicated", () => {
		expect(issues({ urls: "https://a.in", kb: "" })).toEqual(["kb: Choose the knowledge base the pages go into."]);
		expect(
			toScrapeRequest({ ...base, urls: " https://a.in/1 \n\nhttps://a.in/2\nhttps://a.in/1", max_pages: "7" }),
		).toEqual({
			urls: ["https://a.in/1", "https://a.in/2"],
			target_corpus_id: PATIENT,
			review_mode: true,
			max_pages: 7,
		});
	});
});

describe("crawler list", () => {
	it("lists the selected knowledge base's crawls with status, pages and actions", async () => {
		await openList();
		const table = await screen.findByRole("table", { name: "Crawls" });
		const rows = within(table).getAllByRole("row");
		expect(rows).toHaveLength(2);
		expect(within(table).getByRole("rowheader", { name: "shree-labs.in/faq" })).toBeInTheDocument();
		expect(within(table).getByText("Ready for review")).toBeInTheDocument();
		expect(within(table).getByText("5 / 5")).toBeInTheDocument();
		expect(within(table).getByRole("link", { name: "View crawl of shree-labs.in/faq" })).toHaveAttribute(
			"href",
			`/crawler/${JOB_FAQ}`,
		);
		expect(screen.getByText("Crawl a website, review the pages, then add them to Patient help.")).toBeInTheDocument();
		expect(document.title).toBe("Crawler · Sahayak AI");
	});

	it("'All knowledge bases' shows every crawl the caller may see, with its knowledge base", async () => {
		const app = await openList();
		await app.user.click(await screen.findByRole("switch", { name: "All knowledge bases" }));
		const table = await screen.findByRole("table", { name: "Crawls" });
		await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(4));
		expect(within(table).getByText("Staff SOPs")).toBeInTheDocument();
		expect(within(table).getByText("Crawl or delivery failed; see scraper logs")).toBeInTheDocument();
		expect(app.router.state.location.search).toMatchObject({ all: 1 });
	});

	it("empty knowledge base: explains the crawler and offers a new crawl", async () => {
		scraperState.jobs.delete(JOB_SOPS);
		await openList(`/crawler?kb=${STAFF}`);
		expect(await screen.findByRole("heading", { name: "Bring a website into Staff SOPs" })).toBeInTheDocument();
		expect(screen.getByText("Review mode on by default")).toBeInTheDocument();
		expect(screen.getByText("Private network addresses are refused")).toBeInTheDocument();
		expect(screen.getAllByRole("button", { name: "New crawl" }).length).toBeGreaterThan(0);
	});

	it("network error: names the crawler's address with the compose hint", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(http.get(`${SCRAPER}/api/v1/jobs`, () => HttpResponse.error()));
		renderApp(`/crawler?kb=${PATIENT}`);
		const alert = await screen.findByRole("alert");
		expect(alert).toHaveTextContent("Can't reach the crawler at http://127.0.0.1:8001.");
		expect(alert).toHaveTextContent("docker compose ps scraper");
	});

	it("delete asks first, then removes the crawl", async () => {
		const app = await openList();
		await app.user.click(await screen.findByRole("button", { name: "Delete crawl of shree-labs.in/faq" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Delete this crawl?" });
		expect(dialog).toHaveTextContent("Pages already delivered stay in the knowledge base.");
		await app.user.click(within(dialog).getByRole("button", { name: "Delete crawl" }));
		await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
		expect(scraperState.jobs.has(JOB_FAQ)).toBe(false);
		expect(await screen.findByRole("heading", { name: "Bring a website into Patient help" })).toBeInTheDocument();
	});
});

describe("new crawl", () => {
	it("a session sees review mode locked on with the reason, starts a crawl and lands on it", async () => {
		const app = await openList();
		await app.user.click(screen.getByRole("button", { name: "New crawl" }));
		const dialog = await screen.findByRole("dialog", { name: "New crawl" });
		const review = within(dialog).getByRole("switch", { name: "Review pages before they go in" });
		expect(review).toBeChecked();
		expect(review).toBeDisabled();
		expect(dialog).toHaveTextContent("Always on when you're signed in: pages are added when you press Deliver.");
		expect(dialog).toHaveTextContent("Private network addresses");
		expect(within(dialog).getByLabelText("Knowledge base")).toHaveValue(PATIENT);
		await app.user.type(within(dialog).getByLabelText("Page addresses"), "https://vidyaclasses.in/fees");
		await app.user.clear(within(dialog).getByLabelText("Page limit"));
		await app.user.type(within(dialog).getByLabelText("Page limit"), "20");
		await app.user.click(within(dialog).getByRole("button", { name: "Start crawl" }));
		await screen.findByRole("heading", { level: 1, name: "vidyaclasses.in/fees" });
		const post = scraperState.calls.find((c) => c.method === "POST" && c.path === "/api/v1/scrape");
		expect(post).toEqual({
			method: "POST",
			path: "/api/v1/scrape",
			auth: "bearer",
			body: { urls: ["https://vidyaclasses.in/fees"], target_corpus_id: PATIENT, review_mode: true, max_pages: 20 },
		});
		expect(app.pathname()).toMatch(/^\/crawler\/new/);
	});

	it("an API key may turn review mode off", async () => {
		tenantKey();
		const app = renderApp(`/crawler?kb=${PATIENT}`);
		await screen.findByRole("heading", { level: 1, name: "Crawler" });
		await app.user.click(await screen.findByRole("button", { name: "New crawl" }));
		const dialog = await screen.findByRole("dialog", { name: "New crawl" });
		const review = within(dialog).getByRole("switch", { name: "Review pages before they go in" });
		expect(review).toBeEnabled();
		await app.user.click(review);
		expect(review).not.toBeChecked();
		await app.user.type(within(dialog).getByLabelText("Sitemap URL"), "https://shree-labs.in/sitemap.xml");
		await app.user.click(within(dialog).getByRole("button", { name: "Start crawl" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		const post = scraperState.calls.find((c) => c.path === "/api/v1/scrape");
		expect(post).toMatchObject({
			auth: "api_key",
			body: { sitemap_url: "https://shree-labs.in/sitemap.xml", review_mode: false },
		});
	});

	it("a blocked private address shows the scraper's reason on the field", async () => {
		const app = await openList();
		await app.user.click(screen.getByRole("button", { name: "New crawl" }));
		const dialog = await screen.findByRole("dialog", { name: "New crawl" });
		await app.user.type(within(dialog).getByLabelText("Page addresses"), "http://10.1.2.3/intranet");
		await app.user.click(within(dialog).getByRole("button", { name: "Start crawl" }));
		const field = within(dialog).getByLabelText("Page addresses");
		await waitFor(() => expect(field).toHaveAttribute("aria-invalid", "true"));
		expect(dialog).toHaveTextContent(
			"http://10.1.2.3/intranet: private or internal address (10.0.0.0/8) is not allowed",
		);
	});

	it("a field's server error clears as soon as that field is edited, and a client error clears live", async () => {
		const app = await openList();
		await app.user.click(screen.getByRole("button", { name: "New crawl" }));
		const dialog = await screen.findByRole("dialog", { name: "New crawl" });
		const field = within(dialog).getByLabelText("Page addresses");
		await app.user.type(field, "http://10.1.2.3/intranet");
		await app.user.click(within(dialog).getByRole("button", { name: "Start crawl" }));
		await waitFor(() => expect(field).toHaveAttribute("aria-invalid", "true"));
		await app.user.type(field, "x");
		await waitFor(() => expect(field).not.toHaveAttribute("aria-invalid", "true"));
		expect(dialog).not.toHaveTextContent("private or internal address");
	});

	it("the review-mode rule from the scraper is shown if it ever answers it", async () => {
		server.use(
			http.post(`${SCRAPER}/api/v1/scrape`, () => HttpResponse.json({ detail: REVIEW_REQUIRED }, { status: 422 })),
		);
		const app = await openList();
		await app.user.click(screen.getByRole("button", { name: "New crawl" }));
		const dialog = await screen.findByRole("dialog", { name: "New crawl" });
		await app.user.type(within(dialog).getByLabelText("Page addresses"), "https://vidyaclasses.in");
		await app.user.click(within(dialog).getByRole("button", { name: "Start crawl" }));
		expect(await within(dialog).findByRole("alert")).toHaveTextContent(REVIEW_REQUIRED);
	});

	it("403 from the scraper: the knowledge base is not the caller's", async () => {
		server.use(
			http.post(`${SCRAPER}/api/v1/scrape`, () =>
				HttpResponse.json({ detail: "This key cannot use that corpus" }, { status: 403 }),
			),
		);
		const app = await openList();
		await app.user.click(screen.getByRole("button", { name: "New crawl" }));
		const dialog = await screen.findByRole("dialog", { name: "New crawl" });
		await app.user.type(within(dialog).getByLabelText("Page addresses"), "https://vidyaclasses.in");
		await app.user.click(within(dialog).getByRole("button", { name: "Start crawl" }));
		expect(await within(dialog).findByRole("alert")).toHaveTextContent("You can't add pages to that knowledge base.");
	});

	it("503: the crawler can't reach the engine", async () => {
		server.use(
			http.post(`${SCRAPER}/api/v1/scrape`, () =>
				HttpResponse.json({ detail: "RAG engine unreachable" }, { status: 503 }),
			),
		);
		const app = await openList();
		await app.user.click(screen.getByRole("button", { name: "New crawl" }));
		const dialog = await screen.findByRole("dialog", { name: "New crawl" });
		await app.user.type(within(dialog).getByLabelText("Page addresses"), "https://vidyaclasses.in");
		await app.user.click(within(dialog).getByRole("button", { name: "Start crawl" }));
		const alert = await within(dialog).findByRole("alert");
		expect(alert).toHaveTextContent("The crawler can't reach the engine right now.");
		expect(alert).toHaveTextContent("RAG engine unreachable");
	});

	it("client validation stops an empty form without calling the scraper", async () => {
		const app = await openList();
		await app.user.click(screen.getByRole("button", { name: "New crawl" }));
		const dialog = await screen.findByRole("dialog", { name: "New crawl" });
		await app.user.click(within(dialog).getByRole("button", { name: "Start crawl" }));
		expect(await within(dialog).findByText("Enter a sitemap URL or at least one page address.")).toBeInTheDocument();
		expect(scraperState.calls.some((c) => c.path === "/api/v1/scrape")).toBe(false);
	});
});

describe("crawl job", () => {
	it("summary, counts per status with text, and the pages", async () => {
		await openJob();
		const summary = screen.getByRole("region", { name: "Summary" });
		expect(within(summary).getByText("Ready for review")).toBeInTheDocument();
		expect(within(summary).getByRole("link", { name: "Patient help" })).toBeInTheDocument();
		expect(within(summary).getByText("5 of 5 done")).toBeInTheDocument();
		const chips = screen.getByRole("group", { name: "Filter pages by status" });
		expect(within(chips).getByRole("button", { name: "All 5" })).toHaveAttribute("aria-pressed", "true");
		expect(within(chips).getByRole("button", { name: "Verified 2" })).toBeInTheDocument();
		expect(within(chips).getByRole("button", { name: "Rejected 1" })).toBeInTheDocument();
		const table = screen.getByRole("table", { name: "Pages of this crawl" });
		expect(within(table).getByText("Too few words (12 < 50)")).toBeInTheDocument();
		expect(within(table).getByTestId("delivery-error")).toHaveTextContent(LIMIT_ERROR);
		expect(within(table).getAllByRole("checkbox")).toHaveLength(2);
		expect(screen.getByRole("button", { name: "Approve all verified (2)" })).toBeEnabled();
		expect(await screen.findByRole("button", { name: "Retry delivery (1)" })).toBeEnabled();
	});

	it("filter chips set ?status= and ask the scraper for that status", async () => {
		const app = await openJob();
		const chips = screen.getByRole("group", { name: "Filter pages by status" });
		await app.user.click(within(chips).getByRole("button", { name: "Rejected 1" }));
		await waitFor(() => expect(app.router.state.location.search).toMatchObject({ status: "rejected" }));
		const table = screen.getByRole("table", { name: "Pages of this crawl" });
		await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(2));
		expect(scraperState.calls.some((c) => c.path === `/api/v1/jobs/${JOB_FAQ}/pages?status=rejected`)).toBe(true);
	});

	it("approve selected sends page_ids of the checked verified pages", async () => {
		const app = await openJob();
		const approveSelected = screen.getByRole("button", { name: "Approve selected (0)" });
		expect(approveSelected).toBeDisabled();
		await app.user.click(screen.getByRole("checkbox", { name: "Select Home collection | Shree Labs" }));
		await app.user.click(screen.getByRole("button", { name: "Approve selected (1)" }));
		await waitFor(() =>
			expect(scraperState.calls.find((c) => c.path.endsWith("/approve"))?.body).toEqual({ page_ids: [PAGE_HOME] }),
		);
		expect(await screen.findByText("1 page approved")).toBeInTheDocument();
		expect(scraperState.pages.find((p) => p.id === PAGE_HOME)?.status).toBe("approved");
		expect(scraperState.pages.find((p) => p.id === PAGE_PRICES)?.status).toBe("verified");
	});

	it("select all verified, then approve all verified", async () => {
		const app = await openJob();
		await app.user.click(screen.getByRole("checkbox", { name: "Select all verified pages shown" }));
		expect(screen.getByRole("button", { name: "Approve selected (2)" })).toBeEnabled();
		await app.user.click(screen.getByRole("button", { name: "Approve all verified (2)" }));
		await waitFor(() =>
			expect(scraperState.calls.find((c) => c.path.endsWith("/approve"))?.body).toEqual({ approve_all_verified: true }),
		);
		expect(await screen.findByRole("button", { name: "Deliver 3 approved pages" })).toBeEnabled();
	});

	it("deliver: summary with the failed page, whose error stays on its row with Retry", async () => {
		const app = await openJob();
		await app.user.click(screen.getByRole("button", { name: "Approve all verified (2)" }));
		await app.user.click(await screen.findByRole("button", { name: "Deliver 3 approved pages" }));
		const result = await screen.findByTestId("deliver-result");
		expect(result).toHaveTextContent("Delivered 2 of 3 pages.");
		expect(result).toHaveTextContent("1 failed and stay approved with their error.");
		expect(await screen.findByRole("button", { name: "Retry delivery (1)" })).toBeEnabled();
		const table = screen.getByRole("table", { name: "Pages of this crawl" });
		await waitFor(() => expect(within(table).getAllByText("Delivered")).toHaveLength(3));
		expect(within(table).getByTestId("delivery-error")).toHaveTextContent(LIMIT_ERROR);
		// Room made on the plan: the retry sends only what is still approved.
		scraperState.failing.clear();
		await app.user.click(screen.getByRole("button", { name: "Retry delivery (1)" }));
		expect(await screen.findByTestId("deliver-result")).toHaveTextContent("Delivered 1 of 1 pages.");
		expect(screen.getByTestId("deliver-result")).toHaveTextContent("Every approved page is in Patient help.");
		expect(scraperState.pages.find((p) => p.id === PAGE_TIMINGS)?.status).toBe("delivered");
	});

	it("the page drawer is a dialog named by the title; Markdown is text in a focusable region", async () => {
		const app = await openJob();
		await app.user.click(screen.getByRole("button", { name: "Open Home collection | Shree Labs" }));
		const drawer = await screen.findByRole("dialog", { name: "Home collection | Shree Labs" });
		const region = await within(drawer).findByRole("region", {
			name: "Extracted text of Home collection | Shree Labs",
		});
		expect(region).toHaveAttribute("tabindex", "0");
		expect(region.querySelector("pre")?.textContent).toBe(MARKDOWN_HOME);
		expect(region.querySelector("script")).toBeNull();
		expect(app.router.state.location.search).toMatchObject({ pageId: PAGE_HOME });
		expect(within(drawer).getByRole("link", { name: "https://shree-labs.in/home-collection" })).toHaveAttribute(
			"rel",
			"noopener noreferrer",
		);
	});

	it("a rejected page shows why", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp(`/crawler/${JOB_FAQ}?pageId=${PAGE_CAREERS}`);
		const drawer = await screen.findByRole("dialog", { name: "Careers" });
		expect(within(drawer).getByText("Too few words (12 < 50)")).toBeInTheDocument();
		expect(await within(drawer).findByText("No extracted text is saved for this page.")).toBeInTheDocument();
	});

	it("unknown job: Crawl not found with a way back", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/crawler/nosuchjob123");
		expect(await screen.findByRole("heading", { name: "Crawl not found" })).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Back to the crawler" })).toBeInTheDocument();
	});

	it("an old crawl without a target knowledge base can't be delivered", async () => {
		const m = scraperState.jobs.get(JOB_FAQ);
		if (m) m.job.target_corpus_id = "";
		await openJob();
		expect(screen.getByText(/started before crawls had a target knowledge base/)).toBeInTheDocument();
		expect(await screen.findByRole("button", { name: "Retry delivery (1)" })).toBeDisabled();
	});

	it("409 on deliver shows the scraper's reason", async () => {
		server.use(
			http.post(`${SCRAPER}/api/v1/jobs/:id/deliver`, () =>
				HttpResponse.json(
					{ detail: "Job has no target corpus (created before corpus targeting); re-submit it" },
					{ status: 409 },
				),
			),
		);
		const app = await openJob();
		await app.user.click(await screen.findByRole("button", { name: "Retry delivery (1)" }));
		expect(await screen.findByText(/This crawl has no target knowledge base/)).toBeInTheDocument();
	});

	it("a crawl in progress polls until it is ready for review and its pages appear", async () => {
		const m = scraperState.jobs.get(JOB_VIDYA);
		if (m) {
			m.job.status = "pending";
			m.job.error = null;
			m.job.owner_tenant_id = SHREE.id;
			m.job.target_corpus_id = PATIENT;
			m.script = ["crawling", "verifying"];
		}
		signedInAs(PRIYA, SHREE);
		renderApp(`/crawler/${JOB_VIDYA}`);
		await screen.findByRole("heading", { level: 1, name: "vidyaclasses.in" });
		expect(screen.getByText("Crawling vidyaclasses.in… Pages appear here as they are read.")).toBeInTheDocument();
		expect(await screen.findByText("Fees 2026-27 | Vidya Classes", {}, { timeout: 9000 })).toBeInTheDocument();
		expect((await screen.findAllByText("Ready for review", {}, { timeout: 9000 })).length).toBeGreaterThan(0);
	}, 15_000);

	it("members can crawl, approve and deliver (not owner-only)", async () => {
		const app = await openJob(JOB_FAQ, "", [ROHAN, MEMBER_SHREE]);
		expect(screen.getByRole("button", { name: "Approve all verified (2)" })).toBeEnabled();
		await app.user.click(screen.getByRole("button", { name: "Approve all verified (2)" }));
		expect(await screen.findByRole("button", { name: "Deliver 3 approved pages" })).toBeEnabled();
	});

	it("the super admin sees every crawl", async () => {
		const app = await openList(`/crawler?kb=${PATIENT}`, [SV, null]);
		await app.user.click(await screen.findByRole("switch", { name: "All knowledge bases" }));
		const table = await screen.findByRole("table", { name: "Crawls" });
		await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(4));
		expect(within(table).getByText("Admissions help")).toBeInTheDocument();
	});
});
