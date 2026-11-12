import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { pageLoad } from "@/features/payments/checkout";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, ROHAN, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { addOnsState } from "../msw/handlers/billing-addons";
import { COMPLETE_DETAILS, paymentsState } from "../msw/handlers/payments";
import { plansState } from "../msw/handlers/plans";
import { INSTALL, OWN_KEY, order, shopState, stockShop } from "../msw/handlers/shop";
import { server } from "../msw/server";
import { renderApp } from "../render";

const PATIENT = CORPORA[0]?.id ?? "";

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
const asOwner = () => signedInAs(PRIYA, SHREE);
const asMember = () => signedInAs(ROHAN, { ...SHREE, role: "member" });
const asAdmin = () => signedInAs(SV, null);

/** The workspace's billing view, with what the own-key rule says. */
const billingView = (byok: {
	allowed: boolean;
	key_saved: boolean;
	source: "plan" | "add_on" | null;
	plans_with_byok?: string[];
}) =>
	http.get(`${BASE}/api/v1/billing/account`, () =>
		HttpResponse.json({
			tenant_id: SHREE.id,
			plan: { id: "plan-starter", name: "Starter" },
			currency: "INR",
			money_visible: true,
			payment_type: "prepaid",
			usage_mode: "per_question",
			byok_mode: "free",
			subscription_fee_minor: 0,
			billing_period: "monthly",
			per_question_minor: 50,
			byok_per_question_minor: 0,
			overage: "block",
			overage_per_question_minor: 0,
			included_credit_minor: 0,
			credit_limit_minor: 0,
			exempt: false,
			balance_minor: shopState.available,
			available_minor: shopState.available,
			add_ons: [],
			paused: false,
			pause_reason: null,
			alert_level: null,
			billed: true,
			usage_this_period: { questions: 0, charged_minor: 0, unpriced: 0 },
			next_charge_on: null,
			trial_ends_at: null,
			statements: [],
			byok,
		}),
	);

async function openPlan() {
	const app = renderApp("/plan");
	await screen.findByTestId("plan-name");
	return app;
}

const row = (name: string) =>
	screen.getAllByTestId("shop-item").find((li) => within(li).queryByText(name) !== null) as HTMLElement;

describe("Plan & billing → Add-ons and services (the owner)", () => {
	beforeEach(() => {
		stockShop();
		asOwner();
		server.use(billingView({ allowed: false, key_saved: false, source: null }));
	});

	it("groups what is for sale, with the price before GST and the total with GST", async () => {
		await openPlan();
		await screen.findByTestId("shop-card");
		expect(screen.getAllByTestId("shop-group").map((g) => within(g).getByRole("heading").textContent)).toEqual([
			"Your own AI key",
			"Services",
			"Credit packs",
		]);
		expect(within(row("Installation help")).getByTestId("shop-price")).toHaveTextContent(
			"₹1,999.00 + GST · ₹2,358.82 incl. GST",
		);
		expect(within(row("Use your own AI key")).getByText("Renews every month")).toBeInTheDocument();
		expect(within(row("Installation help")).getByText("One time")).toBeInTheDocument();
		expect(within(row("Credit pack ₹1,000")).getByRole("button", { name: "Buy" })).toBeInTheDocument();
	});

	it("shows only the price when GST adds nothing", async () => {
		shopState.items = [{ ...INSTALL, inclusive_total_minor: INSTALL.price_minor }];
		await openPlan();
		expect(await screen.findByTestId("shop-price")).toHaveTextContent(/^₹1,999\.00$/);
	});

	it("buys a service from the balance with a note, and the order appears under Your orders", async () => {
		const app = await openPlan();
		await app.user.click(
			within(await screen.findByTestId("shop-card")).getAllByRole("button", { name: "Buy" })[1] as HTMLElement,
		);
		const dialog = await screen.findByRole("dialog", { name: "Buy Installation help" });
		expect(within(dialog).getByText(/₹1,999\.00 \+ GST/)).toBeInTheDocument();
		await app.user.type(within(dialog).getByLabelText("Anything we should know?"), "Please call before 11");
		await app.user.click(within(dialog).getByRole("button", { name: /Pay ₹1,999\.00 from balance/ }));
		await waitFor(() => expect(screen.queryByRole("dialog", { name: "Buy Installation help" })).toBeNull());
		const sent = shopState.sent.find((s) => s.path.endsWith("/buy"));
		expect(sent?.body).toMatchObject({ payment: "wallet", note: "Please call before 11" });
		expect(sent?.body).toMatchObject({ idempotency_key: expect.stringMatching(/^shop-[0-9a-f]{24}$/) });
		const orders = await screen.findByTestId("owner-orders");
		expect(within(orders).getByText("Installation help")).toBeInTheDocument();
		expect(within(orders).getByTestId("order-status")).toHaveTextContent("Requested");
		expect(within(orders).getByText(/Please call before 11/)).toBeInTheDocument();
		expect(shopState.available).toBe(500_000 - 199_900);
		app.unmount();
	});

	it("a balance that is too low blocks paying from it and says what to do", async () => {
		shopState.available = 150_000;
		server.use(billingView({ allowed: false, key_saved: false, source: null }));
		const app = await openPlan();
		await app.user.click(
			within(await screen.findByTestId("shop-card")).getAllByRole("button", { name: "Buy" })[1] as HTMLElement,
		);
		const dialog = await screen.findByRole("dialog", { name: "Buy Installation help" });
		expect(within(dialog).getByTestId("shop-balance-low")).toHaveTextContent("Your balance is too low");
		expect(within(dialog).getByRole("button", { name: /from balance/ })).toBeDisabled();
		expect(within(dialog).getByRole("button", { name: /Pay online/ })).toBeEnabled();
		app.unmount();
	});

	it("a price that would reach the pause point blocks paying from the balance", async () => {
		shopState.available = 500_000;
		shopState.items = shopState.items.map((i) => (i.id === INSTALL.id ? { ...i, affordable: false } : i));
		server.use(billingView({ allowed: false, key_saved: false, source: null }));
		const app = await openPlan();
		await app.user.click(
			within(await screen.findByTestId("shop-card")).getAllByRole("button", { name: "Buy" })[1] as HTMLElement,
		);
		const dialog = await screen.findByRole("dialog", { name: "Buy Installation help" });
		expect(within(dialog).getByRole("button", { name: /from balance/ })).toBeDisabled();
		app.unmount();
	});

	it("an engine refusal reads in plain words", async () => {
		server.use(
			http.post(`${BASE}/api/v1/billing/add-ons/:id/buy`, () =>
				HttpResponse.json({ detail: "x", code: "insufficient_balance" }, { status: 409 }),
			),
		);
		const app = await openPlan();
		await app.user.click(
			within(await screen.findByTestId("shop-card")).getAllByRole("button", { name: "Buy" })[1] as HTMLElement,
		);
		const dialog = await screen.findByRole("dialog", { name: "Buy Installation help" });
		await app.user.click(within(dialog).getByRole("button", { name: /from balance/ }));
		expect(await within(dialog).findByText(/balance is too low/i)).toBeInTheDocument();
		app.unmount();
	});

	describe("paying online", () => {
		let go: ReturnType<typeof vi.fn>;
		beforeEach(() => {
			go = vi.fn();
			vi.spyOn(pageLoad, "go").mockImplementation(go as (url: string) => void);
			paymentsState.enabled = true;
		});
		afterEach(() => vi.restoreAllMocks());

		it("goes to the pay page with a full page load", async () => {
			paymentsState.taxDetails = { ...COMPLETE_DETAILS };
			const app = await openPlan();
			await app.user.click(
				within(await screen.findByTestId("shop-card")).getAllByRole("button", { name: "Buy" })[1] as HTMLElement,
			);
			const dialog = await screen.findByRole("dialog", { name: "Buy Installation help" });
			await app.user.click(within(dialog).getByRole("button", { name: /Pay online/ }));
			expect(go).toHaveBeenCalledWith(`/pay?purpose=add_on&add_on=${INSTALL.id}`);
			app.unmount();
		});

		it("asks for the tax details first when there are none", async () => {
			paymentsState.taxDetails = { ...COMPLETE_DETAILS, complete: false, legal_name: "" };
			const app = await openPlan();
			await app.user.click(
				within(await screen.findByTestId("shop-card")).getAllByRole("button", { name: "Buy" })[1] as HTMLElement,
			);
			const dialog = await screen.findByRole("dialog", { name: "Buy Installation help" });
			await app.user.click(within(dialog).getByRole("button", { name: /Pay online/ }));
			expect(await screen.findByRole("dialog", { name: /tax details/i })).toBeInTheDocument();
			expect(go).not.toHaveBeenCalled();
			app.unmount();
		});
	});

	it("a feature the plan includes is marked, not for sale", async () => {
		shopState.items = [{ ...OWN_KEY, owned: true, buy_with: [] }];
		await openPlan();
		const r = await screen.findByTestId("shop-item");
		expect(within(r).getByText("Included in your plan")).toBeInTheDocument();
		expect(within(r).queryByRole("button")).toBeNull();
	});

	it("an active monthly purchase shows until when and can stop renewing", async () => {
		shopState.items = [
			{ ...OWN_KEY, owned: true, owned_until: "2026-11-01T00:00:00Z", grant_id: "g-1", buy_with: ["balance"] },
		];
		const app = await openPlan();
		const r = await screen.findByTestId("shop-item");
		expect(within(r).getByText(/Active until 1 Nov 2026/)).toBeInTheDocument();
		await app.user.click(within(r).getByRole("button", { name: "Cancel renewal" }));
		const confirm = await screen.findByRole("alertdialog");
		expect(within(confirm).getByText(/stays active until 1 Nov 2026/)).toBeInTheDocument();
		await app.user.click(within(confirm).getByRole("button", { name: "Cancel renewal" }));
		expect(await screen.findByText(/Won't renew · ends 1 Nov 2026/)).toBeInTheDocument();
		expect(screen.queryByRole("button", { name: "Cancel renewal" })).toBeNull();
		app.unmount();
	});

	it("a member sees the shop but no Buy button", async () => {
		asMember();
		const app = await openPlan();
		const card = await screen.findByTestId("shop-card");
		expect(within(card).queryByRole("button", { name: "Buy" })).toBeNull();
		expect(within(card).getAllByText("Ask an owner of your workspace to buy this.").length).toBeGreaterThan(0);
		app.unmount();
	});

	it("shows nothing while the provider has put nothing in the shop", async () => {
		shopState.items = [];
		await openPlan();
		await waitFor(() => expect(shopState.sent).toEqual([]));
		expect(screen.queryByTestId("shop-card")).toBeNull();
	});
});

describe("Plan & billing → Your orders", () => {
	beforeEach(() => {
		stockShop();
	});

	it("shows the status, the provider's note and the support date, never an internal note", async () => {
		asOwner();
		shopState.orders = [
			order({
				status: "done",
				public_note: "Widget is live on your site",
				internal_note: "vendor charged us ₹4,000",
				support_until: "2027-01-20",
				owner_note: "Please call before 11",
			}),
		];
		await openPlan();
		const card = await screen.findByTestId("owner-orders");
		expect(within(card).getByTestId("order-status")).toHaveTextContent("Done");
		expect(within(card).getByTestId("order-note")).toHaveTextContent(
			"A note from Sahayak AI: Widget is live on your site",
		);
		expect(within(card).getByTestId("order-support")).toHaveTextContent("Included support until 20 Jan 2027");
		expect(within(card).getByRole("link", { name: "Ask about this order" })).toHaveAttribute(
			"href",
			expect.stringContaining("/support?request="),
		);
		expect(card.textContent).not.toContain("vendor charged");
	});

	it("a member may read the orders", async () => {
		asMember();
		shopState.orders = [order()];
		await openPlan();
		expect(await screen.findByTestId("owner-order")).toBeInTheDocument();
	});
});

describe("Model & keys → the own-key lock", () => {
	const openModel = async () => {
		const app = renderApp(`/kbs/${PATIENT}?tab=model`);
		await screen.findByRole("heading", { level: 1, name: "Patient help" });
		return app;
	};

	it("replaces the key form with a plain note and a link, until the plan or an add-on allows it", async () => {
		asOwner();
		server.use(billingView({ allowed: false, key_saved: false, source: null, plans_with_byok: ["Pro", "Business"] }));
		const app = await openModel();
		const lock = await screen.findByTestId("own-key-lock");
		expect(within(lock).getByTestId("own-key-lock-body")).toHaveTextContent(
			"Using your own AI key is part of Pro and Business, or available as an add-on. Until then, answers use Sahayak AI's AI and your balance.",
		);
		expect(within(lock).getByRole("link", { name: "See the add-on" })).toHaveAttribute("href", "/plan");
		expect(screen.queryByRole("button", { name: /^Set key|Replace/ })).toBeNull();
		app.unmount();
	});

	it("names no plan when none of the deployer's plans includes it", async () => {
		asOwner();
		server.use(billingView({ allowed: false, key_saved: false, source: null, plans_with_byok: [] }));
		const app = await openModel();
		const lock = await screen.findByTestId("own-key-lock");
		const body = within(lock).getByTestId("own-key-lock-body");
		expect(body).toHaveTextContent("Using your own AI key is available as an add-on.");
		expect(body).not.toHaveTextContent("Pro");
		app.unmount();
	});

	it("keeps a saved key, says it is not used and still lets it be removed", async () => {
		asOwner();
		server.use(billingView({ allowed: false, key_saved: true, source: null }));
		const app = await openModel();
		const lock = await screen.findByTestId("own-key-lock");
		expect(within(lock).getByTestId("own-key-unused")).toHaveTextContent(
			"Your key is saved but not used on your current plan. Answers use Sahayak AI's AI and your balance.",
		);
		expect(within(lock).getByRole("button", { name: /Remove|Clear/ })).toBeInTheDocument();
		app.unmount();
	});

	it("is not there when the plan allows the key", async () => {
		asOwner();
		server.use(billingView({ allowed: true, key_saved: false, source: "plan" }));
		const app = await openModel();
		await screen.findByLabelText("Model");
		expect(screen.queryByTestId("own-key-lock")).toBeNull();
		app.unmount();
	});
});

describe("Billing → Service orders (the operator)", () => {
	beforeEach(() => {
		asAdmin();
		shopState.orders = [
			order({ owner_note: "Please call before 11" }),
			order({ name: "Website design", status: "in_progress", tenant_name: "Rangoli Sarees" }),
			order({ name: "Priority support", status: "done", support_until: "2027-01-20" }),
		];
	});

	const open = async () => {
		const app = renderApp("/admin/billing?tab=service-orders");
		await screen.findByTestId("billing-service-orders");
		await screen.findByRole("table", { name: "Service orders" });
		return app;
	};

	it("lists the orders and filters by status", async () => {
		const app = await open();
		expect(screen.getAllByRole("row")).toHaveLength(4);
		await app.user.selectOptions(screen.getByLabelText("Status"), "in_progress");
		await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(2));
		expect(screen.getByText("Website design")).toBeInTheDocument();
		app.unmount();
	});

	it("moves an order along with a note the owner reads and one only the operator reads", async () => {
		const app = await open();
		await app.user.click(within(screen.getAllByRole("row")[1] as HTMLElement).getByRole("button", { name: "Open" }));
		const sheet = await screen.findByTestId("order-sheet");
		expect(within(sheet).getByText(/Please call before 11/)).toBeInTheDocument();
		await app.user.type(within(sheet).getByLabelText("Note for the owner"), "We start on Monday");
		await app.user.type(within(sheet).getByLabelText("Internal note"), "vendor quote 12k");
		await app.user.click(within(sheet).getByRole("button", { name: "Start work" }));
		await waitFor(() =>
			expect(shopState.sent.at(-1)?.body).toEqual({
				status: "in_progress",
				public_note: "We start on Monday",
				internal_note: "vendor quote 12k",
			}),
		);
		expect(await within(sheet).findByText("In progress")).toBeInTheDocument();
		expect(within(sheet).queryByRole("button", { name: "Start work" })).toBeNull();
		await app.user.click(within(sheet).getByRole("button", { name: "Mark done" }));
		await waitFor(() => expect(shopState.orders.find((o) => o.public_note)?.status).toBe("done"));
		app.unmount();
	});

	it("saves notes without moving the order", async () => {
		const app = await open();
		await app.user.click(within(screen.getAllByRole("row")[1] as HTMLElement).getByRole("button", { name: "Open" }));
		const sheet = await screen.findByTestId("order-sheet");
		await app.user.type(within(sheet).getByLabelText("Internal note"), "call Tuesday");
		await app.user.click(within(sheet).getByRole("button", { name: "Save notes" }));
		await waitFor(() =>
			expect(shopState.sent.at(-1)?.body).toEqual({ public_note: null, internal_note: "call Tuesday" }),
		);
		app.unmount();
	});

	it("warns that cancelling refunds nothing and offers no move from a finished order", async () => {
		const app = await open();
		await app.user.click(within(screen.getAllByRole("row")[1] as HTMLElement).getByRole("button", { name: "Open" }));
		const sheet = await screen.findByTestId("order-sheet");
		expect(within(sheet).getByText(/Cancelling does not refund anything/)).toBeInTheDocument();
		await app.user.keyboard("{Escape}");
		await waitFor(() => expect(screen.queryByTestId("order-sheet")).toBeNull());
		await app.user.click(within(screen.getAllByRole("row")[3] as HTMLElement).getByRole("button", { name: "Open" }));
		const finished = await screen.findByTestId("order-sheet");
		expect(within(finished).getByText(/This order is done/)).toBeInTheDocument();
		expect(within(finished).queryByRole("button", { name: /Start work|Mark done|Cancel order/ })).toBeNull();
		app.unmount();
	});

	it("an engine refusal of a move reads in plain words", async () => {
		server.use(
			http.patch(`${BASE}/api/v1/admin/billing/service-orders/:id`, () =>
				HttpResponse.json({ detail: "x", code: "invalid_service_order_transition" }, { status: 409 }),
			),
		);
		const app = await open();
		await app.user.click(within(screen.getAllByRole("row")[1] as HTMLElement).getByRole("button", { name: "Open" }));
		const sheet = await screen.findByTestId("order-sheet");
		await app.user.click(within(sheet).getByRole("button", { name: "Start work" }));
		expect(await within(sheet).findByText(/cannot move to that step/)).toBeInTheDocument();
		app.unmount();
	});

	it("has an empty state", async () => {
		shopState.orders = [];
		renderApp("/admin/billing?tab=service-orders");
		expect(await screen.findByTestId("orders-empty")).toHaveTextContent("No service orders yet.");
	});
});

describe("Billing → Add-ons: feature unlocks and services", () => {
	beforeEach(asAdmin);

	const openCatalogue = async () => {
		const app = renderApp("/admin/billing?tab=add-ons");
		await screen.findByTestId("billing-add-ons");
		await screen.findAllByTestId("add-on-card");
		return app;
	};
	const last = () => addOnsState.sent.filter((s) => s.method === "POST").at(-1)?.body as Record<string, unknown>;

	it("creates a monthly own-key unlock that is shown in the owner's shop", async () => {
		const app = await openCatalogue();
		await app.user.click(screen.getByRole("button", { name: "New add-on" }));
		const dialog = await screen.findByRole("dialog", { name: "New add-on" });
		await app.user.selectOptions(within(dialog).getByLabelText("Kind"), "feature_unlock");
		await app.user.type(within(dialog).getByLabelText("Name"), "Use your own AI key");
		await app.user.type(within(dialog).getByLabelText("Price"), "499");
		await app.user.click(within(dialog).getByLabelText("Renews every month from the balance"));
		await app.user.click(within(dialog).getByLabelText("Show in the owner's shop"));
		expect(within(dialog).getByTestId("add-on-preview")).toHaveTextContent(
			"Use your own AI key, this month, renews every month",
		);
		await app.user.click(within(dialog).getByRole("button", { name: "Create add-on" }));
		await waitFor(() => expect(screen.queryByRole("dialog", { name: "New add-on" })).toBeNull());
		expect(last()).toEqual({
			kind: "feature_unlock",
			name: "Use your own AI key",
			currency: "INR",
			price_minor: 49_900,
			bonus_pct: 0,
			feature: "byok",
			duration: "period",
			renews: true,
			sold_to_owners: true,
		});
		const card = await screen.findByRole("article", { name: /^Use your own AI key/ });
		expect(within(card).getByText("In the owner's shop")).toBeInTheDocument();
		app.unmount();
	});

	it("a permanent unlock cannot renew", async () => {
		const app = await openCatalogue();
		await app.user.click(screen.getByRole("button", { name: "New add-on" }));
		const dialog = await screen.findByRole("dialog", { name: "New add-on" });
		await app.user.selectOptions(within(dialog).getByLabelText("Kind"), "feature_unlock");
		await app.user.selectOptions(within(dialog).getByLabelText("For how long"), "permanent");
		expect(within(dialog).getByLabelText("Renews every month from the balance")).toBeDisabled();
		app.unmount();
	});

	it("creates a service with support months and its SAC, and checks the SAC as you type", async () => {
		const app = await openCatalogue();
		await app.user.click(screen.getByRole("button", { name: "New add-on" }));
		const dialog = await screen.findByRole("dialog", { name: "New add-on" });
		await app.user.selectOptions(within(dialog).getByLabelText("Kind"), "service");
		await app.user.type(within(dialog).getByLabelText("Name"), "Installation help");
		await app.user.type(within(dialog).getByLabelText("Price"), "1999");
		await app.user.type(within(dialog).getByLabelText("SAC (service code)"), "12345");
		await app.user.click(within(dialog).getByRole("button", { name: "Create add-on" }));
		expect(await within(dialog).findByText("Six digits starting with 99, or leave it empty.")).toBeInTheDocument();
		await app.user.clear(within(dialog).getByLabelText("SAC (service code)"));
		await app.user.type(within(dialog).getByLabelText("SAC (service code)"), "998313");
		await app.user.type(within(dialog).getByLabelText("Months of support included"), "3");
		expect(within(dialog).getByTestId("add-on-preview")).toHaveTextContent("A service, includes 3 months of support");
		await app.user.click(within(dialog).getByRole("button", { name: "Create add-on" }));
		await waitFor(() => expect(screen.queryByRole("dialog", { name: "New add-on" })).toBeNull());
		expect(last()).toMatchObject({
			kind: "service",
			price_minor: 199_900,
			sac_code: "998313",
			includes_support_months: 3,
			renews: false,
		});
		app.unmount();
	});

	it("marks a seeded price as a placeholder until it is edited", async () => {
		addOnsState.items = addOnsState.items.map((a, i) => (i === 0 ? { ...a, placeholder: true } : a));
		const app = await openCatalogue();
		const badges = screen.getAllByTestId("placeholder-badge");
		expect(badges).toHaveLength(1);
		expect(badges[0]).toHaveTextContent("Placeholder price — set yours");
		app.unmount();
	});
});

describe("Plans → the own-key switch", () => {
	beforeEach(asAdmin);

	it("the plan card says whether businesses may use their own key, and the form switches it", async () => {
		const app = renderApp("/admin/plans");
		await screen.findAllByTestId("plan-card");
		expect(screen.getAllByTestId("plan-own-key")[0]).toHaveTextContent("Own AI key allowed");
		const card = screen.getAllByTestId("plan-card")[0] as HTMLElement;
		await app.user.click(within(card).getByRole("button", { name: /Edit/ }));
		const dialog = await screen.findByRole("dialog");
		const toggle = within(dialog).getByLabelText("Businesses on this plan may use their own AI key");
		expect(toggle).toBeChecked();
		await app.user.click(toggle);
		await app.user.click(within(dialog).getByRole("button", { name: "Save plan" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(plansState.sent.at(-1)?.body).toMatchObject({ byok_allowed: false });
		app.unmount();
	});
});
