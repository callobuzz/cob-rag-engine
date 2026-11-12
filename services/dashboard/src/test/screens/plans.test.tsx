import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, rt, SV, tokenResponse } from "../msw/data";
import { plansState } from "../msw/handlers/plans";
import { server } from "../msw/server";
import { renderApp } from "../render";

async function openPlans() {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user: SV, tenant: null, tenants: [] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () =>
			HttpResponse.json({ user: SV, tenant: null, tenants: [], session_id: "s" }),
		),
	);
	const app = renderApp("/admin/plans");
	await screen.findByRole("heading", { level: 1, name: "Plans" });
	await screen.findAllByTestId("plan-card");
	return app;
}

// BIZ3: what a plan sends when nothing about the business dashboard was touched
const DEFAULT_FLAGS = {
	advanced: true,
	models: true,
	retrieval: true,
	keys_agents: true,
	webhooks: true,
	quality: true,
	api_docs: true,
	usage_details: true,
	widget_code_only: false,
};
const BIZ3_DEFAULTS = {
	website_learning_enabled: true,
	website_pages_max: null,
	dashboard_features: DEFAULT_FLAGS,
	channels: { whatsapp: false, whatsapp_fees: "pass_through" }, // TV6
	byok_allowed: true, // P5 plans
	self_service: false, // P6 plan choice
};

const card = (name: string) => screen.getByRole("article", { name: new RegExp(`^${name}`) });

describe("plans", () => {
	it("cards: default first, active by name, switched off last; limits and badges", async () => {
		await openPlans();
		expect(screen.getAllByTestId("plan-card").map((c) => within(c).getByRole("heading").textContent)).toEqual([
			"StarterDefault for new sign-ups",
			"Pilot",
			"Pro",
			"StandardBuilt in",
			"Diwali offerSwitched off",
			"InternalLocked",
		]);
		const starter = card("Starter");
		expect(within(starter).getByText("₹1,499 / month · billed by you")).toBeInTheDocument();
		expect(within(starter).getByText("Questions a month").nextSibling).toHaveTextContent("1,000");
		expect(within(starter).getByText("4 customers")).toBeInTheDocument();
		expect(within(card("Standard")).getAllByText("Unlimited")).toHaveLength(5); // four limits + website pages (BIZ3)
		expect(screen.getByText(/Search-only calls don't count/)).toBeInTheDocument();
	});

	it("create: empty limits are null, numbers with grouping are parsed, default toggle", async () => {
		const app = await openPlans();
		await app.user.click(screen.getByRole("button", { name: "New plan" }));
		const dialog = await screen.findByRole("dialog", { name: "New plan" });
		await app.user.type(within(dialog).getByLabelText("Name"), "Growth");
		await app.user.type(within(dialog).getByLabelText("Price note"), "₹9,999 / month");
		await app.user.type(within(dialog).getByLabelText("Knowledge bases"), "5");
		await app.user.type(within(dialog).getByLabelText("Questions a month"), "25,000");
		await app.user.type(within(dialog).getByLabelText("Documents"), "0");
		await app.user.click(within(dialog).getByRole("button", { name: "Create plan" }));
		expect(await within(dialog).findByText("Use a whole number of 1 or more, or leave it empty.")).toBeInTheDocument();
		await app.user.clear(within(dialog).getByLabelText("Documents"));
		await app.user.type(within(dialog).getByLabelText("Documents"), "1.5");
		await app.user.click(within(dialog).getByRole("button", { name: "Create plan" }));
		expect(await within(dialog).findByText("Use a whole number of 1 or more, or leave it empty.")).toBeInTheDocument();
		await app.user.clear(within(dialog).getByLabelText("Documents"));
		await app.user.click(within(dialog).getByRole("checkbox", { name: "Default plan" }));
		await app.user.click(within(dialog).getByRole("button", { name: "Create plan" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(plansState.sent[0]).toEqual({
			method: "POST",
			body: {
				name: "Growth",
				description: "₹9,999 / month",
				max_knowledge_bases: 5,
				monthly_questions: 25000,
				max_documents: null,
				max_widget_keys: null,
				max_upload_mb: null,
				max_document_tokens: null,
				count_cached_answers: true,
				is_default: true,
				...BIZ3_DEFAULTS,
			},
		});
		await waitFor(() =>
			expect(within(screen.getAllByTestId("plan-card")[0] as HTMLElement).getByRole("heading")).toHaveTextContent(
				"Growth",
			),
		);
	});

	it("plan_name_taken lands on the name field", async () => {
		const app = await openPlans();
		await app.user.click(screen.getByRole("button", { name: "New plan" }));
		const dialog = await screen.findByRole("dialog", { name: "New plan" });
		await app.user.type(within(dialog).getByLabelText("Name"), "Pro");
		await app.user.click(within(dialog).getByRole("button", { name: "Create plan" }));
		expect(await within(dialog).findByText("A plan with that name exists.")).toBeInTheDocument();
		expect(within(dialog).getByLabelText("Name")).toHaveAttribute("aria-invalid", "true");
	});

	it("edit the default plan: the default box is locked on; limits cleared become null", async () => {
		const app = await openPlans();
		await app.user.click(screen.getByRole("button", { name: "Edit Starter" }));
		const dialog = await screen.findByRole("dialog", { name: "Edit plan · Starter" });
		const box = within(dialog).getByRole("checkbox", { name: "Default plan" });
		expect(box).toBeChecked();
		expect(box).toBeDisabled();
		expect(within(dialog).getByText(/make another plan the default/)).toBeInTheDocument();
		await app.user.clear(within(dialog).getByLabelText("Documents"));
		await app.user.click(within(dialog).getByRole("button", { name: "Save plan" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(plansState.sent[0]?.body).toEqual({
			name: "Starter",
			description: "₹1,499 / month · billed by you",
			max_knowledge_bases: 1,
			monthly_questions: 1000,
			max_documents: null,
			max_widget_keys: 1,
			max_upload_mb: null,
			max_document_tokens: null,
			count_cached_answers: true,
			...BIZ3_DEFAULTS,
		});
	});

	it("size caps: live validation, parsed on save, shown on the card", async () => {
		const app = await openPlans();
		await app.user.click(screen.getByRole("button", { name: "New plan" }));
		const dialog = await screen.findByRole("dialog", { name: "New plan" });
		await app.user.type(within(dialog).getByLabelText("Name"), "Small");
		await app.user.type(within(dialog).getByLabelText("Largest upload"), "0");
		await app.user.tab();
		expect(await within(dialog).findByText("Use a whole number of 1 or more, or leave it empty.")).toBeInTheDocument();
		await app.user.clear(within(dialog).getByLabelText("Largest upload"));
		await app.user.type(within(dialog).getByLabelText("Largest upload"), "10");
		await app.user.type(within(dialog).getByLabelText("Text per document"), "100,000");
		await app.user.click(within(dialog).getByRole("button", { name: "Create plan" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(plansState.sent[0]?.body).toMatchObject({ max_upload_mb: 10, max_document_tokens: 100000 });
		const made = await screen.findByRole("article", { name: /^Small/ });
		expect(within(made).getByText("10 MB")).toBeInTheDocument();
		expect(within(made).getByText("1,00,000 tokens")).toBeInTheDocument();
		expect(within(card("Starter")).getAllByText("Engine default")).toHaveLength(2);
	});

	it("count cached answers: on by default, switched off in the dialog, shown on the card", async () => {
		const app = await openPlans();
		await app.user.click(screen.getByRole("button", { name: "New plan" }));
		const dialog = await screen.findByRole("dialog", { name: "New plan" });
		const box = within(dialog).getByRole("checkbox", { name: "Count cached answers" });
		expect(box).toBeChecked();
		expect(within(dialog).getByText(/cached answers are free/)).toBeInTheDocument();
		await app.user.type(within(dialog).getByLabelText("Name"), "Free cache");
		await app.user.click(box);
		await app.user.click(within(dialog).getByRole("button", { name: "Create plan" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(plansState.sent[0]?.body).toMatchObject({ count_cached_answers: false });
		const made = await screen.findByRole("article", { name: /^Free cache/ });
		expect(within(made).getByText("Free")).toBeInTheDocument();
		expect(within(card("Starter")).getByText("Count as questions")).toBeInTheDocument();
	});

	it("make default, switch off and on through the menu", async () => {
		const app = await openPlans();
		await app.user.click(screen.getByRole("button", { name: "More actions for Pro" }));
		await app.user.click(await screen.findByRole("menuitem", { name: "Make default" }));
		expect(await screen.findByText("Pro is now the default plan")).toBeInTheDocument();
		expect(plansState.sent[0]?.body).toEqual({ is_default: true });
		await app.user.click(screen.getByRole("button", { name: "More actions for Standard" }));
		await app.user.click(await screen.findByRole("menuitem", { name: "Switch off" }));
		expect(await screen.findByText("Standard switched off")).toBeInTheDocument();
		expect(plansState.sent[1]?.body).toEqual({ is_active: false });
		await app.user.click(screen.getByRole("button", { name: "More actions for Diwali offer" }));
		expect(await screen.findByRole("menuitem", { name: "Make default" })).toHaveAttribute("aria-disabled", "true");
		await app.user.click(screen.getByRole("menuitem", { name: "Switch on" }));
		expect(await screen.findByText("Diwali offer switched on")).toBeInTheDocument();
	});

	it("delete: plan_in_use names the count; plan_is_default refuses; an unused plan goes", async () => {
		const app = await openPlans();
		await app.user.click(screen.getByRole("button", { name: "More actions for Pro" }));
		await app.user.click(await screen.findByRole("menuitem", { name: "Delete" }));
		let dialog = await screen.findByRole("alertdialog", { name: "Delete Pro?" });
		await app.user.click(within(dialog).getByRole("button", { name: "Delete plan" }));
		expect(await within(dialog).findByText("2 customers use this plan; move them first.")).toBeInTheDocument();
		await app.user.click(within(dialog).getByRole("button", { name: "Cancel" }));

		// The default plan's menu keeps Delete; the server answers plan_is_default.
		await app.user.click(screen.getByRole("button", { name: "More actions for Starter" }));
		await app.user.click(await screen.findByRole("menuitem", { name: "Delete" }));
		dialog = await screen.findByRole("alertdialog", { name: "Delete Starter?" });
		await app.user.click(within(dialog).getByRole("button", { name: "Delete plan" }));
		expect(await within(dialog).findByText("The default plan can't be deleted or switched off.")).toBeInTheDocument();
		await app.user.click(within(dialog).getByRole("button", { name: "Cancel" }));

		await app.user.click(screen.getByRole("button", { name: "More actions for Diwali offer" }));
		await app.user.click(await screen.findByRole("menuitem", { name: "Delete" }));
		dialog = await screen.findByRole("alertdialog", { name: "Delete Diwali offer?" });
		await app.user.click(within(dialog).getByRole("button", { name: "Delete plan" }));
		await waitFor(() => expect(screen.getAllByTestId("plan-card")).toHaveLength(5));
	});

	// BIZ3 plan
	it("business section: every flag listed, a simple-dashboard plan round-trips and shows on its card", async () => {
		const app = await openPlans();
		await app.user.click(screen.getByRole("button", { name: "New plan" }));
		const dialog = await screen.findByRole("dialog", { name: "New plan" });
		expect(within(dialog).getByText("What businesses on this plan see")).toBeInTheDocument();
		for (const label of [
			"Advanced settings",
			"AI model choice",
			"Search tuning",
			"API keys and agents",
			"Webhooks",
			"Quality tests",
			"API documentation",
			"Detailed usage tables",
			"Website widget: code only",
		]) {
			expect(within(dialog).getByRole("checkbox", { name: label }), label).toBeInTheDocument();
		}
		expect(within(dialog).getByRole("checkbox", { name: "Advanced settings" })).toBeChecked();
		expect(within(dialog).getByRole("checkbox", { name: "Website widget: code only" })).not.toBeChecked();
		await app.user.type(within(dialog).getByLabelText("Name"), "Clinic basic");
		await app.user.click(within(dialog).getByRole("checkbox", { name: "Advanced settings" }));
		await app.user.click(within(dialog).getByRole("checkbox", { name: "Website widget: code only" }));
		await app.user.type(within(dialog).getByLabelText("Pages we read from their website"), "50");
		await app.user.click(within(dialog).getByRole("button", { name: "Create plan" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(plansState.sent[0]?.body).toMatchObject({
			website_learning_enabled: true,
			website_pages_max: 50,
			dashboard_features: { ...DEFAULT_FLAGS, advanced: false, widget_code_only: true },
		});
		const made = await screen.findByRole("article", { name: /^Clinic basic/ });
		expect(within(made).getByTestId("plan-simple")).toHaveTextContent("Simple dashboard");
		expect(within(made).getByTestId("plan-website")).toHaveTextContent("Up to 50 website pages");
		expect(within(card("Starter")).getByTestId("plan-website")).toHaveTextContent("Unlimited");
		expect(within(card("Starter")).queryByTestId("plan-simple")).toBeNull();
	});

	it("business section: website learning off, pages validated like the other limits", async () => {
		const app = await openPlans();
		await app.user.click(screen.getByRole("button", { name: "Edit Pro" }));
		const dialog = await screen.findByRole("dialog", { name: "Edit plan · Pro" });
		await app.user.click(
			within(dialog).getByRole("checkbox", { name: "Let businesses on this plan learn from their website" }),
		);
		await app.user.type(within(dialog).getByLabelText("Pages we read from their website"), "0");
		await app.user.click(within(dialog).getByRole("button", { name: "Save plan" }));
		expect(await within(dialog).findByText("Use a whole number of 1 or more, or leave it empty.")).toBeInTheDocument();
		await app.user.clear(within(dialog).getByLabelText("Pages we read from their website"));
		await app.user.click(within(dialog).getByRole("button", { name: "Save plan" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(plansState.sent[0]?.body).toMatchObject({ website_learning_enabled: false, website_pages_max: null });
		expect(within(card("Pro")).getByTestId("plan-website")).toHaveTextContent("No website learning");
	});
	// /BIZ3 plan

	it("a tenant never sees Plans", async () => {
		sessionStorage.setItem(RT_KEY, rt(1));
		renderApp("/admin/plans");
		expect(await screen.findByRole("heading", { name: "Not available for your account" })).toBeInTheDocument();
	});
});
