import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { Principal } from "@/lib/auth/principal";
import { setAuthState } from "@/lib/auth/store";
import { BASE } from "@/test/msw/data";
import {
	resetWebsiteState,
	SOURCE,
	SOURCE_ID,
	WEBSITE_KB,
	websiteHandlers,
	websiteState,
} from "@/test/msw/handlers/website";
import { server } from "@/test/msw/server";
import type { WebsitePage } from "./api";
import { WebsiteTab } from "./WebsiteTab";

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

describe("From your website", () => {
	it("shows the site, its progress in words, and every page with the reason it was or wasn't learned", async () => {
		renderTab();
		const card = await screen.findByRole("article", { name: "shreedental.in" });
		expect(within(card).getByText("Up to date")).toBeInTheDocument();
		expect(within(card).getByText(/3 pages learned of 6 found/)).toBeInTheDocument();
		const table = await screen.findByRole("table", { name: "Pages from your website" });
		expect(within(table).getByText("Fees and charges")).toBeInTheDocument();
		expect(within(table).getByText("Login or private page")).toBeInTheDocument();
		expect(within(table).getByText("Same as another page")).toBeInTheDocument();
		expect(within(table).getByText("Page not found (404)")).toBeInTheDocument();
		expect(within(table).getAllByText("Learned").length).toBeGreaterThan(0);
		expect(within(table).getByText("Couldn't read")).toBeInTheDocument();
	});

	// TV5: when each page last changed
	it("shows when each page last changed, and a dash for a page that never was read", async () => {
		websiteState.pages[1] = { ...(websiteState.pages[1] as WebsitePage), last_changed_at: "2026-10-03T08:00:00Z" };
		websiteState.pages[5] = { ...(websiteState.pages[5] as WebsitePage), last_changed_at: null };
		renderTab();
		const table = await screen.findByRole("table", { name: "Pages from your website" });
		expect(within(table).getByRole("columnheader", { name: "Changed" })).toBeInTheDocument();
		const fees = within(table).getByText("Fees and charges").closest("tr") as HTMLElement;
		expect(within(fees).getByText("3 Oct")).toBeInTheDocument();
		const gone = within(table).getAllByText("/old-offer")[0]?.closest("tr") as HTMLElement;
		expect(within(gone).getAllByText("—").length).toBeGreaterThan(0);
	});
	// /TV5

	it("never says corpus, crawl, tenant or the engine's name", async () => {
		const { container } = renderTab();
		await screen.findByRole("table", { name: "Pages from your website" });
		expect(container.textContent ?? "").not.toMatch(/corpus|crawl|tenant|cob-rag-engine/i);
	});

	it("opens a page on the website in a new tab, safely", async () => {
		renderTab();
		const link = await screen.findByRole("link", { name: "Open Fees and charges on your website" });
		expect(link).toHaveAttribute("href", "https://shreedental.in/pricing");
		expect(link).toHaveAttribute("target", "_blank");
		expect(link).toHaveAttribute("rel", "noopener noreferrer");
	});

	it("filters by what happened, with counts on the chips", async () => {
		const { user } = renderTab();
		const table = await screen.findByRole("table", { name: "Pages from your website" });
		const chips = screen.getByRole("group", { name: "Show pages" });
		expect(within(chips).getByRole("button", { name: "All (6)" })).toBeInTheDocument();
		await user.click(within(chips).getByRole("button", { name: "Skipped (2)" }));
		await waitFor(() => expect(within(table).queryByText("Fees and charges")).not.toBeInTheDocument());
		expect(within(table).getByText("Login or private page")).toBeInTheDocument();
	});

	it("searches by address or title", async () => {
		const { user } = renderTab();
		await screen.findByRole("table", { name: "Pages from your website" });
		await user.type(screen.getByRole("searchbox", { name: "Search pages" }), "pricing");
		await waitFor(() => expect(screen.queryByText("Root canal treatment")).not.toBeInTheDocument());
		expect(screen.getByText("Fees and charges")).toBeInTheDocument();
	});

	it("leaves a page out at once, then the server agrees", async () => {
		const { user } = renderTab();
		const row = (await screen.findByText("Fees and charges")).closest("tr") as HTMLElement;
		await user.click(within(row).getByRole("button", { name: "Actions for Fees and charges" }));
		await user.click(await screen.findByRole("menuitem", { name: "Leave out" }));
		await waitFor(() => expect(within(row).getByText("Excluded by you")).toBeInTheDocument());
		expect(websiteState.calls).toContainEqual(expect.objectContaining({ method: "PATCH", body: { excluded: true } }));
		expect(await screen.findByText("Left out Fees and charges")).toBeInTheDocument();
	});

	it("reads a page again", async () => {
		const { user } = renderTab();
		const row = (await screen.findByText("Fees and charges")).closest("tr") as HTMLElement;
		await user.click(within(row).getByRole("button", { name: "Actions for Fees and charges" }));
		await user.click(await screen.findByRole("menuitem", { name: "Read again" }));
		expect(await screen.findByText("Reading Fees and charges again…")).toBeInTheDocument();
	});

	it("says when you can read everything again (the ten-minute limit)", async () => {
		const { user } = renderTab();
		await screen.findByRole("article", { name: "shreedental.in" });
		websiteState.rereadAt = Date.now();
		await user.click(screen.getByRole("button", { name: "Read everything again" }));
		expect(await screen.findByText("You can read your website again in 10 minutes.")).toBeInTheDocument();
	});

	it("saves the pages to leave out, one per line", async () => {
		const { user } = renderTab();
		const box = await screen.findByLabelText("Pages to leave out");
		await user.type(box, "/blog/*{enter}/careers");
		await user.click(screen.getByRole("button", { name: "Save" }));
		await waitFor(() =>
			expect(websiteState.calls).toContainEqual(
				expect.objectContaining({ method: "PATCH", body: { exclude_patterns: ["/blog/*", "/careers"] } }),
			),
		);
	});

	it("removes a site only after its name is typed", async () => {
		const { user } = renderTab();
		await user.click(await screen.findByRole("button", { name: "Remove shreedental.in" }));
		const dialog = await screen.findByRole("alertdialog");
		const confirm = within(dialog).getByRole("button", { name: "Remove website" });
		expect(confirm).toBeDisabled();
		await user.type(within(dialog).getByRole("textbox"), "shreedental.in");
		await user.click(confirm);
		await waitFor(() =>
			expect(websiteState.calls).toContainEqual(
				expect.objectContaining({ method: "DELETE", path: `/website/sources/${SOURCE_ID}` }),
			),
		);
		expect(await screen.findByText("Removed shreedental.in")).toBeInTheDocument();
	});

	it("offers an allowed site that was not read yet", async () => {
		websiteState.site.sources = [];
		websiteState.site.unlearned_origins = ["https://shreedental.in"];
		const { user } = renderTab();
		const card = await screen.findByTestId("website-unlearned");
		await user.click(within(card).getByRole("button", { name: "Learn shreedental.in" }));
		await waitFor(() =>
			expect(websiteState.calls).toContainEqual(
				expect.objectContaining({ path: "/website/learn", body: { origin: "https://shreedental.in" } }),
			),
		);
		expect(
			await screen.findByText("Your assistant is reading shreedental.in. This can take a few minutes."),
		).toBeInTheDocument();
	});

	it("asks for an allowed site first when there is none", async () => {
		websiteState.site.sources = [];
		renderTab();
		expect(await screen.findByText("Add your website first")).toBeInTheDocument();
	});

	it("explains a plan that does not include learning, in the deployer's product name", async () => {
		websiteState.site.plan.learning_enabled = false;
		renderTab();
		expect(await screen.findByText(/isn't included in your .+ plan/)).toBeInTheDocument();
		expect(screen.queryByRole("table")).not.toBeInTheDocument();
	});

	it("shows why a paused or failed site is not being read", async () => {
		websiteState.site.sources = [
			{ ...SOURCE, status: "paused", pause_reason: "origin_removed" },
			{
				...SOURCE,
				id: "6a000000-0000-4000-8000-000000000009",
				origin: "https://old.example.in",
				scope: "old.example.in",
				status: "failed",
				last_error_code: "robots_disallowed",
			},
		];
		renderTab();
		expect(await screen.findByText("This site is no longer an allowed site of your widget.")).toBeInTheDocument();
		expect(screen.getByText(/asks programs not to read it/)).toBeInTheDocument();
	});

	it("keeps owner-only controls visible but locked for a member", async () => {
		const { user } = renderTab("member");
		await screen.findByRole("article", { name: "shreedental.in" });
		expect(screen.getByRole("button", { name: "Remove shreedental.in" }).closest("[data-locked]")).not.toBeNull();
		expect(screen.getByRole("textbox", { name: "Pages to leave out" })).toHaveAttribute("readonly");
		expect(screen.getByRole("switch")).toBeInTheDocument();
		const row = (await screen.findByText("Fees and charges")).closest("tr") as HTMLElement;
		await user.click(within(row).getByRole("button", { name: "Actions for Fees and charges" }));
		expect(await screen.findByRole("menuitem", { name: "Leave out" })).toHaveAttribute("aria-disabled", "true");
		expect(websiteState.calls).toEqual([]);
	});

	it("looks again every few seconds while a site is being read, and stops when it is ready", async () => {
		websiteState.site.sources = [
			{ ...SOURCE, status: "learning", pages_found: 10, pages_learned: 4, pages_skipped: 1 },
		];
		let overviews = 0;
		server.use(
			http.get(`${BASE}/api/v1/corpora/:id/website`, () => {
				overviews++;
				return HttpResponse.json(websiteState.site);
			}),
		);
		renderTab();
		const bar = await screen.findByRole("progressbar", { name: "Learning shreedental.in" });
		expect(bar).toHaveAttribute("aria-valuenow", "50");
		const first = overviews;
		await act(async () => {
			websiteState.site.sources = [{ ...SOURCE, status: "ready" }];
			await new Promise((r) => setTimeout(r, 5_300));
		});
		await waitFor(() => expect(screen.queryByRole("progressbar")).not.toBeInTheDocument());
		expect(overviews).toBeGreaterThan(first);
		const settled = overviews;
		await act(async () => {
			await new Promise((r) => setTimeout(r, 5_500));
		});
		expect(overviews).toBe(settled);
	}, 20_000);
});
