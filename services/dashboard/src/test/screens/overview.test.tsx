import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, type PRIYA, rt, SHREE, SV, tokenResponse } from "../msw/data";
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

const emptyCorpora = () =>
	http.get(`${BASE}/api/v1/corpora`, () => HttpResponse.json({ items: [], count: 0, total: 0, offset: 0, limit: 500 }));

const card = (name: string) => screen.findByRole("region", { name });

describe("overview: super admin", () => {
	it("greets, shows KPIs, questions per day with source totals, sign-ups, health and top customers", async () => {
		signedInAs(SV, null);
		const app = renderApp("/");
		expect(await screen.findByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
		expect(screen.getByTestId("greeting")).toHaveTextContent(/^Good (morning|afternoon|evening), SV$/);
		await waitFor(() =>
			expect(screen.getByTestId("overview-sub")).toHaveTextContent(
				"3 customers · 4 knowledge bases · 2 sign-ups waiting for you.",
			),
		);

		// KPIs: 1,820 questions (source totals), confidence 93 % (count-weighted), 4 × 12 documents.
		await waitFor(() => expect(screen.getByTestId("kpi-questions")).toHaveTextContent("1,820"));
		expect(screen.getByTestId("kpi-questions")).toHaveTextContent("no change vs previous 7 days");
		await waitFor(() => expect(screen.getByTestId("kpi-confidence")).toHaveTextContent("93% avg"));
		expect(screen.getByTestId("kpi-tokens")).toHaveTextContent("≈ ₹");
		expect(screen.getByTestId("kpi-tokens")).toHaveTextContent("estimate, your prices apply");
		await waitFor(() => expect(screen.getByTestId("kpi-documents")).toHaveTextContent("48"));

		const chart = await card("Questions per day");
		expect(
			await within(chart).findByRole("img", { name: /^Questions per day, in the last 7 days: highest 260 on/ }),
		).toBeInTheDocument();
		const sources = within(chart).getByRole("list", { name: "Questions by source" });
		expect(within(sources).getByText("Website widget").closest("li")).toHaveTextContent("1,620");
		expect(within(sources).getByText("Website widget").closest("li")).toHaveTextContent("89% of questions");
		expect(within(sources).queryByText(/Agents/)).toBeNull();

		// "Show as table" renders the same numbers.
		await app.user.click(within(chart).getByRole("button", { name: "Show as table" }));
		const table = within(chart).getByRole("table");
		expect(within(table).getAllByRole("row").length).toBeGreaterThanOrEqual(8);
		expect(within(chart).getByRole("button", { name: "Show as chart" })).toHaveAttribute("aria-pressed", "true");

		const pending = await card("2 sign-ups waiting for activation");
		expect(within(pending).getByText("Anand Dental Care")).toBeInTheDocument();
		expect(within(pending).getByText(/dr.anand@anandental.in/)).toBeInTheDocument();
		expect(within(pending).getByRole("link", { name: "Review and activate" })).toHaveAttribute(
			"href",
			"/admin/customers?status=pending",
		);

		const health = await card("Service health");
		await waitFor(() => expect(within(health).getByText("All good")).toBeInTheDocument());
		expect(within(health).getByTestId("service-postgres")).toHaveTextContent("PostgreSQLOK");
		expect(within(health).getAllByText("OK")).toHaveLength(5);

		const top = await card("Top customers");
		await waitFor(() => expect(within(top).getAllByTestId("top-customer")).toHaveLength(2));
		expect(within(top).getAllByTestId("top-customer")[0]).toHaveTextContent("Shree LabsPro · 2 knowledge bases1,904");

		const gaps = await card("Knowledge gaps");
		const rows = await within(gaps).findAllByTestId("gap-row");
		expect(rows[0]).toHaveTextContent("Home collection in Baner on Sunday?");
		expect(rows[0]).toHaveTextContent("Patient help · asked 7 times");
		expect(within(rows[0] as HTMLElement).getByRole("link", { name: /^Add content for/ })).toHaveAttribute(
			"href",
			`/add?kb=${CORPORA[0]?.id}`,
		);

		expect(screen.queryByRole("region", { name: "Setup checklist" })).toBeNull();
		expect(screen.getByTestId("powered-by")).toBeInTheDocument();
	});

	it("the range segment is a radio group stored in the URL", async () => {
		signedInAs(SV, null);
		const app = renderApp("/");
		const group = await screen.findByRole("radiogroup", { name: "Range" });
		expect(within(group).getByRole("radio", { name: "7 days" })).toHaveAttribute("aria-checked", "true");
		await app.user.click(within(group).getByRole("radio", { name: "30 days" }));
		await waitFor(() => expect(app.router.state.location.search).toEqual({ range: "30d" }));
		expect(await screen.findByTestId("kpi-questions")).toHaveTextContent("vs previous 30 days");
	});

	it("a health failure stays in its card; an engine with nothing yet shows the first-run state", async () => {
		signedInAs(SV, null);
		server.use(
			http.get(`${BASE}/api/v1/health`, () => HttpResponse.error()),
			emptyCorpora(),
			http.get(`${BASE}/api/v1/admin/tenants`, ({ request }) => {
				const status = new URL(request.url).searchParams.get("status");
				return HttpResponse.json({ items: [], count: 0, total: status === "pending" ? 0 : 0, offset: 0, limit: 500 });
			}),
		);
		renderApp("/");
		expect(await screen.findByRole("heading", { name: "Add your first customer" })).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Add a customer" })).toHaveAttribute("href", "/admin/customers?add=1");
		const health = await card("Service health");
		expect(await within(health).findAllByText("Couldn't check")).not.toHaveLength(0);
		expect(within(await card("Sign-ups waiting")).getByText("No sign-ups are waiting.")).toBeInTheDocument();
	});
});

// BIZ3: a business lands on Home at /, not on the operator Overview. Its tests are in home.test.tsx.
