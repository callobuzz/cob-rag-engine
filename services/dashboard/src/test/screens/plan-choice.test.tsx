import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { pageLoad } from "@/features/payments/checkout";
import { PENDING_EMAIL_KEY, RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, ROHAN, rt, SHREE, tokenResponse } from "../msw/data";
import {
	choice,
	FREE,
	FREE_ID,
	PRO_ID,
	PUBLIC_FREE,
	PUBLIC_PRO,
	planChoiceState,
	STANDARD_ID,
	stockPlans,
} from "../msw/handlers/plan-choice";
import { server } from "../msw/server";
import { renderApp } from "../render";

/**
 * P6: choosing and changing a plan. Plan & billing's "Change plan" card (owners): the move's cost
 * before confirming, an upgrade applied at once, one waiting for a payment (pay page or top-up
 * request), a downgrade at the renewal and calling it off; the plan chosen on the pricing page at
 * sign-up, and where the owner lands after verifying.
 */
function signedInAs(user: typeof PRIYA, tenant: typeof SHREE) {
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
}

async function openPlan() {
	const app = renderApp("/plan");
	await screen.findByTestId("plan-name");
	return app;
}

const tile = (slug: string) =>
	screen.getAllByTestId("plan-choice").find((li) => li.getAttribute("data-plan") === slug) as HTMLElement;

describe("Plan & billing → Change plan (an owner)", () => {
	let go: ReturnType<typeof vi.fn>;
	beforeEach(() => {
		signedInAs(PRIYA, SHREE);
		stockPlans();
		go = vi.fn();
		vi.spyOn(pageLoad, "go").mockImplementation(go as (url: string) => void);
	});
	afterEach(() => vi.restoreAllMocks());

	it("shows each plan with GST beside the fee and what a move costs today", async () => {
		await openPlan();
		await screen.findByTestId("change-plan-card");
		expect(within(tile("free")).getByText("Your plan")).toBeInTheDocument();
		expect(within(tile("standard")).getByTestId("plan-choice-price")).toHaveTextContent(
			"₹999.00 a month + GST · ₹1,178.82 incl. GST",
		);
		expect(within(tile("standard")).getByTestId("plan-choice-note")).toHaveTextContent(
			"₹515.61 today for the rest of this period",
		);
		expect(within(tile("pro")).getByText("Your own AI key included")).toBeInTheDocument();
		expect(within(tile("pro")).getByText(/balance is too low/)).toBeInTheDocument();
	});

	it("shows the amount before confirming, then moves up at once", async () => {
		const app = await openPlan();
		await app.user.click(within(tile("standard")).getByRole("button", { name: "Upgrade to Standard" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Upgrade to Standard?" });
		const body = within(dialog).getByTestId("plan-confirm-body");
		expect(body).toHaveTextContent("₹515.61 comes from your balance now");
		expect(body).toHaveTextContent("₹232.25 of extra credit is added with it.");
		expect(body).toHaveTextContent("From your next renewal you pay ₹999.00 a month");
		expect(planChoiceState.sent).toEqual([]); // nothing until confirmed
		await app.user.click(within(dialog).getByRole("button", { name: "Upgrade now" }));
		expect(await screen.findByText("You're on Standard now")).toBeInTheDocument();
		expect(planChoiceState.sent).toEqual([{ method: "POST", path: "/billing/plan", body: { plan_id: STANDARD_ID } }]);
		await waitFor(() => expect(within(tile("standard")).getByText("Your plan")).toBeInTheDocument());
	});

	it("an upgrade the balance cannot cover goes to the pay page for the amount", async () => {
		const app = await openPlan();
		await app.user.click(within(tile("pro")).getByRole("button", { name: "Upgrade to Pro" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Upgrade to Pro?" });
		expect(within(dialog).getByTestId("plan-confirm-pay-first")).toHaveTextContent(
			"Your balance cannot cover ₹2,580.12 yet",
		);
		await app.user.click(within(dialog).getByRole("button", { name: "Continue to payment" }));
		await waitFor(() => expect(go).toHaveBeenCalledWith("/pay?purpose=top_up&amount=258100"));
		expect(await screen.findByTestId("plan-pending")).toHaveTextContent("Pro starts when your payment arrives.");
	});

	it("without online payment, a waiting move asks for a top-up with the amount filled in", async () => {
		stockPlans({ pay_online: false });
		const app = await openPlan();
		await app.user.click(within(tile("pro")).getByRole("button", { name: "Upgrade to Pro" }));
		await app.user.click(
			within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Continue to payment" }),
		);
		const dialog = await screen.findByRole("dialog", { name: "Request a top-up" });
		expect(within(dialog).getByLabelText(/Amount/)).toHaveValue("2581.00");
		expect(within(dialog).getByLabelText(/Note/)).toHaveValue("For the move to Pro.");
		expect(go).not.toHaveBeenCalled();
		expect(screen.getByTestId("plan-pending")).toHaveTextContent("Your plan starts when Sahayak AI confirms");
	});

	it("a move down waits for the renewal and can be called off", async () => {
		stockPlans({
			current_plan_id: STANDARD_ID,
			items: [
				{ ...FREE, current: false, direction: "downgrade", effective_on: "2026-11-01" },
				choice({ current: true, direction: "current", due_now_minor: 0, effective_on: null }),
			],
		});
		const app = await openPlan();
		expect(await screen.findByTestId("change-plan-card")).toBeInTheDocument();
		expect(within(tile("free")).getByTestId("plan-choice-note")).toHaveTextContent(
			"Starts on 1 Nov 2026. Nothing is refunded for this period.",
		);
		await app.user.click(within(tile("free")).getByRole("button", { name: "Move to Free" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Move to Free?" });
		expect(within(dialog).getByTestId("plan-confirm-body")).toHaveTextContent(
			"You keep Standard until 1 Nov 2026, then move to Free. Nothing is refunded",
		);
		await app.user.click(within(dialog).getByRole("button", { name: "Move at renewal" }));
		expect(await screen.findByTestId("plan-pending")).toHaveTextContent("Moving to Free on 1 Nov 2026.");
		await app.user.click(screen.getByRole("button", { name: "Keep my current plan" }));
		await app.user.click(
			within(await screen.findByRole("alertdialog", { name: "Keep your current plan?" })).getByRole("button", {
				name: "Keep my plan",
			}),
		);
		await waitFor(() => expect(screen.queryByTestId("plan-pending")).toBeNull());
		expect(planChoiceState.sent.map((s) => s.method)).toEqual(["POST", "DELETE"]);
	});

	it("a plan with a fee says to ask the provider while payments are off", async () => {
		stockPlans({
			billing_on: false,
			items: [
				{ ...FREE, price: null },
				choice({ price: null, available: false, unavailable_reason: "needs_billing", direction: "switch" }),
			],
		});
		await openPlan();
		await screen.findByTestId("change-plan-card");
		expect(within(tile("standard")).getByTestId("plan-choice-unavailable")).toHaveTextContent(
			"Ask Sahayak AI to move you to this plan.",
		);
		expect(within(tile("standard")).queryByRole("button")).toBeNull();
	});
});

describe("Plan & billing → Change plan (a member)", () => {
	it("is not shown", async () => {
		signedInAs(ROHAN, { ...SHREE, role: "member" });
		stockPlans();
		planChoiceState.role = "member";
		await openPlan();
		await screen.findByTestId("plan-meters");
		expect(screen.queryByTestId("change-plan-card")).toBeNull();
	});
});

describe("/signup?plan=", () => {
	let sent: Record<string, unknown> | null = null;
	beforeEach(() => {
		sent = null;
		planChoiceState.publicItems = [PUBLIC_FREE, PUBLIC_PRO];
		server.use(
			http.post(`${BASE}/api/v1/auth/signup`, async ({ request }) => {
				sent = (await request.json()) as Record<string, unknown>;
				return HttpResponse.json({ status: "verification_sent", email: "lata@kaveri.in" }, { status: 202 });
			}),
		);
	});

	async function submit(app: ReturnType<typeof renderApp>) {
		await app.user.type(await screen.findByLabelText("Business name"), "Kaveri Foods");
		await app.user.type(screen.getByLabelText("Your name"), "Lata");
		await app.user.type(screen.getByLabelText("Work email"), "lata@kaveri.in");
		await app.user.type(screen.getByLabelText("Password"), "a long pass phrase");
		await app.user.click(screen.getByRole("button", { name: "Create account" }));
		await waitFor(() => expect(sent).not.toBeNull());
	}

	it("shows the plan chosen on the pricing page and sends it", async () => {
		const app = renderApp("/signup?plan=pro");
		const plan = await screen.findByTestId("signup-plan");
		expect(plan).toHaveTextContent("Pro");
		expect(within(plan).getByTestId("signup-plan-price")).toHaveTextContent("₹4,999.00 a month · ₹5,898.82 incl. GST");
		expect(plan).toHaveTextContent("Nothing is charged until you choose to pay.");
		await submit(app);
		expect(sent).toMatchObject({ plan: PRO_ID });
	});

	it("a free plan says so", async () => {
		renderApp(`/signup?plan=${FREE_ID}`);
		expect(await screen.findByTestId("signup-plan-price")).toHaveTextContent("Free to start");
	});

	it("an unknown plan is not shown and not sent: the default plan applies", async () => {
		const app = renderApp("/signup?plan=platinum");
		await screen.findByLabelText("Business name");
		expect(screen.queryByTestId("signup-plan")).toBeNull();
		await submit(app);
		expect(sent).not.toHaveProperty("plan");
	});
});

describe("/verify with a plan that waits for payment", () => {
	it("lands the new owner on Plan & billing", async () => {
		stockPlans();
		sessionStorage.setItem(PENDING_EMAIL_KEY, "lata@kaveri.in");
		server.use(
			http.post(`${BASE}/api/v1/auth/verify`, () =>
				HttpResponse.json({
					...tokenResponse(),
					plan_choice: { status: "awaiting_payment", plan_id: PRO_ID, plan_name: "Pro", amount_due_minor: 499_900 },
				}),
			),
		);
		const app = renderApp("/verify");
		const first = await screen.findByLabelText("Digit 1 of 6");
		first.focus();
		await app.user.keyboard("123456");
		await waitFor(() => expect(app.pathname()).toBe("/plan"));
	});
});
