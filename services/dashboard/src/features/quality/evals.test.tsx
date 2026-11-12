import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, ROHAN, rt, SHREE, tokenResponse } from "@/test/msw/data";
import { createEvalsMock, RUN_A, RUN_B } from "@/test/msw/handlers/evals";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";

const KB = CORPORA[0]?.id ?? "";

function open(path: string, who: "owner" | "member" = "owner", mockOpts: { publishRun?: boolean } = {}) {
	const user = who === "member" ? ROHAN : PRIYA;
	const tenant = who === "member" ? { ...SHREE, role: "member" as const } : SHREE;
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
	const mock = createEvalsMock(mockOpts);
	server.use(...mock.handlers);
	return { ...renderApp(path), mock };
}

describe("quality: golden sets", () => {
	it("lists the sets with their size and last result, and adds the template's tests", async () => {
		const { user, mock } = open(`/quality?kb=${KB}&tab=golden`);
		const sets = await screen.findByRole("table", { name: "Golden sets" });
		expect(within(sets).getByText("Patient help basics")).toBeInTheDocument();
		expect(within(sets).getByText("2 questions · 2 with expected behaviour")).toBeInTheDocument();
		expect(within(sets).getByText("Not run yet")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Add the template's tests" }));
		await waitFor(() => expect(mock.log.some((l) => l.path === "from-template")).toBe(true));
	});

	it("the default set cannot be deleted from the list", async () => {
		open(`/quality?kb=${KB}&tab=golden`);
		const sets = await screen.findByRole("table", { name: "Golden sets" });
		expect(within(sets).queryByRole("button", { name: /Delete golden set/ })).not.toBeInTheDocument();
	});
});

describe("quality: runs", () => {
	it("estimates, requires the cost confirmation, then starts a named full run", async () => {
		const { user, mock } = open(`/quality?kb=${KB}&tab=runs`);
		await user.click(await screen.findByRole("button", { name: "New run" }));
		const dlg = await screen.findByRole("dialog", { name: "New run" });
		await user.click(within(dlg).getByRole("radio", { name: /full run/i }));
		await user.type(within(dlg).getByLabelText("Name (optional)"), "rerank on");
		await user.click(within(dlg).getByRole("button", { name: "Estimate cost" }));
		expect(await within(dlg).findByText(/for 2 questions/)).toBeInTheDocument();
		const start = within(dlg).getByRole("button", { name: "Start run" });
		expect(start).toHaveAttribute("aria-disabled", "true");
		await user.click(start);
		expect(await within(dlg).findByText("Tick the box to confirm the cost")).toBeInTheDocument();
		expect(mock.log.some((l) => l.path === "runs")).toBe(false);
		await user.click(within(dlg).getByRole("checkbox", { name: /I understand this run costs about/ }));
		await waitFor(() => expect(within(dlg).queryByText("Tick the box to confirm the cost")).not.toBeInTheDocument());
		await user.click(within(dlg).getByRole("button", { name: "Start run" }));
		await waitFor(() =>
			expect(mock.log.find((l) => l.path === "runs")?.body).toMatchObject({
				mode: "full",
				name: "rerank on",
				confirm_cost_usd: 0.11,
			}),
		);
	});

	it("changing the preset drops the estimate and its tick", async () => {
		const { user } = open(`/quality?kb=${KB}&tab=runs`);
		await user.click(await screen.findByRole("button", { name: "New run" }));
		const dlg = await screen.findByRole("dialog", { name: "New run" });
		await user.click(within(dlg).getByRole("radio", { name: /full run/i }));
		await user.click(within(dlg).getByRole("button", { name: "Estimate cost" }));
		await within(dlg).findByText(/for 2 questions/);
		await user.click(within(dlg).getByRole("checkbox", { name: /I understand this run costs about/ }));
		await user.selectOptions(within(dlg).getByLabelText("Search preset"), "premium");
		expect(within(dlg).queryByRole("checkbox")).not.toBeInTheDocument();
		await user.click(within(dlg).getByRole("button", { name: "Estimate cost" }));
		await within(dlg).findByText(/for 2 questions/);
		expect(within(dlg).getByRole("checkbox", { name: /I understand this run costs about/ })).not.toBeChecked();
		expect(within(dlg).getByRole("button", { name: "Start run" })).toHaveAttribute("aria-disabled", "true");
	});

	it("a quick check needs no estimate", async () => {
		const { user, mock } = open(`/quality?kb=${KB}&tab=runs`);
		await user.click(await screen.findByRole("button", { name: "New run" }));
		const dlg = await screen.findByRole("dialog", { name: "New run" });
		expect(within(dlg).queryByRole("button", { name: "Estimate cost" })).not.toBeInTheDocument();
		await user.click(within(dlg).getByRole("button", { name: "Start run" }));
		await waitFor(() => expect(mock.log.find((l) => l.path === "runs")?.body).toMatchObject({ mode: "fast" }));
		expect(mock.log.some((l) => l.path === "estimate")).toBe(false);
	});

	it("lists runs with their result and real cost", async () => {
		open(`/quality?kb=${KB}&tab=runs`);
		const table = await screen.findByRole("table", { name: "Runs" });
		expect(within(table).getByRole("button", { name: "Open run: rerank on" })).toBeInTheDocument();
		expect(within(table).getAllByText("1 of 2 passed")).toHaveLength(2);
	});

	it("filters failed questions and shows the judge's reason", async () => {
		const { user } = open(`/quality?kb=${KB}&tab=runs&run=${RUN_B}`);
		expect(await screen.findByText(/95 % CI/)).toBeInTheDocument();
		expect(screen.getByText("Only 2 questions: confidence intervals are wide.")).toBeInTheDocument();
		expect(await screen.findByText("Do I need to fast for a lipid profile?")).toBeInTheDocument();
		await user.click(screen.getByRole("switch", { name: "Failed only" }));
		const row = (await screen.findByText("Kya aap MRI karte ho?")).closest("tr") as HTMLElement;
		await user.click(within(row).getByRole("button", { name: "Show details" }));
		expect(await screen.findByText(/documents list only pathology tests/)).toBeInTheDocument();
		await waitFor(() => expect(screen.queryByText("Do I need to fast for a lipid profile?")).not.toBeInTheDocument());
	});

	it("rates a question", async () => {
		const { user, mock } = open(`/quality?kb=${KB}&tab=runs&run=${RUN_B}`);
		await screen.findByText("Do I need to fast for a lipid profile?");
		await user.click(screen.getAllByRole("button", { name: "Good" })[0] as HTMLElement);
		await waitFor(() => expect(mock.log.find((l) => l.path === "sample")?.body).toEqual({ rating: "good" }));
	});
});

describe("quality: compare", () => {
	it("compares two runs and marks a non-significant delta", async () => {
		open(`/quality?kb=${KB}&tab=compare&runs=${RUN_A},${RUN_B}`);
		expect(await screen.findByText("+30 pts")).toBeInTheDocument();
		expect(screen.getByText("not significant")).toBeInTheDocument();
		expect(screen.getByText("Some runs have fewer than 30 questions: differences may be noise.")).toBeInTheDocument();
		const group = screen.getByRole("group", { name: "Questions to show" });
		expect(within(group).getByRole("button", { name: "Improved" })).toBeInTheDocument();
		expect(screen.getByText("Do I need to fast for a lipid profile?")).toBeInTheDocument();
	});
});

describe("quality: criteria and the judge", () => {
	it("lets members manage criteria but not the judge or the pass marks", async () => {
		open(`/quality?kb=${KB}&tab=criteria`, "member");
		expect(await screen.findByText("mentions-home-fee")).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Add criterion" })).not.toHaveAttribute("aria-disabled");
		expect(screen.getByRole("button", { name: "Save judge" })).toHaveAttribute("aria-disabled", "true");
		expect(screen.getByRole("button", { name: "Save pass marks" })).toHaveAttribute("aria-disabled", "true");
	});

	it("shows a name error live and saves a criterion", async () => {
		const { user, mock } = open(`/quality?kb=${KB}&tab=criteria`);
		await screen.findByText("mentions-home-fee");
		const name = screen.getByLabelText("Name");
		await user.type(name, "Bad Name");
		await user.tab();
		expect(await screen.findByText(/Lower-case letters, digits and dashes/)).toBeInTheDocument();
		await user.clear(name);
		await user.type(name, "mentions-timings");
		await waitFor(() => expect(screen.queryByText(/Lower-case letters, digits and dashes/)).not.toBeInTheDocument());
		await user.type(screen.getByLabelText("What a good answer must do"), "States the opening hours");
		await user.click(screen.getByRole("button", { name: "Add criterion" }));
		await waitFor(() =>
			expect(mock.log.find((l) => l.path === "criteria")?.body).toMatchObject({
				name: "mentions-timings",
				definition: "States the opening hours",
				enabled: true,
			}),
		);
	});

	it("keeps the stored judge key hidden and sends no key unless one is typed", async () => {
		const { user, mock } = open(`/quality?kb=${KB}&tab=criteria`);
		await screen.findByText("mentions-home-fee");
		await user.type(screen.getByLabelText("Model"), "judge-x");
		await user.click(screen.getByRole("button", { name: "Save judge" }));
		await waitFor(() =>
			expect(mock.log.find((l) => l.path === "settings")?.body).toMatchObject({
				judge_model: "judge-x",
				judge_api_key: null,
			}),
		);
	});
});
