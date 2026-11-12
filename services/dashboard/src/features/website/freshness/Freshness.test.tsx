import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { Principal } from "@/lib/auth/principal";
import { setAuthState } from "@/lib/auth/store";
import { BASE } from "@/test/msw/data";
import { resetWebsiteState, WEBSITE_KB, websiteHandlers, websiteState } from "@/test/msw/handlers/website";
import { server } from "@/test/msw/server";
import { WebsiteTab } from "../WebsiteTab";

const principal = (role: "owner" | "member"): Principal => ({
	auth: "session",
	view: "tenant",
	role,
	user: { id: "u1", email: "priya@shreedental.in", name: "Priya" },
	tenant: { id: "t1", name: "Shree Dental Clinic", status: "active", isOperator: false },
	superAdmin: false,
	tenants: [],
	sessionId: "s1",
	keyKind: null,
});

function renderTab(role: "owner" | "member" = "owner") {
	setAuthState({
		principal: principal(role),
		credential: { kind: "api_key", key: "tk_test", ragUrl: BASE, persistent: false },
	});
	const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
	const user = userEvent.setup();
	const utils = render(
		<QueryClientProvider client={queryClient}>
			<TooltipProvider>
				<WebsiteTab kbId={WEBSITE_KB} />
				<Toaster />
			</TooltipProvider>
		</QueryClientProvider>,
	);
	return { ...utils, user, queryClient };
}

beforeEach(() => {
	resetWebsiteState();
	server.use(...websiteHandlers);
});
afterEach(() => setAuthState({ principal: null, credential: null }));

const writes = () => websiteState.calls.filter((c) => c.method !== "GET");

describe("How often we check your website", () => {
	it("starts on every day, the recommended choice, with busy pages checked more often", async () => {
		renderTab();
		const select = await screen.findByLabelText("Check my website");
		expect(select).toHaveValue("daily");
		expect(within(select).getByRole("option", { name: "Every day (recommended)" })).toBeInTheDocument();
		expect(screen.getByRole("checkbox", { name: /check pages that change often more often/i })).toBeChecked();
	});

	it("saves the schedule an owner picks", async () => {
		const { user } = renderTab();
		const select = await screen.findByLabelText("Check my website");
		await user.selectOptions(select, "weekly");
		await waitFor(() =>
			expect(writes()).toContainEqual({ method: "PATCH", path: "/website", body: { refresh_mode: "weekly" } }),
		);
		await waitFor(() => expect(screen.getByLabelText("Check my website")).toHaveValue("weekly"));
	});

	it("turns the adaptive choice off and on", async () => {
		const { user } = renderTab();
		const box = await screen.findByRole("checkbox", { name: /check pages that change often/i });
		await user.click(box);
		await waitFor(() =>
			expect(writes()).toContainEqual({ method: "PATCH", path: "/website", body: { adaptive: false } }),
		);
		await waitFor(() => expect(box).not.toBeChecked());
	});

	it("never switches checking off, so the adaptive choice has nothing to adjust", async () => {
		const { user } = renderTab();
		await user.selectOptions(await screen.findByLabelText("Check my website"), "off");
		const box = await screen.findByRole("checkbox", { name: /check pages that change often/i });
		await waitFor(() => expect(box).toBeDisabled());
		expect(box).not.toBeChecked();
		expect(screen.getByText(/checking is switched off/i)).toBeInTheDocument();
	});

	it("shows a member the choice but does not let them change it", async () => {
		renderTab("member");
		const select = await screen.findByLabelText("Check my website");
		expect(select).toBeDisabled();
		expect(screen.getByRole("checkbox", { name: /check pages that change often/i })).toBeDisabled();
		expect(screen.getAllByText("Only owners can change this.").length).toBeGreaterThan(0);
		expect(writes()).toEqual([]);
	});
});

describe("Update instantly when you publish", () => {
	it("makes the link, shows it once with the steps, and forgets it after a reload", async () => {
		const first = renderTab();
		await first.user.click(await screen.findByRole("button", { name: "Create update link" }));
		const shown = await screen.findByTestId("push-link");
		expect(within(shown).getByText(/api\/v1\/public\/learn\/lk_/)).toBeInTheDocument();
		expect(within(shown).getByText(/not shown again/i)).toBeInTheDocument();
		const steps = screen.getByTestId("push-guides");
		expect(within(steps).queryByText(/YOUR-UPDATE-LINK/)).not.toBeInTheDocument();
		expect(writes()).toContainEqual({ method: "POST", path: "/website/learn-key", body: null });
		first.unmount();

		renderTab(); // a reload: the engine only says that a link exists
		await screen.findByText(/an update link is set up \(it ends in .{4}\)/i);
		expect(screen.queryByTestId("push-link")).not.toBeInTheDocument();
		expect(document.body.textContent ?? "").not.toMatch(/lk_pQ7x/);
		expect(within(screen.getByTestId("push-guides")).getAllByText(/YOUR-UPDATE-LINK/).length).toBeGreaterThan(0);
	});

	it("asks before replacing the link and says the old one stops working", async () => {
		const { user } = renderTab();
		await user.click(await screen.findByRole("button", { name: "Create update link" }));
		await user.click(await screen.findByRole("button", { name: "Create a new link" }));
		const dialog = await screen.findByRole("alertdialog");
		expect(within(dialog).getByText(/old link stops working at once/i)).toBeInTheDocument();
		expect(writes().filter((c) => c.path === "/website/learn-key")).toHaveLength(1);
		await user.click(within(dialog).getByRole("button", { name: "Make a new link" }));
		await waitFor(() => expect(writes().filter((c) => c.path === "/website/learn-key")).toHaveLength(2));
	});

	it("removes the link after asking", async () => {
		const { user } = renderTab();
		await user.click(await screen.findByRole("button", { name: "Create update link" }));
		await user.click(await screen.findByRole("button", { name: "Remove link" }));
		await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Remove link" }));
		await waitFor(() => expect(writes()).toContainEqual({ method: "DELETE", path: "/website/learn-key", body: null }));
		await screen.findByText("No update link yet.");
		expect(screen.queryByTestId("push-link")).not.toBeInTheDocument();
	});

	it("lets a member see whether a link is set but not make or remove it", async () => {
		websiteState.site.learn_key = { set: true, hint: "ab12", created_at: "2026-10-05T10:00:00Z" };
		renderTab("member");
		await screen.findByText(/it ends in ab12/);
		expect(screen.getByRole("button", { name: "Create a new link" })).toHaveAttribute("aria-disabled", "true");
		expect(screen.getByRole("button", { name: "Remove link" })).toHaveAttribute("aria-disabled", "true");
		expect(writes()).toEqual([]);
	});

	it("has steps for WordPress, any website and IndexNow tools", async () => {
		const { user } = renderTab();
		const steps = await screen.findByTestId("push-guides");
		expect(within(steps).getByText(/webhook plugin such as WP Webhooks/i)).toBeInTheDocument();
		expect(within(steps).getByText(/transition_post_status/)).toBeInTheDocument();
		expect(within(steps).getByText(/only tell search engines/i)).toBeInTheDocument();
		await user.click(within(steps).getByRole("tab", { name: "Any website" }));
		expect(within(steps).getByText(/curl -X POST/)).toBeInTheDocument();
		await user.click(within(steps).getByRole("tab", { name: "IndexNow tools" }));
		expect(within(steps).getByText(/paste your update link there/i)).toBeInTheDocument();
	});

	it("never says corpus, crawl, tenant or the engine's name", async () => {
		const { container } = renderTab();
		await screen.findByTestId("push-guides");
		expect(container.textContent ?? "").not.toMatch(/corpus|crawl|tenant|cob-rag-engine/i);
	});
});
