import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, rt, SV, tokenResponse } from "@/test/msw/data";
import { planById, plansState } from "@/test/msw/handlers/plans";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";

function openPlans() {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user: SV, tenant: null, tenants: [] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () =>
			HttpResponse.json({ user: SV, tenant: null, tenants: [], session_id: "s" }),
		),
	);
	return renderApp("/admin/plans");
}

async function editPilot(user: ReturnType<typeof openPlans>["user"]) {
	await user.click(await screen.findByRole("button", { name: "Edit Pilot" }));
	return within(await screen.findByRole("dialog"));
}

const lastPatch = () => plansState.sent.filter((s) => s.method === "PATCH").at(-1)?.body as Record<string, unknown>;

describe("Plans: answer speed and fair use", () => {
	it("a plan card shows the answer speed and its question limits", async () => {
		const pilot = planById("plan-pilot");
		if (pilot)
			Object.assign(pilot, {
				pipeline_profile_label: "Economy",
				fair_use_limits: { questions_per_hour: 30, questions_per_day: 300 },
			});
		openPlans();
		const cards = await screen.findAllByTestId("plan-card");
		const card = cards.find((c) => within(c).queryByText("Pilot"));
		expect(within(card as HTMLElement).getByTestId("plan-speed")).toHaveTextContent("Economy · 30 an hour · 300 a day");
	});

	it("saving without touching fair use sends none of it", async () => {
		const { user } = openPlans();
		const d = await editPilot(user);
		await user.click(d.getByRole("button", { name: /Save/ }));
		await screen.findByTestId("plan-speed", undefined, { timeout: 3000 }).catch(() => undefined);
		const body = lastPatch();
		expect(body).toBeDefined();
		expect(body).not.toHaveProperty("fair_use_limits");
		expect(body).not.toHaveProperty("fair_use_policy");
		expect(body).not.toHaveProperty("pipeline_profile_id");
	});

	it("round-trips the profile, the limits (spend in dollars as integer micro) and the policy", async () => {
		const { user } = openPlans();
		const d = await editPilot(user);
		await user.selectOptions(d.getByLabelText("Answer speed"), "pf-premium");
		await user.type(d.getByLabelText("Questions an hour"), "30");
		await user.type(d.getByLabelText("AI spend a day"), "12.5");
		expect(d.getByText(/About ₹/)).toBeInTheDocument();
		await user.click(d.getByRole("radio", { name: "Pause answers" }));
		await user.click(d.getByRole("button", { name: /Save/ }));
		await screen.findAllByTestId("plan-card");
		const body = lastPatch();
		expect(body.pipeline_profile_id).toBe("pf-premium");
		expect(body.fair_use_limits).toEqual({ questions_per_hour: 30, ai_spend_per_day_usd_micro: 12_500_000 });
		expect(Number.isInteger((body.fair_use_limits as Record<string, number>).ai_spend_per_day_usd_micro)).toBe(true);
		expect(body.fair_use_policy).toMatchObject({
			on_billing_block: "pause_handoff",
			on_fair_use_exceeded: "throttle_then_stop",
		});
	});

	it("emptying a limit that was set sends null for it", async () => {
		const pilot = planById("plan-pilot");
		if (pilot) pilot.fair_use_limits = { questions_per_day: 300 };
		const { user } = openPlans();
		const d = await editPilot(user);
		const field = d.getByLabelText("Questions a day");
		expect(field).toHaveValue("300");
		await user.clear(field);
		await user.click(d.getByRole("button", { name: /Save/ }));
		await screen.findAllByTestId("plan-card");
		expect(lastPatch().fair_use_limits).toEqual({ questions_per_day: null });
	});

	it("says in a sentence what each choice does", async () => {
		const { user } = openPlans();
		const d = await editPilot(user);
		expect(d.getByText(/Questions are answered with the slow profile below, at no charge/)).toBeInTheDocument();
		expect(
			d.getByText(/Visitors see the business's pause message and contact options until the balance is topped up/),
		).toBeInTheDocument();
		expect(d.getByText(/Past the limit, answers use the slow profile. Past the hard line/)).toBeInTheDocument();
		expect(d.getByText(/Nothing slows down. You and the owner are alerted/)).toBeInTheDocument();
	});

	it("refuses spend with more than two decimals before it calls the engine", async () => {
		const { user } = openPlans();
		const d = await editPilot(user);
		await user.type(d.getByLabelText("AI spend a day"), "1.234");
		await user.click(d.getByRole("button", { name: /Save/ }));
		expect(await d.findByText("Use dollars with at most 2 decimals, like 12.50.")).toBeInTheDocument();
		expect(plansState.sent.filter((s) => s.method === "PATCH")).toHaveLength(0);
	});

	it("uses the product's words: no corpus, tenant, lane or slot in the section", async () => {
		const { user } = openPlans();
		const d = await editPilot(user);
		const section = d.getByText("Fair use", { selector: "legend" }).closest("fieldset") as HTMLElement;
		expect(section.textContent ?? "").not.toMatch(/corpus|tenant|\blane\b|\bslot\b|litellm/i);
	});
});
