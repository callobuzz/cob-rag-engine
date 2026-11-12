import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { agreementBody, agreementValid } from "@/features/billing/accounts/AgreedField";
import { istDay, startOfDayIst } from "@/features/billing/pricing/ScheduleDialog";
import { capFromText } from "@/features/plan/SpendCapCard";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, PRIYA, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { billingState, noticeState } from "../msw/handlers/billing";
import { createSettingsMock } from "../msw/handlers/settings";
import { capState } from "../msw/handlers/support";
import { MEHTA_ID } from "../msw/handlers/tenants";
import { server } from "../msw/server";
import { renderApp } from "../render";
import { MEMBER_SHREE, signedInAs } from "./assistant-utils";

const ownerView = (extra: Record<string, unknown> = {}) =>
	http.get(`${BASE}/api/v1/billing/account`, () =>
		HttpResponse.json({
			tenant_id: SHREE.id,
			plan: { id: "plan-starter", name: "Growth" },
			currency: "INR",
			money_visible: true,
			payment_type: "prepaid",
			usage_mode: "per_question",
			byok_mode: "free",
			subscription_fee_minor: 99_900,
			billing_period: "monthly",
			per_question_minor: 200,
			byok_per_question_minor: 0,
			overage: "block",
			overage_per_question_minor: 0,
			included_credit_minor: 50_000,
			credit_limit_minor: 0,
			exempt: false,
			balance_minor: 300_000,
			available_minor: 300_000,
			add_ons: [],
			paused: false,
			pause_reason: null,
			alert_level: null,
			billed: true,
			usage_this_period: { questions: 1500, charged_minor: 120_000, unpriced: 0 },
			next_charge_on: "2026-10-12",
			trial_ends_at: null,
			statements: [],
			renewal: { on: "2026-10-12", amount_minor: 99_900, included_credit_minor: 50_000, new_price_from: null },
			projected_month_end_minor: 300_000,
			price_changes: [],
			spend_cap: { cap_minor: null, used_minor: 120_000, max_minor: 500_000, state: "ok" },
			...extra,
		}),
	);

describe("helpers", () => {
	it("a typed cap in rupees becomes paise and is checked against the provider's ceiling", () => {
		expect(capFromText("2,000", "INR", 500_000, "Sahayak AI")).toEqual({ minor: 200_000 });
		expect(capFromText("2000.50", "INR", null, "Sahayak AI")).toEqual({ minor: 200_050 });
		expect(capFromText("abc", "INR", null, "Sahayak AI")).toMatchObject({ error: expect.stringContaining("amount") });
		expect(capFromText("0", "INR", null, "Sahayak AI")).toMatchObject({
			error: expect.stringContaining("more than zero"),
		});
		expect(capFromText("6,000", "INR", 500_000, "Sahayak AI")).toMatchObject({
			error: "That is above the highest cap Sahayak AI allows (₹5,000).",
		});
	});

	it("a start date is an Indian midnight and the day is read in India", () => {
		expect(startOfDayIst("2026-11-05")).toBe("2026-11-05T00:00:00+05:30");
		expect(istDay("2026-11-04T18:30:00Z")).toBe("2026-11-05");
	});

	it("an agreement needs ten characters and only then travels with the request", () => {
		expect(agreementValid({ agreed: false, reason: "" })).toBe(true);
		expect(agreementValid({ agreed: true, reason: "ok" })).toBe(false);
		expect(agreementBody({ agreed: true, reason: "  Asked on a call, 5 Oct  " })).toEqual({
			business_agreed: true,
			reason: "Asked on a call, 5 Oct",
		});
		expect(agreementBody({ agreed: false, reason: "ignored" })).toEqual({});
	});
});

describe("the owner's Plan page: no surprises", () => {
	it("shows the next renewal with the exact amount and where the month is heading", async () => {
		server.use(ownerView());
		signedInAs(PRIYA, SHREE);
		renderApp("/plan");
		expect(await screen.findByTestId("renewal-line")).toHaveTextContent("Renews on 12 October 2026 for ₹999");
		expect(screen.getByTestId("renewal-card")).toHaveTextContent("₹500 of credit is added for the new period.");
		expect(screen.getByTestId("projection-line")).toHaveTextContent("About ₹3,000 this month at this pace");
		expect(screen.queryByTestId("renewal-new-price")).toBeNull();
	});

	it("says when the new price starts with this renewal", async () => {
		server.use(
			ownerView({
				renewal: {
					on: "2026-11-12",
					amount_minor: 129_900,
					included_credit_minor: 50_000,
					new_price_from: "2026-11-12",
				},
			}),
		);
		signedInAs(PRIYA, SHREE);
		renderApp("/plan");
		expect(await screen.findByTestId("renewal-new-price")).toHaveTextContent("New price from this renewal.");
		expect(screen.getByTestId("renewal-line")).toHaveTextContent("₹1,299");
	});

	it("lists a coming price change with its date and what moves, never before it applies", async () => {
		server.use(
			ownerView({
				price_changes: [
					{
						effective_on: "2026-11-12",
						plan_name: "Growth",
						changes: [
							{ label: "Plan fee", old_minor: 99_900, new_minor: 129_900 },
							{ label: "Price per question", old_minor: 200, new_minor: 300 },
						],
					},
				],
			}),
		);
		signedInAs(PRIYA, SHREE);
		renderApp("/plan");
		const banner = await screen.findByTestId("price-changes");
		expect(banner).toHaveTextContent("Growth: from your renewal on 12 November 2026");
		expect(banner).toHaveTextContent("Plan fee: ₹999 → ₹1,299");
		expect(banner).toHaveTextContent("Price per question: ₹2 → ₹3");
		expect(banner).toHaveTextContent("Nothing changes until your next renewal");
	});

	it("an owner sets a cap in rupees: paise are sent, the card shows it and the state", async () => {
		server.use(ownerView());
		signedInAs(PRIYA, SHREE);
		const { user } = renderApp("/plan");
		const card = await screen.findByTestId("spend-cap-card");
		expect(card).toHaveTextContent("₹1,200 spent on usage this month");
		await user.click(within(card).getByTestId("spend-cap-edit"));
		await user.type(within(card).getByLabelText("Cap in rupees"), "2,000");
		await user.click(within(card).getByRole("button", { name: "Save" }));
		await waitFor(() => expect(capState.puts).toEqual([200_000]));
	});

	it("a cap above the provider's ceiling is refused before anything is sent", async () => {
		server.use(ownerView());
		signedInAs(PRIYA, SHREE);
		const { user } = renderApp("/plan");
		const card = await screen.findByTestId("spend-cap-card");
		await user.click(within(card).getByTestId("spend-cap-edit"));
		await user.type(within(card).getByLabelText("Cap in rupees"), "9,000");
		await user.click(within(card).getByRole("button", { name: "Save" }));
		expect(await screen.findByText(/above the highest cap .* allows \(₹5,000\)/)).toBeInTheDocument();
		expect(capState.puts).toEqual([]);
	});

	it("shows how close the cap is, what happens at it, and lets the owner remove it", async () => {
		capState.cap_minor = 140_000;
		server.use(
			ownerView({ spend_cap: { cap_minor: 140_000, used_minor: 120_000, max_minor: 500_000, state: "near" } }),
		);
		signedInAs(PRIYA, SHREE);
		const { user } = renderApp("/plan");
		const card = await screen.findByTestId("spend-cap-card");
		expect(card).toHaveTextContent("₹1,200 of ₹1,400 this month");
		expect(within(card).getByTestId("spend-cap-state")).toHaveTextContent("You have used most of your cap.");
		expect(card).toHaveTextContent("At the cap your assistant switches to Sahayak AI's fallback model");
		await user.click(within(card).getByTestId("spend-cap-remove"));
		await waitFor(() => expect(capState.puts).toEqual([null]));
	});

	it("the bar is a progressbar, the refusal is linked to the field and paise are kept", async () => {
		capState.cap_minor = 140_050;
		server.use(ownerView({ spend_cap: { cap_minor: 140_050, used_minor: 70_025, max_minor: 500_000, state: "ok" } }));
		signedInAs(PRIYA, SHREE);
		const { user } = renderApp("/plan");
		const card = await screen.findByTestId("spend-cap-card");
		expect(within(card).getByRole("progressbar", { name: "Monthly spending cap" })).toHaveAttribute(
			"aria-valuenow",
			"50",
		);
		await user.click(within(card).getByTestId("spend-cap-edit"));
		const input = within(card).getByLabelText("Cap in rupees");
		expect(input).toHaveValue("1400.50");
		await user.clear(input);
		await user.type(input, "9,000");
		await user.click(within(card).getByRole("button", { name: "Save" }));
		await waitFor(() => expect(input).toHaveAttribute("aria-invalid", "true"));
		const described = input.getAttribute("aria-describedby") ?? "";
		expect(described).toContain("-error");
		const errorId = described.split(" ").find((x) => x.endsWith("-error")) ?? "";
		expect(document.getElementById(errorId)).toHaveTextContent(/above the highest cap/);
	});

	it("a member reads the cap but cannot change it", async () => {
		server.use(ownerView({ spend_cap: { cap_minor: 140_000, used_minor: 20_000, max_minor: null, state: "ok" } }));
		signedInAs(PRIYA, MEMBER_SHREE);
		renderApp("/plan");
		const card = await screen.findByTestId("spend-cap-card");
		expect(card).toHaveTextContent("₹200 of ₹1,400 this month");
		expect(within(card).queryByTestId("spend-cap-edit")).toBeNull();
		expect(card).toHaveTextContent("Only an owner can change the cap.");
	});

	it("a member without money rights sees none of it", async () => {
		server.use(
			ownerView({
				money_visible: false,
				renewal: null,
				projected_month_end_minor: null,
				spend_cap: null,
				balance_minor: null,
				available_minor: null,
			}),
		);
		signedInAs(PRIYA, MEMBER_SHREE);
		renderApp("/plan");
		await screen.findByTestId("plan-member-note");
		expect(screen.queryByTestId("renewal-card")).toBeNull();
		expect(screen.queryByTestId("spend-cap-card")).toBeNull();
		expect(screen.queryByTestId("price-changes")).toBeNull();
	});
});

// ---------------------------------------------------------------- the provider's pricing editor

function asAdmin() {
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

async function openStarterEditor() {
	asAdmin();
	const app = renderApp("/admin/billing?tab=pricing");
	const cards = await screen.findAllByTestId("pricing-card");
	const card = cards.find((c) => c.textContent?.includes("Starter")) as HTMLElement;
	await app.user.click(within(card).getByRole("button", { name: /Edit pricing/ }));
	const dialog = await screen.findByRole("dialog", { name: /Pricing for Starter/ });
	return { app, dialog };
}

describe("the pricing editor: a raise is announced first", () => {
	it("a rise on a plan with businesses opens the notice dialog and schedules it from a date at least the notice period away", async () => {
		const { app, dialog } = await openStarterEditor();
		const fee = within(dialog).getByLabelText(/Fee/i);
		await app.user.clear(fee);
		await app.user.type(fee, "1,299");
		await app.user.click(within(dialog).getByRole("button", { name: "Save pricing" }));
		const schedule = await screen.findByTestId("schedule-dialog");
		expect(screen.getByTestId("schedule-lead")).toHaveTextContent(/businesses? will pay more/);
		const date = within(schedule).getByLabelText("New price starts on") as HTMLInputElement;
		const earliest = date.min;
		expect(earliest).toMatch(/^\d{4}-\d{2}-\d{2}$/);
		expect(date.value).toBe(earliest); // defaults to the earliest allowed day
		// a day before the earliest is refused with a sentence, nothing is sent
		const puts = () => billingState.sent.filter((s) => s.method === "PUT");
		expect(puts()).toEqual([]);
		await app.user.clear(date);
		await app.user.type(date, "2020-01-01");
		await app.user.click(within(schedule).getByRole("button", { name: "Announce and schedule" }));
		expect(await within(schedule).findByText(/Choose .* or a later day/)).toBeInTheDocument();
		expect(puts()).toEqual([]);
		await app.user.clear(date);
		await app.user.type(date, earliest);
		await app.user.click(within(schedule).getByRole("button", { name: "Announce and schedule" }));
		await waitFor(() => expect(puts()).toHaveLength(1));
		expect(puts()[0]?.body).toMatchObject({ apply: "scheduled", effective_at: `${earliest}T00:00:00+05:30` });
		await waitFor(() => expect(noticeState.scheduled["plan-starter"]).toBeDefined());
	});

	it("a drop applies at once and nothing is scheduled", async () => {
		const { app, dialog } = await openStarterEditor();
		const fee = within(dialog).getByLabelText(/Fee/i);
		await app.user.clear(fee);
		await app.user.type(fee, "799");
		await app.user.click(within(dialog).getByRole("button", { name: "Save pricing" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(screen.queryByTestId("schedule-dialog")).toBeNull();
		expect(noticeState.scheduled["plan-starter"]).toBeUndefined();
		expect(billingState.sent.filter((s) => s.method === "PUT")[0]?.body).toMatchObject({ apply: "now" });
	});

	it("a scheduled change shows a chip and its date, and can be cancelled", async () => {
		asAdmin();
		billingState.versions["plan-starter"]?.[0];
		noticeState.scheduled["plan-starter"] = {
			...(billingState.versions["plan-starter"]?.[0] as NonNullable<(typeof billingState.versions)[string]>[number]),
			version: 2,
			is_current: false,
			subscription_fee_minor: 129_900,
			effective_at: "2026-11-04T18:30:00Z",
		};
		const app = renderApp("/admin/billing?tab=pricing");
		const chip = await screen.findByTestId("scheduled-chip");
		expect(chip).toHaveTextContent("Scheduled");
		expect(screen.getByTestId("scheduled-change")).toHaveTextContent("from 5 November 2026");
		await app.user.click(screen.getByTestId("scheduled-cancel"));
		await app.user.click(await screen.findByRole("button", { name: "Cancel the change" }));
		await waitFor(() => expect(noticeState.scheduled["plan-starter"]).toBeUndefined());
		await waitFor(() => expect(screen.queryByTestId("scheduled-chip")).toBeNull());
	});
});

// ---------------------------------------------------------------- one business

describe("one business: pending changes", () => {
	it("a raise waits for the next renewal: the card shows it and can cancel it", async () => {
		asAdmin();
		noticeState.pending[MEHTA_ID] = { overrides: { per_question_minor: 300 }, at: "2026-11-11T18:30:00Z" };
		const app = renderApp(`/admin/customers/${MEHTA_ID}?tab=billing`);
		const card = await screen.findByTestId("pending-change");
		expect(screen.getByText(/12 Nov 2026: this business will pay the new terms/)).toBeInTheDocument();
		expect(card).toHaveTextContent("Price per question: ₹1.50 → ₹3.00");
		await app.user.click(within(card).getByTestId("pending-cancel"));
		await app.user.click(await screen.findByRole("button", { name: "Cancel this change" }));
		await waitFor(() => expect(noticeState.pending[MEHTA_ID]).toBeUndefined());
	});
});

// ---------------------------------------------------------------- the provider's notice settings

describe("the provider's notice settings", () => {
	it("saves only the trust fields it owns, with the cap ceiling in minor units", async () => {
		asAdmin();
		const m = createSettingsMock();
		server.use(...m.handlers);
		const app = renderApp("/admin/branding");
		const card = await screen.findByTestId("notices-card");
		const renewal = within(card).getByLabelText("Renewal reminder");
		await app.user.clear(renewal);
		await app.user.type(renewal, "5");
		await app.user.type(within(card).getByLabelText("Highest spending cap (rupees)"), "5,000");
		await app.user.type(within(card).getByLabelText("Promised reply time (hours)"), "4");
		await app.user.click(within(card).getByRole("button", { name: "Save notice settings" }));
		await waitFor(() => expect(m.state.patches).toHaveLength(1));
		expect(m.state.patches[0]).toEqual({
			trust: {
				renewal_reminder_days: 5,
				price_change_notice_days: 30,
				spend_cap_max_minor: { INR: 500_000 },
				support_enabled: true,
				support_sla_hours: 4,
			},
		});
	});

	it("refuses numbers outside the allowed ranges without sending", async () => {
		asAdmin();
		const m = createSettingsMock();
		server.use(...m.handlers);
		const app = renderApp("/admin/branding");
		const card = await screen.findByTestId("notices-card");
		const notice = within(card).getByLabelText("Notice before a price rise");
		await app.user.clear(notice);
		await app.user.type(notice, "3");
		await app.user.click(within(card).getByRole("button", { name: "Save notice settings" }));
		expect(await within(card).findByText("Enter whole numbers in the ranges shown.")).toBeInTheDocument();
		expect(m.state.patches).toEqual([]);
	});
});
