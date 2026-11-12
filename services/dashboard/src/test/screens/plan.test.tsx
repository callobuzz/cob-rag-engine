import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { upgradeMailto } from "@/features/plan/NeedMoreCard";
import { questionsHint } from "@/features/plan/PlanPage";
import type { PlanUsageResponse } from "@/lib/api/accounts-types";
import { RT_KEY } from "@/lib/auth/storage";
import { formatDate } from "@/lib/format/date";
import { BASE, CORPORA, PRIYA, ROHAN, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { PLAN_ME } from "../msw/handlers/tenants";
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

const withUsage = (usage: Partial<PlanUsageResponse["usage"]>, limits: Partial<PlanUsageResponse["limits"]> = {}) =>
	http.get(`${BASE}/api/v1/tenants/me/plan`, () =>
		HttpResponse.json({
			...PLAN_ME,
			usage: { ...PLAN_ME.usage, ...usage },
			limits: { ...PLAN_ME.limits, ...limits },
		}),
	);

describe("plan & usage", () => {
	it("owner: hero, four meters from GET /tenants/me/plan, reset date, contact card and this month by KB", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/plan");
		expect(await screen.findByRole("heading", { level: 1, name: "Plan & billing" })).toBeInTheDocument();
		expect(await screen.findByTestId("plan-name")).toHaveTextContent("Pro");
		const meters = screen.getAllByRole("meter");
		expect(meters.map((m) => m.getAttribute("aria-label"))).toEqual([
			"Questions this month",
			"Knowledge bases",
			"Documents",
			"Website widget keys",
		]);
		expect(meters.map((m) => m.getAttribute("aria-valuetext"))).toEqual([
			"1,904 of 5,000",
			"2 of 3",
			"139 of 500",
			"1 of 3",
		]);
		const panel = screen.getByTestId("plan-meters");
		expect(panel).toHaveTextContent(`38% used · resets on ${formatDate(PLAN_ME.resets_at)}`);
		expect(panel).toHaveTextContent("search-only calls don't");
		expect(panel).toHaveTextContent("Patient help, Staff SOPs");
		expect(panel).toHaveTextContent("Active documents across all knowledge bases");
		const sizes = screen.getByTestId("plan-size-limits");
		expect(sizes).toHaveTextContent("Largest upload");
		expect(sizes).toHaveTextContent("Text per document");
		expect(sizes).toHaveTextContent("Engine default");
		await waitFor(() => expect(screen.getByText(/^Active since .* managed by Sahayak AI/)).toBeInTheDocument());

		const more = screen.getByRole("region", { name: "Need more?" });
		await waitFor(() =>
			expect(within(more).getByRole("link", { name: "Email about upgrading" })).toHaveAttribute(
				"href",
				"mailto:support@sahayak.ai?subject=Upgrade%20Shree%20Labs",
			),
		);
		expect(within(more).getByRole("button", { name: "Copy email address" })).toBeInTheDocument();
		expect(within(more).getByText(/reaches 5,000 questions/)).toBeInTheDocument();

		const byKb = screen.getByRole("region", { name: "This month by knowledge base" });
		const rows = await within(byKb).findAllByTestId("month-kb");
		expect(rows[0]).toHaveTextContent("Patient help");
		expect(rows[0]).toHaveTextContent("1,812");
		await waitFor(() => expect(rows[0]).toHaveTextContent("website widget 89%"));
	});

	it("meters: amber from 80 %, rose with 'Limit reached' at 100 %, unlimited has no meter", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(withUsage({ questions: 5000, documents: 420 }, { max_widget_keys: null }));
		renderApp("/plan");
		const questions = await screen.findByRole("meter", { name: "Questions this month" });
		expect(questions).toHaveAttribute("aria-valuetext", "5,000 of 5,000 · limit reached");
		expect(screen.getByRole("meter", { name: "Documents" })).toHaveAttribute(
			"aria-valuetext",
			"420 of 500 · nearly full",
		);
		expect(screen.queryByRole("meter", { name: "Website widget keys" })).toBeNull();
		expect(screen.getByTestId("plan-meters")).toHaveTextContent("1 · unlimited");
		expect(screen.getByTestId("plan-meters")).toHaveTextContent("Limit reached");
	});

	it("members see the same page", async () => {
		signedInAs(ROHAN, { ...SHREE, role: "member" });
		renderApp("/plan");
		expect(await screen.findByTestId("plan-name")).toHaveTextContent("Pro");
		expect(screen.getAllByRole("meter")).toHaveLength(4);
	});

	it("the super admin gets 'Not available'", async () => {
		signedInAs(SV, null);
		renderApp("/plan");
		expect(await screen.findByRole("heading", { name: "Not available for your account" })).toBeInTheDocument();
	});

	it("plan error stays in its card", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(http.get(`${BASE}/api/v1/tenants/me/plan`, () => HttpResponse.json({}, { status: 500 })));
		renderApp("/plan");
		expect(await screen.findByText("Couldn't load your plan")).toBeInTheDocument();
		expect(screen.getByRole("region", { name: "Need more?" })).toBeInTheDocument();
	});
});

describe("plan helpers", () => {
	it("reset hint in local format; mailto subject encoded", () => {
		const hint = questionsHint({ ...PLAN_ME, limits: { ...PLAN_ME.limits, monthly_questions: null } });
		expect(hint).toBe(
			`Resets on ${formatDate("2026-10-01T00:00:00Z")} · website widget, API, conversations and agents all count`,
		);
		expect(upgradeMailto("support@sahayak.ai", "Rangoli Sarees & Co")).toBe(
			"mailto:support@sahayak.ai?subject=Upgrade%20Rangoli%20Sarees%20%26%20Co",
		);
	});
});
