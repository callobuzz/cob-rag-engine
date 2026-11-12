import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, rt, SV, tokenResponse } from "@/test/msw/data";
import { createEngineMock, type ReviewItem } from "@/test/msw/handlers/engine";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";
import { type CobReviewItem, evalLine, helpPageFile } from "./CobReviewList";

const WIDGET: ReviewItem = {
	group: "terms:chat site widget",
	question: "how can I put the chat on my site",
	count: 3,
	last_at: "2026-10-04T08:00:00Z",
	role: "owner",
	language: "hi-Latn",
	top_slug: "screens/widget",
	reasons: ["no_answer", "thumbs_down"],
	comments: ["It sent me to the wrong screen"],
};
const PLAN: ReviewItem = {
	group: "terms:cost plan pro",
	question: "What does the Pro plan cost?",
	count: 1,
	last_at: "2026-10-03T08:00:00Z",
	role: "member",
	language: "en",
	top_slug: null,
	reasons: ["claims_removed"],
	comments: [],
};

function open(review: ReviewItem[]) {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user: SV, tenant: null, tenants: [] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () =>
			HttpResponse.json({ user: SV, tenant: null, tenants: [], session_id: "s" }),
		),
	);
	const mock = createEngineMock();
	mock.state.review = review;
	server.use(...mock.handlers);
	return { app: renderApp("/admin/engine"), mock };
}

const list = () => screen.findByRole("region", { name: "COB answers to review" });

describe("Engine info: COB answers to review", () => {
	it("lists the weak answers with why, who, which help page and what people said", async () => {
		open([WIDGET, PLAN]);
		const c = await list();
		expect(await within(c).findByText("how can I put the chat on my site")).toBeInTheDocument();
		expect(within(c).getByText("No answer found")).toBeInTheDocument();
		expect(within(c).getByText("Thumbs down")).toBeInTheDocument();
		expect(within(c).getByText("Statements removed")).toBeInTheDocument();
		expect(within(c).getByText("asked 3 times")).toBeInTheDocument();
		expect(within(c).getByText("asked once")).toBeInTheDocument();
		expect(within(c).getByText("Owner · Hinglish")).toBeInTheDocument();
		expect(within(c).getByText("Member · English")).toBeInTheDocument();
		expect(within(c).getByText("screens/widget")).toBeInTheDocument();
		expect(within(c).getByText("none")).toBeInTheDocument();
		expect(within(c).getByText("It sent me to the wrong screen")).toBeInTheDocument();
		// The page to edit is shown as text, not as a link or a button.
		expect(within(c).getByText("docs/cob/screens/widget.md")).toBeInTheDocument();
		expect(within(c).queryByRole("link", { name: /edit this help page/i })).not.toBeInTheDocument();
	});

	it("says there is nothing to review when COB did well", async () => {
		open([]);
		const c = await list();
		expect(await within(c).findByText("Nothing to review")).toBeInTheDocument();
	});

	it("asks for thumbs down only, and for another period", async () => {
		const { app, mock } = open([WIDGET, PLAN]);
		const c = await list();
		await within(c).findByText("What does the Pro plan cost?");
		await app.user.click(within(c).getByRole("radio", { name: "Thumbs down only" }));
		await waitFor(() => expect(within(c).queryByText("What does the Pro plan cost?")).not.toBeInTheDocument());
		expect(within(c).getByText("how can I put the chat on my site")).toBeInTheDocument();
		await app.user.click(within(c).getByRole("radio", { name: "90 days" }));
		await waitFor(() => expect(mock.state.reviewQueries.at(-1)).toMatchObject({ days: "90", rating: "down" }));
		expect(mock.state.reviewQueries[0]).toMatchObject({ days: "30", rating: "all", limit: "50" });
	});

	it("copies a line for the eval set", async () => {
		const { app } = open([WIDGET]);
		const c = await list();
		await app.user.click(
			await within(c).findByRole("button", { name: "Add to eval set: how can I put the chat on my site" }),
		);
		const line = JSON.parse(await navigator.clipboard.readText());
		expect(line).toMatchObject({
			role: "owner",
			language: "hi-Latn",
			question: "how can I put the chat on my site",
			expect: { behaviour: "answer", slug: "screens/widget" },
		});
	});

	it("shows an error when the list cannot be loaded", async () => {
		const { mock } = open([]);
		server.use(
			http.get(`${BASE}/api/v1/admin/cob/review`, () =>
				HttpResponse.json({ detail: "Nope", code: "boom" }, { status: 500 }),
			),
			...mock.handlers.slice(0, 0),
		);
		const c = await list();
		expect(await within(c).findByText("Couldn't load the answers to review.")).toBeInTheDocument();
	});
});

describe("review helpers", () => {
	it("names the file a help page lives in", () => {
		expect(helpPageFile("screens/kb-detail")).toBe("docs/cob/screens/kb-detail.md");
		expect(helpPageFile("guide/flow")).toBe("docs/guide.html");
		expect(helpPageFile("readme/configuration")).toBe("README.md");
		expect(helpPageFile("settings/required")).toContain("core/config.py");
		expect(helpPageFile(null)).toBeNull();
		expect(helpPageFile("unknown/x")).toBeNull();
	});

	it("builds a JSONL line the eval set accepts", () => {
		const item: CobReviewItem = { ...PLAN, role: "unknown" };
		const line = JSON.parse(evalLine(item));
		expect(line.role).toBe("owner");
		expect(line.id).toMatch(/^review-[a-z0-9-]+$/);
		expect(line.expect).toEqual({
			behaviour: "answer",
			intent: "docs",
			tool: null,
			slug: null,
			anchor: null,
			link_route: null,
			must_not_include: [],
		});
		expect(evalLine(item)).not.toContain("\n");
	});
});
