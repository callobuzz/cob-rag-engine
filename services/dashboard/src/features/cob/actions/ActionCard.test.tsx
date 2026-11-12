import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { server } from "@/test/msw/server";
import type { CobActionCardBlock } from "../types";
import { ActionCard, countdown } from "./ActionCard";

const user = userEvent.setup();

function card(over: Partial<CobActionCardBlock> = {}): CobActionCardBlock {
	return {
		kind: "cob_action_card",
		proposal_id: "p1",
		tool: "create_widget_key",
		title: "Create a widget key",
		summary: "Create the widget key **Main site** for **Shree Labs**.",
		args: [
			{ label: "Knowledge base", value: "Shree Labs" },
			{ label: "Website", value: "https://shreelabs.in" },
		],
		risk: "low",
		destructive: false,
		confirm_name: null,
		expires_at: new Date(Date.now() + 9 * 60_000).toISOString(),
		status: "pending",
		...over,
	};
}

function mount(node: React.ReactNode) {
	const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
	const root = createRootRoute({ component: () => node });
	const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ["/"] }) });
	return render(
		<QueryClientProvider client={queryClient}>
			<RouterProvider router={router} />
		</QueryClientProvider>,
	);
}

const result = (over: Record<string, unknown> = {}) => ({
	proposal_id: "p1",
	status: "done",
	message: "Widget key **Main site** created.",
	link: { kind: "cob_link", label: "Open Website widget →", route: "/widget", docs_slug: null },
	secret: null,
	http_status: 201,
	code: null,
	...over,
});

describe("ActionCard", () => {
	it("shows the title, the risk, the details and a countdown", async () => {
		mount(<ActionCard block={card()} />);
		expect(await screen.findByText("Create a widget key")).toBeInTheDocument();
		expect(screen.getByTestId("cob-action-risk")).toHaveTextContent("Low risk");
		expect(screen.getByText("https://shreelabs.in")).toBeInTheDocument();
		expect(screen.getByTestId("cob-action-countdown")).toHaveTextContent(/Expires in [89]:\d\d/);
		expect(screen.getByRole("button", { name: "Confirm" })).toBeEnabled();
	});

	it("confirms a low-risk action and shows the result", async () => {
		server.use(http.post("*/api/v1/cob/actions/p1/confirm", () => HttpResponse.json(result())));
		mount(<ActionCard block={card({ risk: "low", destructive: false })} />);
		await user.click(await screen.findByRole("button", { name: "Confirm" }));
		expect(await screen.findByText(/created/)).toBeInTheDocument();
		expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull(); // single use in the UI too
		expect(screen.getByRole("link", { name: "Open Website widget →" })).toBeInTheDocument();
	});

	it("sends no name for an ordinary card", async () => {
		let body: unknown = null;
		server.use(
			http.post("*/api/v1/cob/actions/p1/confirm", async ({ request }) => {
				body = await request.json();
				return HttpResponse.json(result());
			}),
		);
		mount(<ActionCard block={card()} />);
		await user.click(await screen.findByRole("button", { name: "Confirm" }));
		await screen.findByText(/created/);
		expect(body).toEqual({});
	});

	it("destructive needs the exact name before Delete is enabled", async () => {
		let body: unknown = null;
		server.use(
			http.post("*/api/v1/cob/actions/p1/confirm", async ({ request }) => {
				body = await request.json();
				return HttpResponse.json(result({ message: "Knowledge base **Shree Labs** deleted.", link: null }));
			}),
		);
		mount(
			<ActionCard
				block={card({
					tool: "delete_knowledge_base",
					title: "Delete a knowledge base",
					destructive: true,
					risk: "high",
					confirm_name: "Shree Labs",
				})}
			/>,
		);
		const confirm = await screen.findByRole("button", { name: "Delete" });
		expect(confirm).toBeDisabled();
		const field = screen.getByLabelText(/Type Shree Labs to confirm/);
		await user.type(field, "Shree Lab");
		expect(confirm).toBeDisabled();
		await user.type(field, "s");
		expect(confirm).toBeEnabled();
		await user.click(confirm);
		expect(await screen.findByText(/deleted/)).toBeInTheDocument();
		expect(body).toEqual({ confirm_name: "Shree Labs" });
	});

	it("a suspend card says Suspend", async () => {
		mount(
			<ActionCard
				block={card({ tool: "suspend_customer", destructive: true, risk: "high", confirm_name: "Anand Dental Care" })}
			/>,
		);
		expect(await screen.findByRole("button", { name: "Suspend" })).toBeDisabled();
	});

	it("a name the engine refuses stays editable with the message under the field", async () => {
		server.use(
			http.post("*/api/v1/cob/actions/p1/confirm", () =>
				HttpResponse.json({ detail: "no", code: "confirm_mismatch" }, { status: 422 }),
			),
		);
		mount(<ActionCard block={card({ destructive: true, risk: "high", confirm_name: "Shree Labs" })} />);
		await user.type(await screen.findByLabelText(/Type Shree Labs to confirm/), "Shree Labs");
		await user.click(screen.getByRole("button", { name: "Delete" }));
		expect(await screen.findByRole("alert")).toHaveTextContent("The name doesn't match exactly.");
		expect(screen.getByRole("button", { name: "Delete" })).toBeEnabled();
	});

	it("shows a rotated secret once with copy, and hides it after closing", async () => {
		server.use(
			http.post("*/api/v1/cob/actions/p1/confirm", () =>
				HttpResponse.json(result({ message: "Key **Zapier** rotated.", secret: "tk_SECRET_123", link: null })),
			),
		);
		mount(<ActionCard block={card({ tool: "rotate_key", risk: "medium" })} />);
		await user.click(await screen.findByRole("button", { name: "Confirm" }));
		expect(await screen.findByText("tk_SECRET_123")).toBeInTheDocument();
		expect(screen.getByRole("button", { name: /copy/i })).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "I've copied it" }));
		expect(screen.queryByText("tk_SECRET_123")).toBeNull();
		expect(screen.getByText(/isn't stored anywhere/)).toBeInTheDocument();
	});

	it("a route that refused is a failed result with the route's own sentence", async () => {
		server.use(
			http.post("*/api/v1/cob/actions/p1/confirm", () =>
				HttpResponse.json(
					result({
						status: "failed",
						message: "Your plan allows 1 widget key.",
						code: "widget_key_limit_reached",
						http_status: 409,
						link: null,
					}),
				),
			),
		);
		mount(<ActionCard block={card()} />);
		await user.click(await screen.findByRole("button", { name: "Confirm" }));
		expect(await screen.findByText("Your plan allows 1 widget key.")).toBeInTheDocument();
		expect(screen.getByText(/You can ask me again/)).toBeInTheDocument();
		expect(screen.getByTestId("cob-action-card")).toHaveAttribute("data-phase", "failed");
	});

	it("expired card shows Expired and offers to ask again", async () => {
		server.use(
			http.post("*/api/v1/cob/actions/p1/confirm", () =>
				HttpResponse.json({ detail: "old", code: "cob_proposal_expired" }, { status: 410 }),
			),
		);
		const again = vi.fn();
		mount(<ActionCard block={card()} onAskAgain={again} />);
		await user.click(await screen.findByRole("button", { name: "Confirm" }));
		expect(await screen.findByText("This card expired. Ask me again.")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Ask again" }));
		expect(again).toHaveBeenCalledOnce();
	});

	it("a card already past its time opens as expired and cannot be pressed", async () => {
		mount(<ActionCard block={card({ expires_at: new Date(Date.now() - 1000).toISOString() })} />);
		expect(await screen.findByText("This card expired. Ask me again.")).toBeInTheDocument();
		expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
	});

	it("cancel posts to the engine and says nothing was changed", async () => {
		let hit = false;
		server.use(
			http.post("*/api/v1/cob/actions/p1/cancel", () => {
				hit = true;
				return HttpResponse.json({ proposal_id: "p1", status: "cancelled" });
			}),
		);
		mount(<ActionCard block={card()} />);
		await user.click(await screen.findByRole("button", { name: "Cancel" }));
		expect(await screen.findByText(/Nothing was changed/)).toBeInTheDocument();
		expect(hit).toBe(true);
	});

	it("a cancel that finds the action already used says so instead of 'nothing changed'", async () => {
		server.use(
			http.post("*/api/v1/cob/actions/p1/cancel", () =>
				HttpResponse.json({ detail: "used", code: "cob_proposal_used" }, { status: 409 }),
			),
		);
		mount(<ActionCard block={card()} />);
		await user.click(await screen.findByRole("button", { name: "Cancel" }));
		expect(await screen.findByText("That action was already done or cancelled.")).toBeInTheDocument();
		expect(screen.queryByText(/Nothing was changed/)).toBeNull();
	});

	it("a suspend card is badged as stopping access, not as irreversible", async () => {
		mount(
			<ActionCard block={card({ tool: "suspend_customer", risk: "high", destructive: true, confirm_name: "Anand" })} />,
		);
		expect(await screen.findByTestId("cob-action-risk")).toHaveTextContent("Stops access");
	});

	it("a card read back from History shows how it ended and has no buttons", async () => {
		for (const [status, label] of [
			["done", "Done"],
			["failed", "Didn't work"],
			["cancelled", "Cancelled"],
		] as const) {
			const { unmount } = mount(<ActionCard block={card({ status })} />);
			expect(await screen.findByText(label)).toBeInTheDocument();
			expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
			unmount();
		}
	});

	it("an engine that says the card was already used is a failed card, not a crash", async () => {
		server.use(
			http.post("*/api/v1/cob/actions/p1/confirm", () =>
				HttpResponse.json({ detail: "used", code: "cob_proposal_used" }, { status: 409 }),
			),
		);
		mount(<ActionCard block={card()} />);
		await user.click(await screen.findByRole("button", { name: "Confirm" }));
		await waitFor(() => expect(screen.getByText("That action was already done or cancelled.")).toBeInTheDocument());
	});
});

describe("countdown", () => {
	it("reads minutes and seconds and never goes below zero", () => {
		expect(countdown(9 * 60_000 + 42_000)).toBe("9:42");
		expect(countdown(5_000)).toBe("0:05");
		expect(countdown(-3)).toBe("0:00");
	});
});
