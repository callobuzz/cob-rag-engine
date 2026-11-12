import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, rt, SHREE, tokenResponse } from "@/test/msw/data";
import { OUTCOMES } from "@/test/msw/handlers/outcomes";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";

const KB = CORPORA[0]?.id ?? "";

function open(path: string) {
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
	return renderApp(path);
}

describe("insights outcomes", () => {
	it("is the default tab and shows the outcome numbers in words", async () => {
		open(`/insights?kb=${KB}`);
		expect(await screen.findByRole("tab", { name: "Outcomes", selected: true })).toBeInTheDocument();
		const kpis = await screen.findByTestId("outcome-kpis");
		expect(within(kpis).getByText("75%")).toBeInTheDocument();
		expect(within(kpis).getByText(/309 of 412 conversations were resolved without a person/)).toBeInTheDocument();
		expect(within(kpis).getByText(/37 conversations asked for a person/)).toBeInTheDocument();
		expect(within(kpis).getByText("58")).toBeInTheDocument();
	});

	it("shows handoff reasons in words, not as engine keys", async () => {
		const { user } = open(`/insights?kb=${KB}&tab=outcomes`);
		await screen.findByTestId("outcome-kpis");
		const toggles = await screen.findAllByRole("button", { name: "Show as table" });
		for (const toggle of toggles) await user.click(toggle);
		expect(screen.getAllByText("Asked for a person").length).toBeGreaterThan(0);
		expect(screen.queryByText("explicit_request")).not.toBeInTheDocument();
	});

	it("links the most asked unanswered questions to the review inbox", async () => {
		open(`/insights?kb=${KB}&tab=outcomes`);
		const link = await screen.findByRole("link", { name: "do you do mri scans" });
		expect(link.getAttribute("href")).toContain("/review?");
		expect(link.getAttribute("href")).toContain("kind=no_answer");
	});

	it("offers every chart as a table", async () => {
		const { user } = open(`/insights?kb=${KB}&tab=outcomes`);
		await screen.findByTestId("outcome-kpis");
		const toggles = await screen.findAllByRole("button", { name: "Show as table" });
		await user.click(toggles[0] as HTMLElement);
		expect(screen.getByRole("columnheader", { name: "Language" })).toBeInTheDocument();
		expect(screen.getByRole("rowheader", { name: "hi-Latn" })).toBeInTheDocument();
	});

	it("uses the scope route for all knowledge bases and does not link to a review inbox", async () => {
		open(`/insights?kb=${KB}&tab=outcomes&scope=all`);
		expect(await screen.findByText(/of 980 conversations/)).toBeInTheDocument();
		expect(screen.queryByRole("link", { name: "do you do mri scans" })).not.toBeInTheDocument();
	});

	it("shows a dash and the engine's note for figures that are not available yet", async () => {
		server.use(
			http.get(`${BASE}/api/v1/corpora/:kb/assistant/analytics`, () =>
				HttpResponse.json({
					...OUTCOMES,
					handoffs: null,
					handoff_rate: null,
					handoff_reasons: [],
					leads: null,
					lead_conversion_rate: null,
					leads_by_status: [],
					leads_by_page: [],
					notes: ["Handoff figures appear once talk-to-a-person is available."],
				}),
			),
		);
		open(`/insights?kb=${KB}&tab=outcomes`);
		const kpis = await screen.findByTestId("outcome-kpis");
		expect(within(kpis).getAllByText("Not available yet")).toHaveLength(2);
		expect(screen.getByText("Handoff figures appear once talk-to-a-person is available.")).toBeInTheDocument();
	});

	it("says so when there were no conversations", async () => {
		server.use(
			http.get(`${BASE}/api/v1/corpora/:kb/assistant/analytics`, () =>
				HttpResponse.json({
					...OUTCOMES,
					conversations: 0,
					turns: 0,
					resolution_rate: null,
					leads: 0,
					cost_usd: 0,
				}),
			),
		);
		open(`/insights?kb=${KB}&tab=outcomes`);
		expect(await screen.findByText("No conversations yet in this period.")).toBeInTheDocument();
	});

	it("still shows leads and spend for a period with no conversations", async () => {
		server.use(
			http.get(`${BASE}/api/v1/corpora/:kb/assistant/analytics`, () =>
				HttpResponse.json({ ...OUTCOMES, conversations: 0, turns: 0, resolution_rate: null }),
			),
		);
		open(`/insights?kb=${KB}&tab=outcomes`);
		const kpis = await screen.findByTestId("outcome-kpis");
		expect(within(kpis).getByText("58")).toBeInTheDocument();
		expect(screen.queryByText("No conversations yet in this period.")).not.toBeInTheDocument();
	});

	it("sends an explicit start for All time, because the engine defaults to 30 days", async () => {
		const froms: (string | null)[] = [];
		server.use(
			http.get(`${BASE}/api/v1/corpora/:kb/assistant/analytics`, ({ request }) => {
				froms.push(new URL(request.url).searchParams.get("from"));
				return HttpResponse.json(OUTCOMES);
			}),
		);
		const { user } = open(`/insights?kb=${KB}&tab=outcomes`);
		await screen.findByTestId("outcome-kpis");
		await user.click(screen.getByRole("radio", { name: "All time" }));
		await waitFor(() => expect(froms.at(-1)).toBe("2000-01-01T00:00:00.000Z"));
		expect(froms[0]).not.toBe(froms.at(-1));
	});
});
