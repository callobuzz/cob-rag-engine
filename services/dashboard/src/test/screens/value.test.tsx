import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import type { Home } from "@/features/home/api";
import { RT_KEY } from "@/lib/auth/storage";
import { minorToInput, parseMajor } from "@/lib/format/money";
import { BASE, PRIYA, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { homeState } from "../msw/handlers/home";
import { KOTHRUD_ID } from "../msw/handlers/tenants";
import { CLINIC, reportFor, valueState } from "../msw/handlers/value";
import { server } from "../msw/server";
import { renderApp } from "../render";
import { MEMBER_SHREE, PATIENT, signedInAs } from "./assistant-utils";

type HomeValue = NonNullable<Home["value"]>;

const FORMULA = [
	"9 qualified leads × ₹1,500 = ₹13,500",
	"23 requests to talk to a person × ₹800 = ₹18,400",
	"412 questions answered × 3 min saved = 20 h 36 min × ₹250/h = ₹5,150",
];

async function openValue(path = "/value") {
	const app = renderApp(path);
	await screen.findByRole("heading", { level: 1, name: "Value report" });
	await screen.findByTestId("value-total");
	return app;
}

describe("the value report page", () => {
	it("the estimate with its formula, from the numbers the engine sent", async () => {
		signedInAs(PRIYA, SHREE);
		await openValue();
		expect(screen.getByTestId("value-total")).toHaveTextContent("₹37,050");
		expect(screen.getByText(/Estimated value, based on your settings/)).toBeInTheDocument();
		const lines = within(screen.getByTestId("value-formula"))
			.getAllByRole("listitem")
			.map((li) => li.textContent);
		expect(lines).toEqual(FORMULA);
	});

	it("calls leads that no rule rated plain leads, not qualified ones", async () => {
		valueState.inputs = { leads_qualified: 40, leads_unrated: 40 };
		signedInAs(PRIYA, SHREE);
		await openValue();
		expect(within(screen.getByTestId("value-formula")).getByTestId("formula-leads")).toHaveTextContent(
			"40 leads × ₹1,500 = ₹60,000",
		);
		expect(screen.getByTestId("count-leads-qualified")).toHaveTextContent("Leads");
		expect(screen.getByTestId("count-leads-qualified")).not.toHaveTextContent("Qualified");
	});

	it("the counts, what people asked and what it could not answer", async () => {
		signedInAs(PRIYA, SHREE);
		await openValue();
		expect(screen.getByTestId("count-conversations")).toHaveTextContent("188");
		expect(screen.getByTestId("count-answered")).toHaveTextContent("412");
		expect(screen.getByTestId("count-after-hours")).toHaveTextContent("61");
		expect(screen.getByTestId("count-first-reply")).toHaveTextContent("under a second");
		expect(within(screen.getByTestId("top-questions")).getByText(/root canal ka kitna charge hai/)).toBeInTheDocument();
		const unanswered = screen.getByTestId("unanswered");
		expect(unanswered).toHaveTextContent("do you take cards");
		const add = within(unanswered).getByRole("link", { name: "Add an answer" });
		expect(add.getAttribute("href")).toContain("/knowledge");
		expect(new URL(add.getAttribute("href") ?? "", "http://x").searchParams.get("question")).toBe("do you take cards");
	});

	it("says when the numbers are only suggestions", async () => {
		signedInAs(PRIYA, SHREE);
		await openValue();
		expect(screen.getByText(/suggested ones are used/)).toBeInTheDocument();
	});

	it("never says ROI, guarantee or promise", async () => {
		signedInAs(PRIYA, SHREE);
		await openValue();
		const text = document.body.textContent ?? "";
		expect(text).not.toMatch(/\bROI\b|guarantee|promise/i);
	});

	it("shows from when after-hours answers are counted, when the engine says so", async () => {
		valueState.afterHoursSince = "2026-10-03";
		signedInAs(PRIYA, SHREE);
		await openValue();
		expect(screen.getByTestId("after-hours-since")).toHaveTextContent("3 Oct 2026");
	});

	it("another month through the month picker", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = await openValue();
		await user.selectOptions(screen.getByLabelText("Month"), "2026-09");
		await waitFor(() => expect(valueState.asked).toContain("2026-09"));
		await waitFor(() => expect(screen.getByRole("heading", { name: "Estimated value" })).toBeInTheDocument());
		expect(screen.getByLabelText("Month")).toHaveValue("2026-09");
	});

	it("a closed month opened from the e-mail still offers a way back to this month", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = await openValue("/value?month=2026-09");
		const picker = screen.getByLabelText("Month");
		expect(picker).toHaveValue("2026-09");
		const back = within(picker).getByRole("option", { name: "This month so far" });
		await user.selectOptions(picker, back);
		await waitFor(() => expect(screen.getByLabelText("Month")).toHaveValue(""));
	});

	it("the counts are terms followed by their values, for screen readers", async () => {
		signedInAs(PRIYA, SHREE);
		await openValue();
		const group = screen.getByTestId("count-conversations");
		expect(group.querySelector("dt")?.nextElementSibling?.tagName).toBe("DD");
	});

	it("an owner changes the values: rupees in, paise out, and the estimate moves", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = await openValue();
		await user.click(screen.getByTestId("value-change"));
		const dialog = await screen.findByRole("dialog", { name: "Your values" });
		const lead = within(dialog).getByLabelText("What a lead is worth");
		await user.clear(lead);
		await user.type(lead, "2,000");
		const staff = within(dialog).getByLabelText("What an hour of staff time costs");
		await user.clear(staff);
		await user.type(staff, "300.50");
		await user.click(within(dialog).getByRole("button", { name: "Save values" }));
		await waitFor(() => expect(valueState.puts).toHaveLength(1));
		expect(valueState.puts[0]).toMatchObject({
			lead_value_minor: 200_000,
			handoff_value_minor: 80_000,
			staff_hour_minor: 30_050,
			minutes_per_answer: 3,
			email_enabled: true,
		});
		await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
		await waitFor(() =>
			expect(screen.getByTestId("value-total")).toHaveTextContent(
				`₹${((9 * 200_000 + 23 * 80_000 + Math.floor((1236 * 30_050) / 60)) / 100).toLocaleString("en-IN")}`,
			),
		);
	});

	it("the suggested values for this kind of business fill the form and are remembered", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = await openValue();
		await user.click(screen.getByTestId("value-change"));
		const dialog = await screen.findByRole("dialog", { name: "Your values" });
		const lead = within(dialog).getByLabelText("What a lead is worth");
		await user.clear(lead);
		await user.type(lead, "5");
		await user.click(within(dialog).getByRole("button", { name: "Use suggested values for clinics" }));
		expect(lead).toHaveValue("1500");
		await user.click(within(dialog).getByRole("button", { name: "Save values" }));
		await waitFor(() => expect(valueState.puts).toHaveLength(1));
		expect(valueState.puts[0]).toMatchObject({ lead_value_minor: 150_000, industry_defaults: "clinic" });
	});

	it("refuses a bad amount and a bad number of minutes before sending", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = await openValue();
		await user.click(screen.getByTestId("value-change"));
		const dialog = await screen.findByRole("dialog", { name: "Your values" });
		const lead = within(dialog).getByLabelText("What a lead is worth");
		await user.clear(lead);
		await user.type(lead, "abc");
		const minutes = within(dialog).getByLabelText("Minutes saved per answered question");
		await user.clear(minutes);
		await user.type(minutes, "121");
		await user.click(within(dialog).getByRole("button", { name: "Save values" }));
		expect(await within(dialog).findByText("Enter an amount like 1,500 or 1500.50.")).toBeInTheDocument();
		expect(within(dialog).getByText("Enter a whole number from 0 to 120.")).toBeInTheDocument();
		expect(valueState.puts).toHaveLength(0);
	});

	it("a member reads the page but has no way to change the values", async () => {
		signedInAs(PRIYA, MEMBER_SHREE);
		await openValue();
		expect(screen.queryByTestId("value-change")).not.toBeInTheDocument();
		expect(screen.getByText("Only an owner can change these values.")).toBeInTheDocument();
	});

	it("the printable page opens in a new tab", async () => {
		signedInAs(PRIYA, SHREE);
		let requested = "";
		server.events.on("request:start", ({ request }) => {
			if (request.url.includes("format=html")) requested = request.url;
		});
		const written: string[] = [];
		const tab = { document: { open() {}, write: (h: string) => written.push(h), close() {} }, close() {}, print() {} };
		const opened = vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
		const { user } = await openValue();
		await user.click(screen.getByTestId("value-print"));
		await waitFor(() => expect(written.join("")).toContain("Estimated value, based on your settings"));
		expect(opened).toHaveBeenCalledWith("", "_blank");
		expect(requested).toContain("month=2026-10");
		server.events.removeAllListeners();
	});

	it("a month the engine refuses says why", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/value?month=2026-12");
		expect(await screen.findByText(/value month invalid|That month/i)).toBeInTheDocument();
	});
});

describe("the Home value card", () => {
	const card = (over: Partial<HomeValue> = {}) => ({ ...homeValue(), ...over });

	function homeValue(): HomeValue {
		return {
			month_to_date: {
				total_minor: 3_705_000,
				currency: "INR" as const,
				answered: 412,
				leads_qualified: 9,
				handoffs: 23,
			},
			saved_settings: true,
			last_month_total_minor: 2_500_000,
		};
	}

	it("this month so far, last month and a way in to the full report", async () => {
		homeState.over[PATIENT] = { value: card() };
		signedInAs(PRIYA, SHREE);
		const { user, pathname } = renderApp("/");
		const tile = await screen.findByTestId("home-value");
		expect(within(tile).getByTestId("home-value-total")).toHaveTextContent("₹37,050");
		expect(screen.getByText("Estimated, based on your settings")).toBeInTheDocument();
		expect(tile).toHaveTextContent("Last month: ₹25,000");
		expect(screen.queryByTestId("home-value-set")).not.toBeInTheDocument();
		await user.click(within(tile).getByRole("link", { name: "See the full report" }));
		await screen.findByRole("heading", { level: 1, name: "Value report" });
		expect(pathname()).toBe("/value");
	});

	it("asks an owner to set their values while only the suggestions are in use", async () => {
		homeState.over[PATIENT] = { value: card({ saved_settings: false, last_month_total_minor: null }) };
		signedInAs(PRIYA, SHREE);
		renderApp("/");
		const prompt = await screen.findByTestId("home-value-set");
		expect(within(prompt).getByRole("link", { name: "Set your values" })).toBeInTheDocument();
	});

	it("a member sees the card without the prompt link", async () => {
		homeState.over[PATIENT] = { value: card({ saved_settings: false }) };
		signedInAs(PRIYA, MEMBER_SHREE);
		renderApp("/");
		const prompt = await screen.findByTestId("home-value-set");
		expect(within(prompt).queryByRole("link")).not.toBeInTheDocument();
	});

	it("no card when the provider switched the value report off", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp("/");
		await screen.findByTestId("assistant-status");
		expect(screen.queryByTestId("home-value")).not.toBeInTheDocument();
	});
});

describe("the deployer's Value tab", () => {
	it("shows the business's report exactly as its owner sees it, read only", async () => {
		sessionStorage.setItem(RT_KEY, rt(1));
		server.use(
			http.post(`${BASE}/api/v1/auth/refresh`, () =>
				HttpResponse.json(tokenResponse({ user: SV, tenant: null, tenants: [] }, 2)),
			),
			http.get(`${BASE}/api/v1/auth/me`, () =>
				HttpResponse.json({ user: SV, tenant: null, tenants: [], session_id: "s" }),
			),
		);
		renderApp(`/admin/customers/${KOTHRUD_ID}?tab=value`);
		const tab = await screen.findByTestId("admin-value");
		await within(tab).findByTestId("value-total");
		expect(within(tab).getByTestId("value-total")).toHaveTextContent("₹37,050");
		expect(within(tab).queryByRole("link", { name: "Add an answer" })).not.toBeInTheDocument();
		expect(valueState.adminAsked.at(-1)?.tenant).toBe(KOTHRUD_ID);
	});
});

describe("rupees and paise", () => {
	it("what a person types becomes whole paise, and back", () => {
		expect(parseMajor("1,500")).toBe(150_000);
		expect(parseMajor("1500.50")).toBe(150_050);
		expect(parseMajor("₹ 250")).toBe(25_000);
		expect(parseMajor("abc")).toBeNull();
		expect(minorToInput(150_050)).toBe("1500.50");
	});

	it("the demo numbers follow the engine's formula", () => {
		const r = reportFor("2026-10", true);
		expect(r.breakdown.total_minor).toBe(
			9 * CLINIC.lead_value_minor + 23 * CLINIC.handoff_value_minor + Math.floor((1236 * CLINIC.staff_hour_minor) / 60),
		);
	});
});
