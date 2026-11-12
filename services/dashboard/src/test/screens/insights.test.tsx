import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, ROHAN, rt, SHREE, SV, tokenResponse } from "../msw/data";
import { createInsightsMock, GAP_DONE_ID, type Logged, QUESTION_IDS } from "../msw/handlers/insights";
import { createQualityMock } from "../msw/handlers/quality";
import { server } from "../msw/server";
import { renderApp } from "../render";

const PATIENT = CORPORA[0]?.id ?? "";
const MEMBER_SHREE = { ...SHREE, role: "member" as const };

type Who = "owner" | "member" | "admin";

function signIn(who: Who) {
	const user = who === "admin" ? SV : who === "member" ? ROHAN : PRIYA;
	const tenant = who === "admin" ? null : who === "member" ? MEMBER_SHREE : SHREE;
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

function open(path: string, who: Who = "owner", opts: Parameters<typeof createInsightsMock>[0] = {}) {
	signIn(who);
	const mock = createInsightsMock({ memberSession: who === "member", ...opts });
	const quality = createQualityMock({ memberSession: who === "member" });
	server.use(...mock.handlers, ...quality.handlers);
	const app = renderApp(path);
	return { ...app, mock, quality };
}

const calls = (log: Logged[], method: string, path: string) =>
	log.filter((l) => l.method === method && l.path === path);

describe("insights: questions", () => {
	it("lists the history with confidence badges, time taken and paging parameters", async () => {
		const { mock } = open(`/insights?kb=${PATIENT}&tab=questions`);
		expect(await screen.findByRole("heading", { level: 1, name: "Insights" })).toBeInTheDocument();
		const table = await screen.findByRole("table", { name: "Question history" });
		const rows = within(table).getAllByRole("row");
		expect(rows).toHaveLength(4); // header + 3 Patient help questions
		expect(within(table).getByText("Answered well")).toBeInTheDocument();
		expect(within(table).getByText("Unsure")).toBeInTheDocument();
		expect(within(table).getByText("Weak")).toBeInTheDocument();
		expect(within(table).getByText("2.1 s")).toBeInTheDocument();
		expect(within(table).queryByRole("columnheader", { name: "Knowledge base" })).toBeNull();
		const req = calls(mock.log, "GET", "/api/v1/analytics/queries/history")[0];
		expect(req?.query).toMatchObject({ corpus_id: PATIENT, offset: "0", limit: "20" });
	});

	it("filters by session id and on the loaded page by words", async () => {
		const { mock, user } = open(`/insights?kb=${PATIENT}&tab=questions`);
		await screen.findByRole("table", { name: "Question history" });
		await user.type(screen.getByLabelText("Session id"), "visitor-17");
		await user.click(screen.getByRole("button", { name: "Apply" }));
		await waitFor(() =>
			expect(calls(mock.log, "GET", "/api/v1/analytics/queries/history").at(-1)?.query.session_id).toBe("visitor-17"),
		);
		await waitFor(() => expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(3));
		await user.type(screen.getByLabelText("Filter this page"), "baner");
		await waitFor(() => expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(2));
	});

	it("'All my knowledge bases' omits corpus_id and adds the knowledge base column", async () => {
		const { mock, user } = open(`/insights?kb=${PATIENT}&tab=questions`);
		await screen.findByRole("table", { name: "Question history" });
		await user.click(screen.getByRole("radio", { name: "All my knowledge bases" }));
		const table = await screen.findByRole("table", { name: "Question history" });
		await waitFor(() =>
			expect(within(table).getByRole("columnheader", { name: "Knowledge base" })).toBeInTheDocument(),
		);
		expect(within(table).getAllByRole("row")).toHaveLength(5);
		expect(calls(mock.log, "GET", "/api/v1/analytics/queries/history").at(-1)?.query.corpus_id).toBeUndefined();
	});

	it("the drawer loads /query/{id}, shows its feedback, adds feedback and offers playground and golden set", async () => {
		const { mock, user } = open(`/insights?kb=${PATIENT}&tab=questions`);
		await user.click(await screen.findByRole("button", { name: "Open question: Home collection in Baner on Sunday?" }));
		const drawer = await screen.findByRole("dialog", { name: "Home collection in Baner on Sunday?" });
		expect(await within(drawer).findByTestId("drawer-answer")).toHaveTextContent("I couldn't find that");
		expect(within(drawer).getByText("openai/gpt-oss-20b")).toBeInTheDocument();
		expect(within(drawer).getByText("1,240 (1,100 in · 140 out)")).toBeInTheDocument();
		expect(calls(mock.log, "GET", `/api/v1/query/${QUESTION_IDS.sunday}`)).toHaveLength(1);
		const fb = await within(drawer).findByTestId("drawer-feedback");
		expect(fb).toHaveTextContent("Not helpful");
		expect(fb).toHaveTextContent("Sundays 7–11 am");
		expect(within(drawer).getByRole("link", { name: "Open in playground" })).toHaveAttribute(
			"href",
			expect.stringContaining(`/playground?kb=${PATIENT}&q=Home`),
		);

		await user.type(within(drawer).getByLabelText("What should it have said? (optional)"), "Yes, 7–11 am.");
		await user.click(within(drawer).getByRole("button", { name: "Save feedback" }));
		await waitFor(() => expect(calls(mock.log, "POST", "/api/v1/feedback")).toHaveLength(1));
		expect(calls(mock.log, "POST", "/api/v1/feedback")[0]?.body).toEqual({
			query_id: QUESTION_IDS.sunday,
			rating: 1,
			correction: "Yes, 7–11 am.",
		});

		await user.click(within(drawer).getByRole("button", { name: "Add to golden set" }));
		const dialog = await screen.findByRole("dialog", { name: "Add a golden question" });
		expect(within(dialog).getByLabelText("Question")).toHaveValue("Home collection in Baner on Sunday?");
		expect(within(dialog).getByLabelText("Expected answer")).toHaveValue("I couldn't find that in the documents.");
	});

	it("an unknown question id shows 'No longer available'", async () => {
		open(`/insights?kb=${PATIENT}&tab=questions&question=0d0d0d0d-0000-4000-8000-00000000ffff`);
		expect(await screen.findByTestId("question-gone")).toHaveTextContent("No longer available");
	});
});

describe("insights: trends and confidence", () => {
	it("two single-axis charts, totals, a table toggle and the granularity", async () => {
		const { mock, user } = open(`/insights?kb=${PATIENT}&tab=trends`);
		expect(await screen.findByTestId("trend-count-chart")).toHaveAttribute(
			"aria-label",
			expect.stringContaining("116 questions, most 64"),
		);
		expect(screen.getByTestId("trend-confidence-chart")).toHaveAttribute(
			"aria-label",
			expect.stringContaining("from 71% to 82%"),
		);
		expect(within(screen.getByTestId("trend-kpis")).getByText("116")).toBeInTheDocument();
		expect(within(screen.getByTestId("trend-kpis")).getByText("75%")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Show as table" }));
		const table = screen.getByRole("table", { name: "Trend data" });
		expect(within(table).getAllByRole("row")).toHaveLength(4);
		expect(within(table).getByText("—")).toBeInTheDocument();
		await user.click(screen.getByRole("radio", { name: "Week" }));
		await waitFor(() =>
			expect(calls(mock.log, "GET", "/api/v1/analytics/queries/trends").at(-1)?.query.granularity).toBe("week"),
		);
		const first = calls(mock.log, "GET", "/api/v1/analytics/queries/trends")[0];
		expect(first?.query).toMatchObject({ corpus_id: PATIENT, granularity: "day" });
		expect(first?.query.start).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:00:00\.000Z$/);
	});

	it("confidence spread: summary, chart and table", async () => {
		const { user } = open(`/insights?kb=${PATIENT}&tab=confidence`);
		expect(await screen.findByTestId("confidence-summary")).toHaveTextContent(
			"30 days: 104 scored answers, 58 of them 0.8 or higher.",
		);
		expect(screen.getByTestId("confidence-chart")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Show as table" }));
		const table = screen.getByRole("table");
		expect(within(table).getByRole("rowheader", { name: "0.8–1.0" })).toBeInTheDocument();
		expect(within(table).getByText("56%")).toBeInTheDocument();
	});
});

describe("insights: knowledge gaps", () => {
	it("opens a report: keyword chips, counts, examples and 'Add content for this'", async () => {
		const { user } = open(`/insights?kb=${PATIENT}&tab=gaps`);
		await user.click(await screen.findByRole("button", { name: /View report from/ }));
		const clusters = await screen.findByTestId("gap-clusters");
		const items = within(clusters)
			.getAllByRole("listitem")
			.filter((li) => li.parentElement === clusters);
		expect(items[0]).toHaveTextContent("Topic 1 · asked 7 times");
		expect(within(items[0] as HTMLElement).getByText("baner")).toBeInTheDocument();
		expect(items[0]).toHaveTextContent("“रविवारी घरून नमुना घेता का?”");
		const link = within(items[0] as HTMLElement).getByRole("link", { name: "Add content for this" });
		expect(link).toHaveAttribute("href", `/add?kb=${PATIENT}`);
		expect(link).toHaveAccessibleDescription("Add documents that cover: home collection, baner, sunday");
	});

	it("runs gap detection for one knowledge base and polls until completed", async () => {
		const { mock, user } = open(`/insights?kb=${PATIENT}&tab=gaps`);
		await user.click(await screen.findByRole("button", { name: "Run gap detection" }));
		expect(await screen.findByTestId("gap-polling")).toHaveTextContent("Checking every 5 seconds");
		expect(calls(mock.log, "POST", "/api/v1/analytics/gaps/run")[0]?.query).toEqual({ corpus_id: PATIENT });
		expect(await screen.findByText("thyroid", {}, { timeout: 8000 })).toBeInTheDocument();
		expect(screen.queryByTestId("gap-polling")).toBeNull();
	}, 15_000);

	it("a 503 (no embedder) is shown as the engine's message", async () => {
		const { user } = open(`/insights?kb=${PATIENT}&tab=gaps`, "owner", { noEmbedder: true });
		await user.click(await screen.findByRole("button", { name: "Run gap detection" }));
		expect(await screen.findByText("No embedder available for gap detection")).toBeInTheDocument();
	});

	it("a tenant with 'All' cannot run (needs one knowledge base) and sees no engine-wide report", async () => {
		const { mock } = open(`/insights?kb=${PATIENT}&tab=gaps&scope=all`);
		expect(await screen.findByTestId("gaps-needs-kb")).toHaveTextContent("Pick one knowledge base");
		expect(screen.getByRole("button", { name: "Run gap detection" })).toBeDisabled();
		expect(screen.queryByRole("region", { name: "Latest engine-wide report" })).toBeNull();
		expect(calls(mock.log, "GET", "/api/v1/admin/gaps")).toHaveLength(0);
	});

	it("the super admin with 'All' triggers the global report and sees the latest engine-wide one", async () => {
		const { mock, user } = open(`/insights?kb=${PATIENT}&tab=gaps&scope=all`, "admin");
		const latest = await screen.findByRole("region", { name: "Latest engine-wide report" });
		expect(await within(latest).findByText("hba1c")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Run for every knowledge base" }));
		await waitFor(() => expect(calls(mock.log, "POST", "/api/v1/admin/gaps/trigger")).toHaveLength(1));
		expect(calls(mock.log, "POST", "/api/v1/analytics/gaps/run")).toHaveLength(0);
		expect(await screen.findByTestId("gap-polling")).toBeInTheDocument();
	});

	it("the admin with one knowledge base uses the per-KB run", async () => {
		const { mock, user } = open(`/insights?kb=${PATIENT}&tab=gaps&report=${GAP_DONE_ID}`, "admin");
		await user.click(await screen.findByRole("button", { name: "All reports" }));
		await user.click(await screen.findByRole("button", { name: "Run gap detection" }));
		await waitFor(() => expect(calls(mock.log, "POST", "/api/v1/analytics/gaps/run")).toHaveLength(1));
		expect(calls(mock.log, "POST", "/api/v1/admin/gaps/trigger")).toHaveLength(0);
	});
});

describe("insights: feedback", () => {
	it("totals for one knowledge base, the rating filter and 'Add to golden set'", async () => {
		const { mock, quality, user } = open(`/insights?kb=${PATIENT}&tab=feedback`);
		const stats = await screen.findByTestId("feedback-stats");
		expect(within(stats).getAllByText("2")).toHaveLength(1);
		const table = await screen.findByRole("table", { name: "Feedback" });
		expect(within(table).getAllByRole("row")).toHaveLength(3);
		expect(within(table).getByText("Only a rating with a correction can become a golden answer")).toBeInTheDocument();

		await user.click(screen.getByRole("radio", { name: "Not helpful" }));
		await waitFor(() => expect(calls(mock.log, "GET", "/api/v1/feedback").at(-1)?.query.max_rating).toBe("2"));
		await waitFor(() =>
			expect(within(screen.getByRole("table", { name: "Feedback" })).getAllByRole("row")).toHaveLength(2),
		);

		await user.click(
			screen.getByRole("button", { name: "Add “Home collection in Baner on Sunday?” to the golden set" }),
		);
		await waitFor(() => expect(calls(quality.log, "POST", "/api/v1/golden/from-feedback")).toHaveLength(1));
		expect(calls(quality.log, "POST", "/api/v1/golden/from-feedback")[0]?.body).toEqual({
			feedback_id: "fb000000-0000-4000-8000-000000000001",
		});
		expect(await screen.findByText("Added to the golden set")).toBeInTheDocument();
	});

	it("'All' hides the totals (they need one knowledge base) and sends no corpus_id", async () => {
		const { mock } = open(`/insights?kb=${PATIENT}&tab=feedback&scope=all`);
		expect(await screen.findByText("Totals are shown when one knowledge base is selected.")).toBeInTheDocument();
		await screen.findByRole("table", { name: "Feedback" });
		expect(calls(mock.log, "GET", "/api/v1/feedback/stats")).toHaveLength(0);
		expect(calls(mock.log, "GET", "/api/v1/feedback")[0]?.query.corpus_id).toBeUndefined();
	});
});

describe("insights: memories", () => {
	it("filters by kind and person; search needs a person", async () => {
		const { mock, user } = open(`/insights?kb=${PATIENT}&tab=memories`);
		const table = await screen.findByRole("table", { name: "Memories" });
		expect(within(table).getAllByRole("row")).toHaveLength(3);

		await user.type(screen.getByLabelText("Search this person's memories"), "kothrud");
		await user.click(screen.getByRole("button", { name: "Search" }));
		expect(await screen.findByRole("alert")).toHaveTextContent("Enter a person's user id first");
		expect(calls(mock.log, "POST", `/api/v1/corpora/${PATIENT}/memories/search`)).toHaveLength(0);

		await user.selectOptions(screen.getByLabelText("Kind"), "preference");
		await user.type(screen.getByLabelText("Person (user id)"), "visitor-17");
		await user.click(screen.getByRole("button", { name: "Apply" }));
		await waitFor(() =>
			expect(calls(mock.log, "GET", `/api/v1/corpora/${PATIENT}/memories`).at(-1)?.query).toMatchObject({
				memory_type: "preference",
				user_id: "visitor-17",
			}),
		);
		await user.click(screen.getByRole("button", { name: "Search" }));
		expect(await screen.findByText("1 match for “kothrud”")).toBeInTheDocument();
		expect(calls(mock.log, "POST", `/api/v1/corpora/${PATIENT}/memories/search`)[0]?.body).toEqual({
			user_id: "visitor-17",
			query: "kothrud",
			limit: 20,
		});
	});

	it("adds one memory and validates bulk JSON before sending", async () => {
		const { mock, user } = open(`/insights?kb=${PATIENT}&tab=memories`);
		await screen.findByRole("table", { name: "Memories" });
		await user.click(screen.getByRole("button", { name: "Add memory" }));
		const add = await screen.findByRole("dialog", { name: "Add a memory" });
		await user.type(within(add).getByLabelText("Person (user id)"), "visitor-21");
		await user.type(within(add).getByLabelText("Memory"), "Prefers Marathi replies");
		await user.selectOptions(within(add).getByLabelText("Kind"), "preference");
		await user.click(within(add).getByRole("button", { name: "Add memory" }));
		await waitFor(() => expect(calls(mock.log, "POST", `/api/v1/corpora/${PATIENT}/memories`)).toHaveLength(1));
		expect(calls(mock.log, "POST", `/api/v1/corpora/${PATIENT}/memories`)[0]?.body).toEqual({
			user_id: "visitor-21",
			content: "Prefers Marathi replies",
			memory_type: "preference",
			category: null,
			related_entity: null,
			confidence: 1,
			source: "explicit",
		});

		await user.click(screen.getByRole("button", { name: "Add many (JSON)" }));
		const bulk = await screen.findByRole("dialog", { name: "Add many memories" });
		const box = within(bulk).getByLabelText("Memories (JSON)");
		await user.click(box);
		await user.paste(JSON.stringify(Array(51).fill({ user_id: "v", content: "c" })));
		await user.click(within(bulk).getByRole("button", { name: "Add memories" }));
		expect(await within(bulk).findByText("At most 50 memories at a time (51 pasted).")).toBeInTheDocument();
		expect(calls(mock.log, "POST", `/api/v1/corpora/${PATIENT}/memories/bulk`)).toHaveLength(0);
		await user.clear(box);
		await user.paste(JSON.stringify([{ user_id: "visitor-9", content: "Has a thyroid follow-up in October" }]));
		await user.click(within(bulk).getByRole("button", { name: "Add memories" }));
		await waitFor(() => expect(calls(mock.log, "POST", `/api/v1/corpora/${PATIENT}/memories/bulk`)).toHaveLength(1));
	});

	it("an owner's typed bulk delete states the scope and sends the filter; purge likewise", async () => {
		const { mock, user } = open(`/insights?kb=${PATIENT}&tab=memories&user=visitor-17&mtype=preference`);
		await screen.findByRole("table", { name: "Memories" });
		await user.click(screen.getByRole("button", { name: "Delete all for this filter" }));
		const dlg = await screen.findByRole("alertdialog", { name: "Delete memories in bulk?" });
		expect(within(dlg).getByTestId("bulk-scope")).toHaveTextContent(
			"This deletes every preference memory of person “visitor-17” in Patient help.",
		);
		const confirm = within(dlg).getByRole("button", { name: "Delete" });
		expect(confirm).toBeDisabled();
		await user.type(within(dlg).getByLabelText(/Type delete to confirm/), "delete");
		await user.click(confirm);
		await waitFor(() => expect(calls(mock.log, "DELETE", `/api/v1/corpora/${PATIENT}/memories`)).toHaveLength(1));
		expect(calls(mock.log, "DELETE", `/api/v1/corpora/${PATIENT}/memories`)[0]?.query).toEqual({
			memory_type: "preference",
			user_id: "visitor-17",
		});
		expect(await screen.findByText("1 memory deleted")).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Purge deleted" }));
		const purge = await screen.findByRole("alertdialog", { name: "Purge deleted memories?" });
		await user.type(within(purge).getByLabelText(/Type delete to confirm/), "delete");
		await user.click(within(purge).getByRole("button", { name: "Purge" }));
		await waitFor(() => expect(calls(mock.log, "DELETE", `/api/v1/corpora/${PATIENT}/memories/purge`)).toHaveLength(1));
	});

	it("deletes one memory after a plain confirm and views one", async () => {
		const { mock, user } = open(`/insights?kb=${PATIENT}&tab=memories`);
		await screen.findByRole("table", { name: "Memories" });
		await user.click(screen.getByRole("button", { name: /^View memory: Prefers home collection/ }));
		expect(await screen.findByTestId("memory-content")).toHaveTextContent("Prefers home collection in Kothrud");
		await user.keyboard("{Escape}");
		await user.click(screen.getByRole("button", { name: /^Delete memory: Diabetic/ }));
		const dlg = await screen.findByRole("alertdialog", { name: "Delete this memory?" });
		await user.click(within(dlg).getByRole("button", { name: "Delete" }));
		await waitFor(() =>
			expect(
				calls(mock.log, "DELETE", `/api/v1/corpora/${PATIENT}/memories/a1a1a1a1-0000-4000-8000-000000000002`),
			).toHaveLength(1),
		);
	});

	it("a member sees bulk delete and purge locked, with the reason, and nothing is sent", async () => {
		const { mock, user } = open(`/insights?kb=${PATIENT}&tab=memories`, "member");
		await screen.findByRole("table", { name: "Memories" });
		const bulk = screen.getByRole("button", { name: "Delete all for this filter" });
		expect(bulk).toHaveAttribute("aria-disabled", "true");
		expect(screen.getByRole("button", { name: "Purge deleted" })).toHaveAttribute("aria-disabled", "true");
		await user.click(bulk);
		expect(await screen.findAllByText("Only owners can delete memories in bulk")).not.toHaveLength(0);
		expect(screen.queryByRole("alertdialog")).toBeNull();
		expect(calls(mock.log, "DELETE", `/api/v1/corpora/${PATIENT}/memories`)).toHaveLength(0);
	});

	it("'All' still works on the selected knowledge base and says so", async () => {
		open(`/insights?kb=${PATIENT}&tab=memories&scope=all`);
		expect(await screen.findByTestId("memories-one-kb")).toHaveTextContent(
			"This section works on one knowledge base at a time: Patient help.",
		);
	});
});
