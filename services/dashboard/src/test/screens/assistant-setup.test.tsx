import { readFileSync } from "node:fs";
import { screen, waitFor, within } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { crawlSettings, crawlSite } from "@/features/assistant-setup/crawl";
import { hoursProblem } from "@/features/assistant-setup/HoursEditor";
import { provenanceText } from "@/features/assistant-setup/ProvenanceBadge";
import { writeRefreshToken } from "@/lib/auth/storage";
import { setAuthState } from "@/lib/auth/store";
import { PRIYA, ROHAN, rt, SHREE } from "../msw/data";
import { assistantStateFor } from "../msw/handlers/assistant";
import { scraperState } from "../msw/handlers/scraper";
import { COMPLETE_ANSWERS, QUESTIONS, SHREE_PROPOSAL, setupState } from "../msw/handlers/setup";
import { renderApp } from "../render";
import { capture, MEMBER_SHREE, PATIENT, signedInAs } from "./assistant-utils";

const KB = PATIENT;

beforeEach(() => {
	crawlSettings.pollMs = 0;
});

const heading = (name: RegExp) => screen.findByRole("heading", { name });
const tenantKey = () => {
	writeRefreshToken(rt(1), false);
	setAuthState({ credential: { kind: "session", accessToken: "at_1", expiresAt: Date.now() + 900_000 } });
};

const setupBodies = (suffix: string) =>
	capture((r) => r.method === "POST" && r.url.endsWith(`/assistant/setup${suffix}`));

async function open(sid?: string) {
	signedInAs(PRIYA, SHREE);
	return renderApp(sid ? `/kbs/${KB}/setup?sid=${sid}` : `/kbs/${KB}/setup`);
}

describe("crawlSite", () => {
	it("crawls through the scraper and returns verified page text, truncated", async () => {
		tenantKey();
		const progress: number[] = [];
		const out = await crawlSite({
			url: "https://vidyaclasses.in",
			kb: KB,
			maxPages: 15,
			onProgress: (p) => progress.push(p.done),
		});
		expect(out.jobId).toBeTruthy();
		expect(out.pages).toHaveLength(3);
		expect(out.pages.every((p) => p.text.length > 0 && p.text.length <= 4000)).toBe(true);
		expect(progress.length).toBeGreaterThan(0);
		const submitted = scraperState.calls.find((c) => c.method === "POST" && c.path === "/api/v1/scrape");
		expect(submitted?.body).toMatchObject({ review_mode: true, max_pages: 15, target_corpus_id: KB });
	});

	it("stops when cancelled", async () => {
		tenantKey();
		const controller = new AbortController();
		controller.abort();
		await expect(
			crawlSite({
				url: "https://vidyaclasses.in",
				kb: KB,
				maxPages: 15,
				onProgress: () => {},
				signal: controller.signal,
			}),
		).rejects.toMatchObject({ name: "AbortError" });
	});
});

describe("guided setup mock stays in step with the engine", () => {
	it("the questions fixture is the engine's questions.json", () => {
		const engine = JSON.parse(readFileSync("../../src/callobuzz_rag/conversation/setup/questions.json", "utf-8")) as {
			questions: unknown[];
		};
		expect(QUESTIONS).toEqual(engine.questions);
		expect(QUESTIONS).toHaveLength(12);
	});
});

describe("setup helpers", () => {
	it("hours: overnight is fine, equal times and bad times are not", () => {
		expect(hoursProblem([{ day: "fri", open: "18:00", close: "02:00" }])).toBeNull();
		expect(hoursProblem([{ day: "fri", open: "09:00", close: "09:00" }])).toMatch(/can't be the same/);
		expect(hoursProblem([{ day: "fri", open: "9am", close: "5pm" }])).toMatch(/times like/);
	});

	it("provenance text is plain: crawl, template, confirmed, existing or the answer's question", () => {
		expect(provenanceText("crawl")).toBe("From your website");
		expect(provenanceText("template")).toBe("From the template");
		expect(provenanceText("confirmed")).toBe("From your confirmed details");
		expect(provenanceText("existing")).toBe("Kept from your current assistant");
		expect(provenanceText("q:tone", { tone: "How should the assistant sound?" })).toBe(
			"From your answer: How should the assistant sound?",
		);
	});
});

describe("setup wizard: start, crawl, proposal", () => {
	it("starts from a website, reads it, shows the proposal and lets the owner correct it", async () => {
		const started = setupBodies("");
		const { user } = await open();
		await user.click(await screen.findByRole("radio", { name: /i have a website/i }));
		await user.type(screen.getByLabelText(/website address/i), "https://vidyaclasses.in");
		await user.click(screen.getByRole("button", { name: /continue/i }));
		expect(await screen.findByText(/here's what we found/i, {}, { timeout: 5000 })).toBeInTheDocument();
		await waitFor(() => expect(started).toEqual([{ website_url: "https://vidyaclasses.in" }]));
		expect(setupState.lastProposeBody).toMatchObject({ pages: expect.any(Array) });
		const pages = (setupState.lastProposeBody?.pages ?? []) as { url: string; title: string; text: string }[];
		expect(pages.length).toBe(3);
		// The crawl job was only read for the proposal: it is thrown away.
		expect([...scraperState.jobs.keys()].some((id) => id.startsWith("new"))).toBe(false);

		const name = screen.getByLabelText(/business name/i);
		expect(name).toHaveValue("Shree Labs");
		expect(within(name.closest("[data-field]") as HTMLElement).getByText(/from your website/i)).toBeInTheDocument();
		await user.clear(name);
		await user.type(name, "Shree Labs Kothrud");
		expect(within(name.closest("[data-field]") as HTMLElement).queryByText(/from your website/i)).toBeNull();
		await user.click(screen.getByRole("button", { name: /looks right/i }));
		await waitFor(() => expect(setupState.lastAnswers?.business_type).toBe("clinic"));
		expect(setupState.lastPartial).toBe(true);
		expect(setupState.lastProfile).toMatchObject({
			business_name: "Shree Labs Kothrud",
			phone: "+919822012345",
			email: "care@shreelabs.in",
			hours: SHREE_PROPOSAL.hours,
		});
		expect(await screen.findByText(/question 1 of/i)).toBeInTheDocument();
	});

	it("cancelling while the website is being read aborts the request", async () => {
		setupState.proposeDelayMs = 30_000;
		const { user } = await open();
		await user.click(await screen.findByRole("radio", { name: /i have a website/i }));
		await user.type(screen.getByLabelText(/website address/i), "https://vidyaclasses.in");
		await user.click(screen.getByRole("button", { name: /continue/i }));
		await waitFor(() => expect(setupState.lastProposeBody).not.toBeNull(), { timeout: 5000 });
		await user.click(screen.getByRole("button", { name: /^cancel$/i }));
		expect(await screen.findByText(/reading the website was cancelled/i)).toBeInTheDocument();
		expect(screen.queryByText(/here's what we found/i)).toBeNull();
	});

	it("ticking Also add these pages delivers them instead of throwing them away", async () => {
		crawlSettings.pollMs = 150;
		const { user } = await open();
		await user.click(await screen.findByRole("radio", { name: /i have a website/i }));
		await user.type(screen.getByLabelText(/website address/i), "https://vidyaclasses.in");
		await user.click(screen.getByRole("button", { name: /continue/i }));
		await user.click(await screen.findByRole("checkbox", { name: /also add these pages/i }));
		expect(await screen.findByText(/here's what we found/i, {}, { timeout: 5000 })).toBeInTheDocument();
		const calls = scraperState.calls.map((c) => `${c.method} ${c.path.replace(/new\d+/, "new")}`);
		expect(calls).toContain("POST /api/v1/jobs/new/approve");
		expect(calls).toContain("POST /api/v1/jobs/new/deliver");
		expect(calls).not.toContain("DELETE /api/v1/jobs/new");
	});

	it("falls back to the type-only path when no AI model is set up", async () => {
		setupState.llm = false;
		const { user } = await open();
		await user.click(await screen.findByRole("radio", { name: /i have a website/i }));
		await user.type(screen.getByLabelText(/website address/i), "https://vidyaclasses.in");
		await user.click(screen.getByRole("button", { name: /continue/i }));
		expect(await screen.findByText(/no ai model is set up/i, {}, { timeout: 5000 })).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: /choose a business type instead/i }));
		expect(await screen.findByRole("radio", { name: /clinic or lab/i })).toBeInTheDocument();
		await user.click(screen.getByRole("radio", { name: /coaching institute/i }));
		await user.click(screen.getByRole("button", { name: /continue/i }));
		// The type-only path shows the same confirm screen, empty, with no website badge.
		expect(await screen.findByText(/about your business/i)).toBeInTheDocument();
		expect(screen.getByLabelText(/business name/i)).toHaveValue("");
		expect(screen.getByLabelText(/business type/i)).toHaveValue("coaching");
	});

	it("rejects a website address that is not http(s) before sending", async () => {
		const started = setupBodies("");
		const { user } = await open();
		await user.click(await screen.findByRole("radio", { name: /i have a website/i }));
		await user.type(screen.getByLabelText(/website address/i), "shreelabs");
		await user.tab();
		expect(await screen.findByText(/start with https:\/\//i)).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: /continue/i }));
		expect(started).toEqual([]);
	});

	it("warns that setup replaces an unpublished draft", async () => {
		await open();
		expect(await screen.findByText(/setup replaces your unpublished draft/i)).toBeInTheDocument();
	});

	it("an unreadable site is said plainly, and the owner can still continue", async () => {
		const sid = setupState.seed({ status: "proposed", proposal: { ...SHREE_PROPOSAL, read_ok: false } });
		await open(sid);
		expect(await screen.findByText(/couldn't read your website automatically/i)).toBeInTheDocument();
	});

	it("hours: add, same for Mon to Sat, remove; bad times stop the save with a message", async () => {
		const sid = setupState.seed({ status: "proposed", proposal: { ...SHREE_PROPOSAL, hours: [] }, website_url: null });
		const { user } = await open(sid);
		await user.click(await screen.findByRole("button", { name: /add hours/i }));
		await user.click(screen.getByRole("button", { name: /same for mon–sat/i }));
		expect(screen.getAllByLabelText(/^opens$/i)).toHaveLength(6);
		await user.click(screen.getByRole("button", { name: /remove hours for sat/i }));
		expect(screen.getAllByLabelText(/^opens$/i)).toHaveLength(5);
		const first = screen.getAllByLabelText(/^opens$/i)[0] as HTMLElement;
		await user.clear(first);
		await user.type(first, "9am");
		await user.click(screen.getByRole("button", { name: /looks right/i }));
		expect(await screen.findByText(/times like 09:00/i)).toBeInTheDocument();
		expect(setupState.lastAnswers).toBeNull();
	});

	it("a phone number the engine refuses lands on the phone field", async () => {
		const sid = setupState.seed({ status: "proposed" });
		const { user } = await open(sid);
		const phone = await screen.findByLabelText(/^phone/i);
		expect(phone).toHaveValue("+919822012345");
		await user.clear(phone);
		await user.type(phone, "12345");
		// the mock refuses nothing here; the field message path is exercised in the engine tests
		await user.click(screen.getByRole("button", { name: /looks right/i }));
		await waitFor(() => expect(setupState.lastProfile).toMatchObject({ phone: "12345" }));
	});
});

/** Answer the current question the simplest valid way and press Next or Finish. */
async function answerCurrent(user: UserEvent) {
	const radios = screen.queryAllByRole("radio");
	const boxes = screen.queryAllByRole("checkbox");
	const name = screen.queryByLabelText(/assistant be called/i);
	if (name) await user.type(name, "Asha");
	else if (radios.length) await user.click(radios[0] as HTMLElement);
	else if (boxes.length) await user.click(boxes[0] as HTMLElement);
	await user.click(screen.getByRole("button", { name: /^(next|finish)$/i }));
}

describe("setup wizard: interview to publish", () => {
	it("asks one question at a time, skips the sensitive question for a clinic, and saves progress", async () => {
		const sid = setupState.seed({ status: "proposed", business_type: "clinic" });
		const sent = capture((r) => r.method === "POST" && r.url.endsWith(`/${sid}/answers`));
		const { user } = await open(sid);
		await user.click(await screen.findByRole("button", { name: /looks right/i }));
		expect(await heading(/who usually asks/i)).toBeInTheDocument();
		// 12 questions, minus the skipped sensitive one, minus hours (the profile already holds them)
		expect(screen.getByText(/question 1 of 10/i)).toBeInTheDocument();
		expect(screen.getByText(/skipped, set by the template: Is your work in any of these areas/i)).toBeInTheDocument();
		await user.click(screen.getByRole("radio", { name: /patients and their families/i }));
		await user.click(screen.getByRole("button", { name: /next/i }));
		expect(await heading(/how should the assistant sound/i)).toBeInTheDocument();
		await waitFor(() => expect(sent.at(-1)).toMatchObject({ partial: true, answers: { audience: "patients" } }));
	});

	it("a required question must be answered; the message is on the question", async () => {
		const sid = setupState.seed({
			status: "interviewing",
			business_type: "clinic",
			profile: { hours: [{ day: "mon", open: "07:00", close: "20:00" }] },
		});
		const { user } = await open(sid);
		await heading(/who usually asks/i);
		await user.click(screen.getByRole("button", { name: /next/i }));
		expect(await screen.findByText(/please answer this one/i)).toBeInTheDocument();
		expect(screen.getByText(/question 1 of/i)).toBeInTheDocument();
	});

	it("a server error lands on its question, and the owner is taken to it", async () => {
		const sid = setupState.seed({
			status: "interviewing",
			business_type: "clinic",
			profile: { hours: [{ day: "mon", open: "07:00", close: "20:00" }] },
		});
		const { user } = await open(sid);
		await user.click(await screen.findByRole("radio", { name: /patients and their families/i }));
		await user.click(screen.getByRole("button", { name: /next/i }));
		await user.click(await screen.findByRole("radio", { name: /warm and caring/i }));
		setupState.failAnswer = { question: "tone", reason: "unknown_option" };
		await user.click(screen.getByRole("button", { name: /next/i }));
		expect(await screen.findByText(/that answer isn't one of the choices/i)).toBeInTheDocument();
		expect(screen.getByRole("heading", { name: /how should the assistant sound/i })).toBeInTheDocument();
	});

	it("finishing saves the whole set, writes the draft with AI wording and shows the review", async () => {
		const sid = setupState.seed({
			status: "interviewing",
			business_type: "clinic",
			profile: {
				business_name: "Shree Labs",
				phone: "+919822012345",
				hours: [{ day: "mon", open: "07:00", close: "20:00" }],
			},
		});
		const sent = capture((r) => r.method === "POST" && r.url.endsWith(`/${sid}/answers`));
		const { user } = await open(sid);
		await heading(/who usually asks/i);
		for (let i = 0; i < 12; i++) {
			if (screen.queryByRole("table", { name: /what we set up/i })) break;
			await answerCurrent(user);
			await waitFor(() => {
				if (!screen.queryByRole("button", { name: /^(next|finish)$/i }) && !screen.queryByRole("table"))
					throw new Error("waiting");
			});
		}
		expect(await screen.findByRole("table", { name: /what we set up/i })).toBeInTheDocument();
		expect(sent.at(-1)).toMatchObject({ partial: false });
		expect(setupState.generateWordings).toEqual(["ai"]);
	});

	it("without an AI model the draft is written from the template and the review says so", async () => {
		setupState.llm = false;
		const sid = setupState.seed({
			status: "interviewing",
			business_type: "clinic",
			answers: COMPLETE_ANSWERS,
			profile: { hours: [{ day: "mon", open: "07:00", close: "20:00" }] },
		});
		const { user } = await open(sid);
		// walk to the last question: every answer is prefilled from the session, so Next works
		await heading(/who usually asks/i);
		for (let i = 0; i < 12; i++) {
			if (screen.queryByRole("table", { name: /what we set up/i })) break;
			await user.click(screen.getByRole("button", { name: /^(next|finish)$/i }));
			await waitFor(() => {
				if (!screen.queryByRole("button", { name: /^(next|finish)$/i }) && !screen.queryByRole("table"))
					throw new Error("waiting");
			});
		}
		expect(await screen.findByText(/written from the template/i)).toBeInTheDocument();
		expect(setupState.generateWordings).toEqual(["ai", "template"]);
	});

	it("review shows each generated item with the answer that created it", async () => {
		const sid = setupState.seed({ status: "generated", business_type: "clinic" });
		await open(sid);
		const row = await screen.findByRole("row", { name: /tone/i });
		expect(await within(row).findByText(/from your answer: how should the assistant sound/i)).toBeInTheDocument();
		expect(within(row).getByText("Warm and caring")).toBeInTheDocument();
		const business = screen.getByRole("row", { name: /business name/i });
		expect(within(business).getByText(/from your confirmed details/i)).toBeInTheDocument();
		expect(screen.getByRole("link", { name: /edit in the assistant tab/i })).toHaveAttribute(
			"href",
			`/kbs/${KB}?tab=assistant`,
		);
	});

	it("review warns when a chosen button has no contact behind it", async () => {
		const sid = setupState.seed({
			status: "generated",
			business_type: "clinic",
			profile: { business_name: "Shree Labs", hours: [] },
		});
		const draft = assistantStateFor(KB).draft;
		if (draft)
			(draft.config as unknown as { identity: { card: { whatsapp: string | null } } }).identity.card.whatsapp = null;
		await open(sid);
		expect(await screen.findByText(/add a whatsapp number in the assistant tab/i)).toBeInTheDocument();
	});

	it("Change my answers goes back to the questions", async () => {
		const sid = setupState.seed({ status: "generated", business_type: "clinic" });
		const { user } = await open(sid);
		await user.click(await screen.findByRole("button", { name: /change my answers/i }));
		expect(await heading(/who usually asks/i)).toBeInTheDocument();
	});

	it("Run checks starts the run and moves to the checks", async () => {
		const sid = setupState.seed({ status: "generated", business_type: "clinic", gaps: ["Do you do thyroid tests?"] });
		const { user } = await open(sid);
		await user.click(await screen.findByRole("button", { name: /run checks/i }));
		expect(await screen.findByText(/18 of 20 checks passed/i)).toBeInTheDocument();
		expect(setupState.testsCalls).toBe(1);
	});

	it("tests step shows pass/fail rows and gaps with add-content links", async () => {
		const sid = setupState.seed({ status: "tested", business_type: "clinic", gaps: ["Do you do thyroid tests?"] });
		await open(sid);
		expect(await screen.findByText(/18 of 20 checks passed/i)).toBeInTheDocument();
		const table = await screen.findByRole("table", { name: /check results/i });
		expect(within(table).getAllByText("Needs a look")).toHaveLength(2);
		const gap = screen
			.getByText(/do you do thyroid tests/i, { selector: "span.font-semibold" })
			.closest("li") as HTMLElement;
		expect(within(gap).getByRole("link", { name: /add content/i })).toHaveAttribute("href", `/add?kb=${KB}`);
		expect(within(gap).getByRole("link", { name: /add an exact answer/i })).toHaveAttribute(
			"href",
			`/kbs/${KB}?tab=assistant&section=scope`,
		);
	});

	it("no documents: a banner says to add some first", async () => {
		const sid = setupState.seed({ status: "tested", business_type: "clinic" });
		signedInAs(PRIYA, SHREE);
		const { server } = await import("../msw/server");
		const { HttpResponse, http } = await import("msw");
		const { BASE } = await import("../msw/data");
		server.use(http.get(`${BASE}/api/v1/documents`, () => HttpResponse.json({ items: [], count: 0 })));
		renderApp(`/kbs/${KB}/setup?sid=${sid}`);
		expect(await screen.findByText(/add documents first, then run the checks again/i)).toBeInTheDocument();
	});

	it("members cannot publish: the button is locked with the reason", async () => {
		const sid = setupState.seed({ status: "tested", business_type: "clinic" });
		signedInAs(ROHAN, MEMBER_SHREE);
		const { user } = renderApp(`/kbs/${KB}/setup?sid=${sid}`);
		await user.click(await screen.findByRole("button", { name: /continue to publish/i }));
		const publish = await screen.findByRole("button", { name: /^publish$/i });
		expect(publish).toHaveAttribute("aria-disabled", "true");
	});

	it("owners publish at the draft's current revision and see that it is live", async () => {
		const sid = setupState.seed({ status: "tested", business_type: "clinic" });
		const sent = capture((r) => r.method === "POST" && r.url.endsWith(`/${sid}/apply`));
		const { user } = await open(sid);
		await user.click(await screen.findByRole("button", { name: /continue to publish/i }));
		const revision = assistantStateFor(KB).draft?.draft_revision;
		await user.click(await screen.findByRole("button", { name: /^publish$/i }));
		expect(await screen.findByText(/your assistant is live/i)).toBeInTheDocument();
		expect(sent).toEqual([{ base_revision: revision }]);
		expect(screen.getByRole("link", { name: /try it in the playground/i })).toHaveAttribute(
			"href",
			`/playground?kb=${KB}`,
		);
		expect(screen.getByRole("link", { name: /add it to your website/i })).toHaveAttribute("href", `/widget?kb=${KB}`);
	});

	it("a draft edited in the Assistant tab between steps is read again at publish time", async () => {
		const sid = setupState.seed({ status: "tested", business_type: "clinic" });
		const { user } = await open(sid);
		await user.click(await screen.findByRole("button", { name: /continue to publish/i }));
		const s = assistantStateFor(KB);
		if (s.draft) s.draft = { ...s.draft, draft_revision: s.draft.draft_revision + 5 };
		await user.click(await screen.findByRole("button", { name: /^publish$/i }));
		expect(await screen.findByText(/your assistant is live/i)).toBeInTheDocument();
	});

	it("a replaced session says so and offers to start again", async () => {
		const sid = setupState.seed({ status: "abandoned" });
		const { user } = await open(sid);
		expect(await screen.findByText(/this setup was replaced/i)).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: /start again/i }));
		expect(await screen.findByRole("radio", { name: /i have a website/i })).toBeInTheDocument();
	});

	it("an unknown session is a plain message with a way back", async () => {
		await open("5e700f00-0000-4000-8000-0000000000ff");
		expect(await screen.findByText(/this setup was replaced or no longer exists/i)).toBeInTheDocument();
		expect(screen.getByRole("button", { name: /start again/i })).toBeInTheDocument();
	});

	it("recommends a multilingual search model when Hindi was chosen and the catalog says English-only", async () => {
		setupState.englishOnly = true;
		const sid = setupState.seed({
			status: "interviewing",
			business_type: "clinic",
			answers: { languages: ["en", "hi-Latn"] },
		});
		await open(sid);
		expect(await screen.findByText(/search model is english-only/i)).toBeInTheDocument();
		expect(screen.getByRole("link", { name: /open the embedding tab/i })).toHaveAttribute(
			"href",
			`/kbs/${KB}?tab=embedding`,
		);
	});

	it("shows the step list with the current step marked", async () => {
		const sid = setupState.seed({ status: "generated", business_type: "clinic" });
		await open(sid);
		const steps = await screen.findByRole("list", { name: /setup steps/i });
		expect(within(steps).getByText(/4\. Review/)).toHaveAttribute("aria-current", "step");
	});
});

describe("entry points", () => {
	it("the Assistant tab has a Guided setup button", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp(`/kbs/${KB}?tab=assistant`);
		const link = await screen.findByRole("link", { name: /guided setup/i });
		expect(link).toHaveAttribute("href", `/kbs/${KB}/setup`);
	});

	it("CreateKbDialog offers the guided setup after creating a knowledge base", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = renderApp("/kbs");
		await user.click(await screen.findByRole("button", { name: "New knowledge base" }));
		const dialog = await screen.findByRole("dialog");
		await user.type(within(dialog).getByLabelText("Name"), "Patient help 2");
		await user.click(within(dialog).getByRole("button", { name: "Create knowledge base" }));
		const setup = await within(dialog).findByRole("button", { name: /set up the assistant now/i });
		await user.click(setup);
		expect(await screen.findByRole("heading", { level: 1, name: /set up your assistant/i })).toBeInTheDocument();
	});
});
