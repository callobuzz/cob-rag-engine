import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { setCurrency } from "@/lib/format/currency";
import { BASE, CORPORA, PRIYA, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { server } from "../msw/server";
import { renderApp } from "../render";

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

/** Every GET /usage the page sends (query string only). */
function captureUsage(): URLSearchParams[] {
	const seen: URLSearchParams[] = [];
	server.events.on("request:start", ({ request }) => {
		const u = new URL(request.url);
		if (u.pathname === "/api/v1/usage") seen.push(u.searchParams);
	});
	return seen;
}

let saved: { text: Promise<string>; name: string }[] = [];
beforeEach(() => {
	saved = [];
	URL.createObjectURL = vi.fn((b: Blob) => {
		saved.push({
			text: b.arrayBuffer().then((a) => new TextDecoder("utf-8", { ignoreBOM: true }).decode(a)),
			name: "",
		});
		return "blob:usage";
	});
	URL.revokeObjectURL = vi.fn();
	vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
		const last = saved.at(-1);
		if (last) last.name = this.download;
	});
});
afterEach(() => {
	server.events.removeAllListeners();
	setCurrency("INR");
});

const PATIENT = CORPORA[0]?.id ?? "";

describe("usage", () => {
	it("tenant: summary, per-day chart and the table by knowledge base with a totals row", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/usage");
		expect(await screen.findByRole("heading", { level: 1, name: "Usage" })).toBeInTheDocument();
		const summary = await screen.findByTestId("usage-summary");
		// The MSW fixture answers every corpus (the server scopes; the fixture doesn't): 2,846 in all.
		await waitFor(() => expect(summary).toHaveTextContent("2,846"));
		// A customer's cost is their own spend (their own LLM key), not what the operator's key paid.
		expect(summary).toHaveTextContent("Your AI spend");
		expect(await screen.findByRole("img", { name: /^Questions per day,/ })).toBeInTheDocument();
		const table = await screen.findByRole("table", { name: "Usage by knowledge base" });
		const rows = within(table).getAllByRole("row");
		expect(rows[1]).toHaveTextContent(/^Patient help.*Questions:1,812.*19,93,200/);
		const total = rows.at(-1) as HTMLElement;
		expect(within(total).getByRole("rowheader")).toHaveTextContent("Total");
		expect(total).toHaveTextContent("2,846");
		expect(total).toHaveTextContent("100%");
		// No customer filter or grouping for a tenant.
		expect(screen.queryByLabelText("Customer")).toBeNull();
		expect(
			within(screen.getByRole("radiogroup", { name: "Group by" })).queryByRole("radio", { name: "Customer" }),
		).toBeNull();
	});

	it("filters live in the URL and scope the calls; CSV totals match the summary", async () => {
		signedInAs(PRIYA, SHREE);
		const seen = captureUsage();
		const app = renderApp("/usage");
		await screen.findByRole("table", { name: "Usage by knowledge base" });
		await app.user.selectOptions(screen.getByLabelText("Knowledge base"), "Patient help");
		await waitFor(() => expect(app.router.state.location.search).toMatchObject({ kb: PATIENT }));
		await waitFor(() =>
			expect(seen.some((s) => s.get("corpus_id") === PATIENT && s.get("group_by") === "day")).toBe(true),
		);
		const table = await screen.findByRole("table", { name: "Usage by knowledge base" });
		await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(3));
		const summary = screen.getByTestId("usage-summary");
		await waitFor(() => expect(summary).toHaveTextContent("1,812"));

		await app.user.click(screen.getByRole("button", { name: "Export CSV" }));
		expect(saved).toHaveLength(1);
		const csv = await saved[0]?.text;
		expect(saved[0]?.name).toMatch(/^usage-\d{4}-\d{2}-\d{2}-\d{4}-\d{2}-\d{2}-corpus\.csv$/);
		expect(csv?.charCodeAt(0)).toBe(0xfeff);
		const lines = (csv ?? "").slice(1).trimEnd().split("\r\n");
		expect(lines[0]).toBe("key,label,requests,prompt_tokens,completion_tokens,cost_usd,cost_inr,unpriced_requests");
		expect(lines[1]).toBe(`${PATIENT},Patient help,1812,1993200,471120,0.7248,61.61,0`);
		expect(lines.at(-1)).toBe(",Total,1812,1993200,471120,0.7248,61.61,0");
	});

	it("the ₹ / $ toggle changes every cost and the CSV column", async () => {
		signedInAs(PRIYA, SHREE);
		const app = renderApp(`/usage?group=corpus&kb=${PATIENT}`);
		const table = await screen.findByRole("table", { name: "Usage by knowledge base" });
		await waitFor(() => expect(within(table).getAllByRole("row")[1]).toHaveTextContent("₹61.61"));
		await app.user.click(
			within(screen.getByRole("radiogroup", { name: "Currency" })).getByRole("radio", { name: "$ USD" }),
		);
		await waitFor(() => expect(within(table).getAllByRole("row")[1]).toHaveTextContent("$0.7248"));
		expect(localStorage.getItem("cob.currency")).toBe("USD");
		await app.user.click(screen.getByRole("button", { name: "Export CSV" }));
		expect((await saved[0]?.text)?.split("\r\n")[0]).toContain(",cost_usd,cost_usd");
	});

	it("by source: widget keys by name, an unknown key by its id, no invented agents row", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/usage?group=source");
		const table = await screen.findByRole("table", { name: "Usage by source" });
		expect(await within(table).findByText("Widget · Patient help · shree-labs.in")).toBeInTheDocument();
		expect(within(table).getByText("Widget deadbeef")).toBeInTheDocument();
		expect(within(table).getByText("Your apps (API)")).toBeInTheDocument();
		expect(within(table).getByText("Your apps (streaming)")).toBeInTheDocument();
		expect(within(table).getByText("Conversations")).toBeInTheDocument();
		expect(within(table).queryByText(/Agents/)).toBeNull();
	});

	it("admin: customer filter and grouping by customer", async () => {
		signedInAs(SV, null);
		const seen = captureUsage();
		const app = renderApp("/usage?group=tenant");
		const table = await screen.findByRole("table", { name: "Usage by customer" });
		expect(await within(table).findByText("Shree Labs")).toBeInTheDocument();
		await app.user.selectOptions(screen.getByLabelText("Customer"), "Shree Labs");
		await waitFor(() => expect(seen.some((s) => s.get("tenant_id") === SHREE.id)).toBe(true));
		// The knowledge-base list follows the chosen customer.
		const kb = screen.getByLabelText("Knowledge base");
		expect(within(kb).queryByRole("option", { name: "Store assistant" })).toBeNull();
		expect(within(kb).getByRole("option", { name: "Patient help" })).toBeInTheDocument();
	});

	it("a custom range over 366 days is refused before any call; the engine's 422 is shown", async () => {
		signedInAs(PRIYA, SHREE);
		const seen = captureUsage();
		renderApp("/usage?range=custom&from=2025-01-01&to=2026-09-30");
		expect(await screen.findByRole("alert")).toHaveTextContent("Pick at most 366 days.");
		expect(screen.getByLabelText("From")).toHaveAttribute("aria-invalid", "true");
		expect(seen).toHaveLength(0);
	});

	it("422 from the engine and an empty period", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.get(`${BASE}/api/v1/usage`, () =>
				HttpResponse.json({ detail: "Range is limited to one year" }, { status: 422 }),
			),
		);
		const first = renderApp("/usage");
		expect(await screen.findByText("Range is limited to one year")).toBeInTheDocument();
		first.unmount();
		server.use(
			http.get(`${BASE}/api/v1/usage`, () =>
				HttpResponse.json({
					start: "2026-09-01",
					end: "2026-09-30",
					group_by: "corpus",
					rows: [],
					totals: { key: null, requests: 0, prompt_tokens: 0, completion_tokens: 0, cost_usd: 0 },
				}),
			),
		);
		renderApp("/usage");
		expect(
			await screen.findByRole("heading", { name: "No answered questions in this period yet" }),
		).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Open the playground" })).toHaveAttribute("href", "/playground");
		expect(screen.getByRole("button", { name: "Export CSV" })).toBeDisabled();
	});
});
