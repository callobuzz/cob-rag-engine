import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, OPERATOR, rt, SV, tokenResponse } from "@/test/msw/data";
import { adminView, fairUseState, pipelinesState } from "@/test/msw/handlers/pipelines";
import { planById } from "@/test/msw/handlers/plans";
import { VIDYA_ID } from "@/test/msw/handlers/tenants";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";

function signedInAsAdmin() {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user: SV, tenant: null, tenants: [] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () =>
			HttpResponse.json({ user: SV, tenant: null, tenants: [], session_id: "s" }),
		),
	);
}

/** Vidya Classes on the Starter plan, which allows 30 questions an hour and 300 a day. */
function setUp(over: Parameters<typeof adminView>[1] = {}) {
	const starter = planById("plan-starter");
	if (starter) starter.fair_use_limits = { questions_per_hour: 30, questions_per_day: 300 };
	fairUseState.views[VIDYA_ID] = adminView(VIDYA_ID, { name: "Vidya Classes", plan: "Starter", ...over });
	signedInAsAdmin();
	return renderApp(`/admin/customers/${VIDYA_ID}?tab=limits`);
}

const panel = async (name: string) => within(await screen.findByRole("region", { name }));
const lastPut = () => fairUseState.sent.at(-1)?.body as Record<string, unknown>;

describe("customer: answer speed and limits", () => {
	it("a business the gate ignores (Internal plan) gets the 'Never limited' panel, not editable limits", async () => {
		setUp({ exempt: true });
		expect(await screen.findByTestId("customer-limits-exempt")).toBeInTheDocument();
		expect(screen.queryByRole("button", { name: "Save limits" })).toBeNull();
	});

	it("shows the plan's values as 'Follow plan', today's decisions and the meters with their state", async () => {
		setUp();
		const limits = await panel("Limits");
		expect(limits.getByLabelText("Questions an hour")).toHaveDisplayValue("Follow plan (30 an hour)");
		expect(limits.getByLabelText("Questions a minute")).toHaveDisplayValue("Follow plan (no limit)");
		expect(await screen.findByTestId("fair-use-today")).toHaveTextContent("Today: 250 answered, 38 slower, 3 paused");
		const usage = await panel("Usage");
		expect(usage.getByRole("meter", { name: "Questions today" })).toHaveAttribute("aria-valuenow", "83");
		expect(usage.getByText("Near limit")).toBeInTheDocument();
	});

	it("sets an own limit, takes a limit off, and sends only what changed", async () => {
		const { user } = setUp();
		const limits = await panel("Limits");
		await user.selectOptions(limits.getByLabelText("Questions an hour"), "custom");
		await user.type(limits.getByLabelText("Questions an hour, own limit"), "60");
		await user.selectOptions(limits.getByLabelText("Questions a day"), "none");
		await user.click(limits.getByRole("button", { name: "Save limits" }));
		await waitFor(() => expect(fairUseState.sent).toHaveLength(1));
		expect(lastPut()).toEqual({ limits: { questions_per_hour: 60, questions_per_day: null } });
		// The saved copy comes back: the row now says it is the customer's own.
		expect(await within(await screen.findByRole("region", { name: "Limits" })).findAllByText("Own")).not.toHaveLength(
			0,
		);
	});

	it("going back to the plan clears the override", async () => {
		const { user } = setUp({
			limits: { questions_per_hour: 60, questions_per_day: 300 },
			overridden: ["questions_per_hour"],
		});
		const limits = await panel("Limits");
		const field = limits.getByLabelText("Questions an hour");
		expect(field).toHaveDisplayValue("Own limit");
		expect(limits.getByLabelText("Questions an hour, own limit")).toHaveValue("60");
		await user.selectOptions(field, "follow");
		await user.click(limits.getByRole("button", { name: "Save limits" }));
		await waitFor(() => expect(fairUseState.sent).toHaveLength(1));
		expect(lastPut()).toEqual({ clear: ["questions_per_hour"] });
	});

	it("an own spend limit is dollars and goes as integer micro", async () => {
		const { user } = setUp();
		const limits = await panel("Limits");
		await user.selectOptions(limits.getByLabelText("AI spend a day"), "custom");
		await user.type(limits.getByLabelText("AI spend a day, own limit"), "12.5");
		await user.click(limits.getByRole("button", { name: "Save limits" }));
		await waitFor(() => expect(fairUseState.sent).toHaveLength(1));
		expect(lastPut()).toEqual({ limits: { ai_spend_per_day_usd_micro: 12_500_000 } });
	});

	it("refuses a bad own limit before it calls the engine", async () => {
		const { user } = setUp();
		const limits = await panel("Limits");
		await user.selectOptions(limits.getByLabelText("Questions an hour"), "custom");
		await user.type(limits.getByLabelText("Questions an hour, own limit"), "lots");
		await user.click(limits.getByRole("button", { name: "Save limits" }));
		expect(await limits.findByText(/Use a whole number of 1 or more/)).toBeInTheDocument();
		expect(fairUseState.sent).toHaveLength(0);
	});

	it("changes what happens for this customer only when told to", async () => {
		const { user } = setUp();
		const limits = await panel("Limits");
		await user.selectOptions(limits.getByLabelText("When the balance runs out"), "pause_handoff");
		await user.click(limits.getByRole("button", { name: "Save limits" }));
		await waitFor(() => expect(fairUseState.sent).toHaveLength(1));
		expect(lastPut()).toEqual({ policy: { on_billing_block: "pause_handoff" } });
	});

	it("gives the customer another pipeline profile", async () => {
		const { user } = setUp();
		const speed = await panel("Answer speed");
		await user.selectOptions(speed.getByLabelText("Pipeline profile"), "pf-premium");
		await user.click(speed.getByRole("button", { name: "Save answer speed" }));
		await waitFor(() =>
			expect(pipelinesState.sent.at(-1)).toMatchObject({
				path: `tenants/${VIDYA_ID}`,
				body: { profile_id: "pf-premium" },
			}),
		);
		expect(await screen.findByText(/Answers now use Premium, shown to the owner as “Premium”/)).toBeInTheDocument();
	});

	it("the operator's own workspace is never limited", async () => {
		signedInAsAdmin();
		renderApp(`/admin/customers/${OPERATOR.id}?tab=limits`);
		expect(await screen.findByTestId("customer-limits-exempt")).toHaveTextContent(
			/never slowed down, paused or limited/,
		);
		expect(screen.queryByRole("button", { name: "Save limits" })).not.toBeInTheDocument();
	});

	it("the tab is in the list of the customer's sections", async () => {
		setUp();
		expect(await screen.findByRole("tab", { name: "Answer speed & limits" })).toHaveAttribute("aria-selected", "true");
	});
});

describe("customers list: fair-use state", () => {
	it("shows OK, Near limit, Slowed down or Paused next to each active customer's plan", async () => {
		signedInAsAdmin();
		renderApp("/admin/customers?status=active");
		const vidya = (await screen.findByRole("rowheader", { name: /Vidya Classes/ })).closest("tr") as HTMLElement;
		await waitFor(() => expect(within(vidya).getByText("Slowed down")).toBeInTheDocument());
		const shree = screen.getByRole("rowheader", { name: /Shree Labs/ }).closest("tr") as HTMLElement;
		expect(within(shree).getByText("OK")).toBeInTheDocument();
	});
});
