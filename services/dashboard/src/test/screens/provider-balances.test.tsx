import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { bodyFrom } from "@/features/costs/ProviderDialog";
import { messageFor } from "@/lib/api/error-messages";
import { ApiError } from "@/lib/api/errors";
import { RT_KEY } from "@/lib/auth/storage";
import { setCurrency, setEngineRate } from "@/lib/format/currency";
import { BASE, rt, SV, tokenResponse } from "../msw/data";
import { CONSUMPTION_PLATFORM, consumption, providerCard, providerLine, providerState } from "../msw/handlers/usage";
import { server } from "../msw/server";
import { renderApp } from "../render";

afterEach(() => {
	server.events.removeAllListeners();
	setCurrency("INR");
	setEngineRate(null);
});

function signedInAsSuperAdmin() {
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

const card = (provider: string) => {
	const el = screen.getAllByTestId("provider-card").find((c) => c.getAttribute("data-provider") === provider);
	if (!el) throw new Error(`no ${provider} card`);
	return el;
};

describe("Provider balances panel", () => {
	it("renders every status, with what each provider says", async () => {
		signedInAsSuperAdmin();
		providerState.cards = [
			providerCard({
				provider: "openrouter",
				label: "OpenRouter",
				status: "ok",
				remaining_usd_micro: 41_200_000,
				limit_usd_micro: 100_000_000,
				limit_reset: "monthly",
				resets_at: "2026-10-01T00:00:00Z",
				spend_today_usd_micro: 1_250_000,
				spend_week_usd_micro: 6_000_000,
				spend_month_usd_micro: 12_340_000,
				account_credits_usd_micro: 60_000_000,
			}),
			providerCard({
				provider: "openai",
				label: "OpenAI",
				status: "unknown",
				admin_key_set: true,
				admin_key_source: "stored",
				spend_month_usd_micro: 12_340_000,
				official_spend: [
					{ day: "2026-09-02", model: "gpt-4o-mini", usd_micro: 1_230_000 },
					{ day: "2026-09-03", model: "gpt-4o", usd_micro: 11_110_000 },
				],
			}),
			providerCard({
				provider: "manual",
				id: "m1",
				label: "Groq",
				status: "low",
				remaining_usd_micro: 3_200_000,
				manual_balance_usd_micro: 10_000_000,
				manual_remaining_usd_micro: 3_200_000,
				manual_since: "2026-09-01",
				unpriced_requests: 4,
				spend_tracked: true,
				low_threshold_usd_micro: 5_000_000,
			}),
			providerCard({
				provider: "manual",
				id: "m2",
				label: "Gemini",
				status: "empty",
				remaining_usd_micro: 0,
				manual_balance_usd_micro: 5_000_000,
				manual_remaining_usd_micro: 0,
				manual_since: "2026-09-01",
			}),
			providerCard({ provider: "anthropic", label: "Anthropic" }),
		];
		renderApp("/admin/costs");
		await screen.findByTestId("provider-balances");
		const statuses = (await screen.findAllByTestId("provider-status")).map((s) => s.getAttribute("data-status"));
		expect(statuses).toEqual(["ok", "unknown", "low", "empty", "not_configured"]);

		const router = within(card("openrouter"));
		expect(router.getByTestId("provider-left")).toHaveTextContent("$41.20");
		expect(card("openrouter")).toHaveTextContent("Limit $100.00");
		expect(card("openrouter")).toHaveTextContent("resets monthly");
		expect(card("openrouter")).toHaveTextContent("Account credits");

		const openai = within(card("openai"));
		expect(openai.getByTestId("provider-left")).toHaveTextContent("unknown");
		expect(openai.getByTestId("provider-by-model")).toHaveTextContent("gpt-4o");
		expect(card("openai")).toHaveTextContent("Key saved");

		const groq = screen.getAllByTestId("provider-card").find((c) => c.textContent?.includes("Groq"));
		expect(groq).toHaveTextContent("Estimated left");
		expect(groq).toHaveTextContent("+4 requests with an unknown price");
		expect(groq).toHaveTextContent("Alert below $5.00");

		expect(card("anthropic")).toHaveTextContent("Not set up");
	});

	it("an admin key is a password field that never comes pre-filled; saving it shows Key saved", async () => {
		signedInAsSuperAdmin();
		const { user } = renderApp("/admin/costs");
		await screen.findByTestId("provider-balances");
		await user.click(within(card("openai")).getByRole("button", { name: "Edit OpenAI" }));
		const key = await screen.findByLabelText("Admin key");
		expect(key).toHaveAttribute("type", "password");
		expect(key).toHaveValue("");
		await user.type(key, "sk-admin-SECRET");
		await user.click(screen.getByRole("button", { name: "Save" }));
		await waitFor(() => expect(card("openai")).toHaveTextContent("Key saved"));
		expect(providerState.puts.at(-1)).toMatchObject({ admin_key: "sk-admin-SECRET" });
		expect(document.body.textContent).not.toContain("sk-admin-SECRET");

		// Opening it again: still empty, and it says a key is saved.
		await user.click(within(card("openai")).getByRole("button", { name: "Edit OpenAI" }));
		expect(await screen.findByLabelText("Admin key")).toHaveValue("");
		expect(screen.getByText(/A key is saved/)).toBeInTheDocument();
	});

	it("a key from the engine's environment cannot be edited or removed here, and says so", async () => {
		signedInAsSuperAdmin();
		const openai = providerState.cards.find((c) => c.provider === "openai");
		if (!openai) throw new Error("no openai card");
		openai.admin_key_source = "env";
		openai.admin_key_set = true;
		openai.status = "unknown";
		const { user } = renderApp("/admin/costs");
		await screen.findByTestId("provider-balances");
		expect(card("openai")).toHaveTextContent("Key from the engine settings");
		await user.click(within(card("openai")).getByRole("button", { name: "Edit OpenAI" }));
		expect(await screen.findByText(/set in the engine's environment file/)).toBeInTheDocument();
		expect(screen.queryByLabelText("Admin key")).not.toBeInTheDocument();
		expect(screen.queryByRole("button", { name: "Remove key" })).not.toBeInTheDocument();
	});

	it("adds a manual balance", async () => {
		signedInAsSuperAdmin();
		const { user } = renderApp("/admin/costs");
		await screen.findByTestId("provider-balances");
		await user.click(screen.getByRole("button", { name: "Add a provider balance" }));
		await user.type(await screen.findByLabelText("Name"), "Groq");
		await user.type(screen.getByLabelText("Balance left, in dollars"), "50.00");
		await user.click(screen.getByRole("button", { name: "Save" }));
		await waitFor(() =>
			expect(screen.getAllByTestId("provider-card").some((c) => c.textContent?.includes("Groq"))).toBe(true),
		);
		expect(providerState.puts.at(-1)).toMatchObject({ label: "Groq", manual_balance_usd: "50.00" });
	});

	it("refreshing twice says to try again in a minute", async () => {
		signedInAsSuperAdmin();
		const { user } = renderApp("/admin/costs");
		await screen.findByTestId("provider-balances");
		const button = within(card("openrouter")).getByRole("button", { name: "Refresh OpenRouter" });
		await user.click(button);
		await screen.findByText("OpenRouter refreshed");
		await user.click(button);
		expect(await screen.findByText(/Try again in a minute/)).toBeInTheDocument();
	});
});

describe("Provider chip in the header", () => {
	const line = (over: Parameters<typeof providerCard>[0]) => providerLine(providerCard(over));

	it("OpenRouter only: exactly the balance chip it always had", async () => {
		signedInAsSuperAdmin();
		consumption.current = {
			...CONSUMPTION_PLATFORM,
			providers: {
				worst: line({ provider: "openrouter", label: "OpenRouter", status: "ok", remaining_usd_micro: 41_200_000 }),
				items: [line({ provider: "openrouter", label: "OpenRouter", status: "ok", remaining_usd_micro: 41_200_000 })],
			},
		};
		renderApp("/admin/costs");
		expect(await screen.findByTestId("openrouter-balance")).toHaveTextContent("Balance");
		expect(screen.queryByTestId("provider-chip")).toBeNull();
	});

	it("picks the worst provider when more than OpenRouter is set up", async () => {
		signedInAsSuperAdmin();
		const ok = line({ provider: "openrouter", label: "OpenRouter", status: "ok", remaining_usd_micro: 41_200_000 });
		const low = line({ provider: "openai", label: "OpenAI", status: "low", remaining_usd_micro: 3_200_000 });
		consumption.current = { ...CONSUMPTION_PLATFORM, providers: { worst: low, items: [ok, low] } };
		renderApp("/admin/costs");
		const chip = await screen.findByTestId("provider-chip");
		expect(chip).toHaveTextContent("OpenAI $3.20 left");
		expect(chip).toHaveAttribute("data-low", "true");
		expect(screen.queryByTestId("openrouter-balance")).toBeNull();
	});

	it("an unknown balance reads check balance; an empty one says out of credit", async () => {
		signedInAsSuperAdmin();
		const unknown = line({ provider: "openai", label: "OpenAI", status: "unknown" });
		consumption.current = { ...CONSUMPTION_PLATFORM, providers: { worst: unknown, items: [unknown] } };
		const first = renderApp("/admin/costs");
		expect(await screen.findByTestId("provider-chip")).toHaveTextContent("OpenAI: check balance");
		first.unmount();
		const empty = line({ provider: "manual", label: "Groq", status: "empty", remaining_usd_micro: 0 });
		consumption.current = { ...CONSUMPTION_PLATFORM, providers: { worst: empty, items: [empty] } };
		renderApp("/admin/costs");
		expect(await screen.findByTestId("provider-chip")).toHaveTextContent("Groq: out of credit");
	});
});

describe("bodyFrom", () => {
	const base = { label: "", balance: "", balanceAt: "", note: "", threshold: "", key: "" };
	const flags = { removeKey: false, removeBalance: false };

	it("sends only what changed", () => {
		const p = providerCard({
			provider: "manual",
			id: "m1",
			label: "Groq",
			manual_balance_usd_micro: 50_000_000,
			manual_balance_at: "2026-09-01T00:00:00Z",
		});
		const same = { ...base, label: "Groq", balance: "50", balanceAt: "2026-09-01" };
		expect(bodyFrom(same, p, flags)).toEqual({ clear_admin_key: false, clear_manual: false });
		expect(bodyFrom({ ...same, balance: "42.5", balanceAt: "2026-09-02" }, p, flags)).toMatchObject({
			manual_balance_usd: "42.5",
			manual_balance_at: "2026-09-02T00:00:00Z",
		});
		expect(bodyFrom({ ...same, threshold: "5" }, p, flags)).toMatchObject({ low_threshold_usd: "5" });
		expect(bodyFrom({ ...same, balance: "" }, p, flags)).toMatchObject({ clear_manual: true });
	});

	it("a key is sent only when typed, and removal is explicit", () => {
		const p = providerCard({ provider: "openai", label: "OpenAI", admin_key_set: true });
		expect(bodyFrom({ ...base, label: "OpenAI" }, p, flags)).not.toHaveProperty("admin_key");
		expect(bodyFrom({ ...base, label: "OpenAI", key: " sk-x " }, p, flags)).toMatchObject({ admin_key: "sk-x" });
		expect(bodyFrom({ ...base, label: "OpenAI" }, p, { ...flags, removeKey: true })).toMatchObject({
			clear_admin_key: true,
		});
	});
});

describe("provider settings errors", () => {
	it("shows the reason the engine gives, with a fallback when it gives none", () => {
		const api = (detail: string) =>
			new ApiError({ status: 422, code: "invalid_provider_settings", detail, extra: {}, retryAfterS: null });
		expect(messageFor(api("A manual balance has no key.")).title).toBe("A manual balance has no key.");
		expect(messageFor(api("")).title).toMatch(/do not fit together/);
	});
});
