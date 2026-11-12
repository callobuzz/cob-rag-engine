import { screen, waitFor, within } from "@testing-library/react";
import { delay, HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, rt, SHREE, tokenResponse } from "@/test/msw/data";
import { createReviewMock } from "@/test/msw/handlers/review";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";

const KB = CORPORA[0]?.id ?? "";

function open(path = `/review?kb=${KB}`) {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user: PRIYA, tenant: SHREE, tenants: [SHREE] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () =>
			HttpResponse.json({ user: PRIYA, tenant: SHREE, tenants: [SHREE], session_id: "s" }),
		),
		http.get(`${BASE}/api/v1/corpora`, () => {
			const items = CORPORA.filter((c) => c.tenant_id === SHREE.id);
			return HttpResponse.json({ items, count: items.length, total: items.length, offset: 0, limit: 500 });
		}),
	);
	const mock = createReviewMock();
	server.use(...mock.handlers);
	return { ...renderApp(path), mock };
}

describe("review inbox", () => {
	it("shows grouped items with counts and their example questions", async () => {
		open();
		expect(await screen.findByRole("heading", { level: 1, name: "Review" })).toBeInTheDocument();
		const card = await screen.findByRole("article", { name: "Do you do MRI scans?" });
		expect(within(card).getByText(/14 visitors/)).toBeInTheDocument();
		expect(within(card).getByRole("button", { name: "Add an exact answer" })).toBeInTheDocument();
		expect(within(card).getByRole("button", { name: "Add a document" })).toBeInTheDocument();
	});

	it("filters by kind", async () => {
		const { user } = open();
		await screen.findByRole("article", { name: "Do you do MRI scans?" });
		await user.click(screen.getByRole("radio", { name: "Asked for a person" }));
		await waitFor(() =>
			expect(screen.queryByRole("article", { name: "Do you do MRI scans?" })).not.toBeInTheDocument(),
		);
		expect(screen.getByRole("article", { name: "keyword" })).toBeInTheDocument();
	});

	it("adds an exact answer to the draft with the draft revision it read", async () => {
		const { user, mock } = open();
		const card = await screen.findByRole("article", { name: "Do you do MRI scans?" });
		const button = within(card).getByRole("button", { name: "Add an exact answer" });
		await waitFor(() => expect(button).toBeEnabled());
		await user.click(button);
		const dlg = await screen.findByRole("dialog", { name: "Add an exact answer" });
		await user.click(within(dlg).getByRole("button", { name: "Add to draft" }));
		expect(await within(dlg).findByText("Name the topic.")).toBeInTheDocument();
		await user.type(within(dlg).getByLabelText("Topic"), "MRI scans");
		await user.type(within(dlg).getByLabelText("Answer"), "We are a pathology lab, so we don't do MRI scans.");
		await user.click(within(dlg).getByRole("button", { name: "Add to draft" }));
		await waitFor(() =>
			expect(mock.actions[0]).toMatchObject({
				item_key: "a".repeat(32),
				action: "added_answer",
				base_revision: 3,
				answer: { topic_name: "MRI scans", question: "Do you do MRI scans?", language: "en" },
			}),
		);
		expect(await screen.findByText(/Publish to make it live/)).toBeInTheDocument();
		await waitFor(() =>
			expect(screen.queryByRole("article", { name: "Do you do MRI scans?" })).not.toBeInTheDocument(),
		);
	});

	it("marks an item done", async () => {
		const { user, mock } = open();
		await screen.findByRole("article", { name: "Do you do MRI scans?" });
		const buttons = await screen.findAllByRole("button", { name: "Done" });
		await user.click(buttons[0] as HTMLElement);
		await waitFor(() => expect(mock.actions[0]).toMatchObject({ action: "dismissed" }));
	});

	it("a double click sends one action", async () => {
		const { user } = open();
		const sent: unknown[] = [];
		server.use(
			http.post(`${BASE}/api/v1/corpora/:kb/assistant/review/actions`, async ({ request }) => {
				sent.push(await request.json());
				await delay(200);
				return HttpResponse.json(
					{ item_key: "k", action: "dismissed", target: null, draft_revision: 4 },
					{ status: 201 },
				);
			}),
		);
		await screen.findByRole("article", { name: "Do you do MRI scans?" });
		const button = (await screen.findAllByRole("button", { name: "Done" }))[0] as HTMLElement;
		await user.dblClick(button);
		await waitFor(() => expect(sent.length).toBeGreaterThan(0));
		await delay(300);
		expect(sent).toHaveLength(1);
	});

	it("says what a refused action means", async () => {
		const { user } = open();
		server.use(
			http.post(`${BASE}/api/v1/corpora/:kb/assistant/review/actions`, () =>
				HttpResponse.json({ detail: "gone", code: "review_item_not_found" }, { status: 404 }),
			),
		);
		const buttons = await screen.findAllByRole("button", { name: "Done" });
		await user.click(buttons[0] as HTMLElement);
		expect(
			await screen.findByText("That item was already handled or has expired. Refresh the inbox."),
		).toBeInTheDocument();
	});
});
