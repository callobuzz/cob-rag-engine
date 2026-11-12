import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TokenTenant, TokenUser } from "@/lib/api/accounts-types";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, rt, SHREE, SV, tokenResponse } from "@/test/msw/data";
import { cobHandlers, cobState, resetCobState } from "@/test/msw/handlers/cob";
import { FULL_INFO } from "@/test/msw/handlers/engine";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";
import { COB_WIDTH_KEY } from "./useResizableWidth";

const realMatchMedia = window.matchMedia;
const realInnerWidth = window.innerWidth;

/** jsdom has no layout: say whether the viewport is a desktop (>= 861 px) one. */
function viewport(desktop: boolean, width = desktop ? 1440 : 500) {
	window.innerWidth = width;
	window.matchMedia = ((query: string) => ({
		matches: desktop && query.includes("861"),
		media: query,
		onchange: null,
		addEventListener: () => {},
		removeEventListener: () => {},
		addListener: () => {},
		removeListener: () => {},
		dispatchEvent: () => false,
	})) as typeof window.matchMedia;
}

function signIn(user: TokenUser = PRIYA, tenant: TokenTenant | null = SHREE) {
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

beforeEach(() => {
	resetCobState();
	sessionStorage.clear();
	localStorage.removeItem(COB_WIDTH_KEY);
	server.use(...cobHandlers);
	viewport(true);
});
afterEach(() => {
	window.matchMedia = realMatchMedia;
	window.innerWidth = realInnerWidth;
	vi.restoreAllMocks();
});

async function open(path = "/plan") {
	signIn();
	const app = renderApp(path);
	const launcher = await screen.findByRole("button", { name: /Ask COB/ });
	return { ...app, launcher };
}

async function ask(app: Awaited<ReturnType<typeof open>>, text: string) {
	const box = await screen.findByRole("textbox", { name: "Ask COB" });
	await app.user.type(box, `${text}{Enter}`);
}

describe("COB launcher and panel", () => {
	it("has a labelled launcher; it opens a complementary panel with the greeting and the screen's starters", async () => {
		const app = await open();
		expect(app.launcher).toHaveAttribute("aria-expanded", "false");
		expect(app.launcher).toHaveAccessibleName(/Ask COB \((Ctrl|⌘)\+\/\)/);
		await app.user.click(app.launcher);
		const panel = await screen.findByRole("complementary", { name: "COB assistant" });
		expect(app.launcher).toHaveAttribute("aria-expanded", "true");
		expect(within(panel).getByText(/Hi, I'm COB/)).toBeInTheDocument();
		expect(await within(panel).findByRole("button", { name: "What can I do on this screen?" })).toBeInTheDocument();
		await waitFor(() => expect(screen.getByRole("textbox", { name: "Ask COB" })).toHaveFocus());
		// Every icon-only control has a name; the footer identity is there.
		for (const name of ["Chat history", "New chat", "Close COB"]) {
			expect(within(panel).getByRole("button", { name })).toBeInTheDocument();
		}
		expect(within(panel).getByTestId("powered-by")).toHaveTextContent("cob-rag-engine by Callobuzz");
		// Desktop opens a non-modal region, not a dialog; the button points at it.
		expect(app.launcher).not.toHaveAttribute("aria-haspopup");
		expect(app.launcher).toHaveAttribute("aria-controls", panel.id);
		expect(panel.id).toBe("cob-panel");
	});

	it("on a phone the launcher announces a dialog and controls nothing docked", async () => {
		viewport(false);
		const app = await open();
		expect(app.launcher).toHaveAttribute("aria-haspopup", "dialog");
		expect(app.launcher).not.toHaveAttribute("aria-controls");
	});

	it("Ctrl+/ toggles it, also from inside a text field; Esc closes and returns focus to the opener", async () => {
		const app = await open();
		app.launcher.focus();
		fireEvent.keyDown(document, { key: "/", ctrlKey: true });
		const box = await screen.findByRole("textbox", { name: "Ask COB" });
		await waitFor(() => expect(box).toHaveFocus());
		fireEvent.keyDown(box, { key: "/", ctrlKey: true });
		await waitFor(() => expect(screen.queryByRole("complementary", { name: "COB assistant" })).toBeNull());
		await waitFor(() => expect(app.launcher).toHaveFocus());
		fireEvent.keyDown(document, { key: "/", metaKey: true });
		const again = await screen.findByRole("textbox", { name: "Ask COB" });
		fireEvent.keyDown(again, { key: "Escape" });
		await waitFor(() => expect(screen.queryByRole("complementary", { name: "COB assistant" })).toBeNull());
		await waitFor(() => expect(app.launcher).toHaveFocus());
	});

	it("is not rendered when COB is switched off on the engine", async () => {
		signIn();
		server.use(
			http.get(`${BASE}/api/v1/info`, () =>
				HttpResponse.json({
					...FULL_INFO,
					features: { ...FULL_INFO.features, cob: { ...FULL_INFO.features.cob, enabled: false } },
				}),
			),
		);
		renderApp("/plan");
		await screen.findByRole("heading", { level: 1 });
		await waitFor(() => expect(screen.queryByRole("button", { name: /Ask COB/ })).toBeNull());
	});

	it("resizes with the keyboard, keeps the width and the page makes room", async () => {
		const app = await open();
		await app.user.click(app.launcher);
		const handle = await screen.findByRole("separator", { name: "Resize COB panel" });
		expect(handle).toHaveAttribute("aria-valuenow", "420");
		expect(document.documentElement.style.getPropertyValue("--cob-w")).toBe("420px");
		handle.focus();
		await app.user.keyboard("{ArrowLeft}{ArrowLeft}");
		expect(handle).toHaveAttribute("aria-valuenow", "460");
		expect(localStorage.getItem(COB_WIDTH_KEY)).toBe("460");
		await app.user.keyboard("{End}{ArrowLeft}");
		expect(handle).toHaveAttribute("aria-valuenow", "720");
	});

	it("on a phone it is a full-screen dialog; Esc closes it", async () => {
		viewport(false);
		const app = await open();
		await app.user.click(app.launcher);
		const dialog = await screen.findByRole("dialog", { name: "COB assistant" });
		expect(within(dialog).getByRole("textbox", { name: "Ask COB" })).toBeInTheDocument();
		fireEvent.keyDown(dialog, { key: "Escape" });
		await waitFor(() => expect(screen.queryByRole("dialog", { name: "COB assistant" })).toBeNull());
	});
});

describe("COB answers", () => {
	it("a starter streams a Markdown answer with a source chip and a link that keeps the panel open", async () => {
		const app = await open();
		await app.user.click(app.launcher);
		await app.user.click(await screen.findByRole("button", { name: "What can I do on this screen?" }));
		const turn = await screen.findByTestId("cob-turn");
		await waitFor(() => expect(within(turn).getByText("Website widget", { selector: "strong" })).toBeInTheDocument());
		expect(turn).toHaveTextContent("and copy the snippet");
		const chip = await within(turn).findByRole("link", { name: "Open Website widget — Embed the widget" });
		expect(chip).toBeInTheDocument();
		expect(cobState.chats[0]?.context).toMatchObject({ route: "/plan" });
		// Done: the polite region says the final answer once; thumbs appear.
		await waitFor(() =>
			expect(screen.getByTestId("cob-live")).toHaveTextContent(
				"COB answered: Open Website widget and copy the snippet.",
			),
		);
		expect(screen.getByTestId("cob-live").textContent).not.toMatch(/\*\*|\[1\]/);
		expect(await within(turn).findByRole("button", { name: "Good answer" })).toBeInTheDocument();

		await app.user.click(within(turn).getByRole("link", { name: "Open Website widget →" }));
		await waitFor(() => expect(app.pathname()).toBe("/widget"));
		expect(screen.getByRole("complementary", { name: "COB assistant" })).toBeInTheDocument();
		expect(screen.getByTestId("cob-turn")).toBeInTheDocument();
	});

	it("search mode shows the banner and linked results, no thumbs", async () => {
		const app = await open();
		await app.user.click(app.launcher);
		await ask(app, "cap please");
		const results = await screen.findByTestId("cob-search-results");
		expect(results).toHaveTextContent("You've used this month's questions");
		expect(within(results).getByText("Website widget — Embed the widget")).toBeInTheDocument();
		expect(within(results).getByRole("link", { name: "Open" })).toBeInTheDocument();
		expect(screen.queryByRole("button", { name: "Good answer" })).toBeNull();
	});

	it("the docs-syncing error is a friendly state with a retry", async () => {
		const app = await open();
		await app.user.click(app.launcher);
		await ask(app, "sync now");
		const err = await screen.findByTestId("cob-error");
		expect(err).toHaveTextContent("COB is reading the docs for the first time");
		expect(within(err).getByRole("button", { name: "Try again" })).toBeInTheDocument();
	});

	it("a failed request shows its message and a retry that asks again", async () => {
		const app = await open();
		await app.user.click(app.launcher);
		await ask(app, "boom");
		const err = await screen.findByTestId("cob-error");
		expect(err).toHaveTextContent("COB couldn't answer just now.");
		await app.user.click(within(err).getByRole("button", { name: "Try again" }));
		await waitFor(() => expect(cobState.chats).toHaveLength(2));
	});

	it("thumbs down with a comment posts the rating and the comment", async () => {
		const app = await open();
		await app.user.click(app.launcher);
		await ask(app, "how do I embed it");
		await app.user.click(await screen.findByRole("button", { name: "Bad answer" }));
		await app.user.type(screen.getByRole("textbox", { name: /What was wrong/ }), "Wrong page");
		await app.user.click(within(screen.getByTestId("cob-feedback")).getByRole("button", { name: "Send" }));
		await waitFor(() => expect(cobState.feedback).toHaveLength(1));
		expect(cobState.feedback[0]).toMatchObject({ rating: -1, comment: "Wrong page" });
		expect(await screen.findByText("Thanks for the feedback.")).toBeInTheDocument();
	});

	it("Shift+Enter adds a line, Enter sends, the counter counts", async () => {
		const app = await open();
		await app.user.click(app.launcher);
		const box = await screen.findByRole("textbox", { name: "Ask COB" });
		await app.user.type(box, "one{Shift>}{Enter}{/Shift}two");
		expect(box).toHaveValue("one\ntwo");
		expect(screen.getByText(/7 \/ 2000/)).toBeInTheDocument();
		await app.user.keyboard("{Enter}");
		await waitFor(() => expect(cobState.chats[0]?.message).toBe("one\ntwo"));
	});
});

describe("COB history", () => {
	it("opens an earlier chat and deletes one", async () => {
		const app = await open();
		await app.user.click(app.launcher);
		await app.user.click(await screen.findByRole("button", { name: "Chat history" }));
		await app.user.click(await screen.findByRole("button", { name: "Open chat: Embedding the widget" }));
		const turn = await screen.findByTestId("cob-turn");
		expect(turn).toHaveTextContent("How do I embed the widget?");
		expect(turn).toHaveTextContent("and copy the snippet");

		await app.user.click(screen.getByRole("button", { name: "Chat history" }));
		vi.spyOn(window, "confirm").mockReturnValue(true);
		await app.user.click(await screen.findByRole("button", { name: "Delete chat: Embedding the widget" }));
		expect(await screen.findByText("No chats yet")).toBeInTheDocument();
		expect(cobState.conversations).toHaveLength(0);
	});
});

describe("COB cost note", () => {
	it("a member on a capped plan sees that COB answers count as questions", async () => {
		signIn(PRIYA, { ...SHREE, role: "member" });
		const app = renderApp("/plan");
		await app.user.click(await screen.findByRole("button", { name: /Ask COB/ }));
		const note = await screen.findByTestId("cob-cost-note");
		expect(note).toHaveTextContent("COB answers count toward your monthly questions.");
		expect(within(note).getByTestId("cob-cost-meter")).toHaveTextContent("412 of 1,000 questions used this month");
	});

	it("the operator view does not see it", async () => {
		signIn(SV, null);
		const app = renderApp("/");
		await app.user.click(await screen.findByRole("button", { name: /Ask COB/ }));
		await screen.findByRole("complementary", { name: "COB assistant" });
		expect(screen.queryByTestId("cob-cost-note")).toBeNull();
	});
});

describe("COB theme and accessibility", () => {
	it("styles with design tokens only (no hard-coded colours), so dark mode follows the theme", () => {
		const files = import.meta.glob("./*.tsx", { query: "?raw", import: "default", eager: true }) as Record<
			string,
			string
		>;
		const offenders = Object.entries(files)
			.filter(([name]) => !name.endsWith(".test.tsx"))
			.filter(([, src]) => /#[0-9a-fA-F]{3,8}\b|\b(bg|text|border)-(white|black|gray|slate|zinc|neutral)\b/.test(src))
			.map(([name]) => name);
		expect(offenders).toEqual([]);
	});

	it("announces only the stage while answering and the final text once", async () => {
		const app = await open();
		await app.user.click(app.launcher);
		const live = await screen.findByTestId("cob-live");
		expect(live).toHaveAttribute("aria-live", "polite");
		expect(live).toHaveTextContent("");
		await ask(app, "how do I embed it");
		await waitFor(() => expect(live).toHaveTextContent(/^COB answered: /));
		// Tokens are never announced one by one: the region holds one string, not a growing log.
		expect(live.textContent?.match(/COB answered/g)).toHaveLength(1);
	});

	it("does not announce an old answer opened from History", async () => {
		const app = await open();
		await app.user.click(app.launcher);
		await app.user.click(await screen.findByRole("button", { name: "Chat history" }));
		await app.user.click(await screen.findByText("Embedding the widget"));
		await screen.findByTestId("cob-turn");
		expect(screen.getByTestId("cob-live")).toHaveTextContent("");
	});

	it("narrow windows cap the docked width so the page keeps room", async () => {
		localStorage.setItem(COB_WIDTH_KEY, "720");
		viewport(true, 900);
		const app = await open();
		await app.user.click(app.launcher);
		const handle = await screen.findByRole("separator", { name: "Resize COB panel" });
		expect(handle).toHaveAttribute("aria-valuemax", "360");
		expect(handle).toHaveAttribute("aria-valuenow", "360");
	});

	it("a cancelled drag ends and later moves do not resize", async () => {
		const app = await open();
		await app.user.click(app.launcher);
		const handle = await screen.findByRole("separator", { name: "Resize COB panel" });
		fireEvent.pointerDown(handle, { clientX: 500, buttons: 1 });
		fireEvent.pointerMove(handle, { clientX: 460, buttons: 1 });
		expect(handle).toHaveAttribute("aria-valuenow", "460");
		fireEvent.pointerCancel(handle, { clientX: 460 });
		expect(localStorage.getItem(COB_WIDTH_KEY)).toBe("460");
		fireEvent.pointerMove(handle, { clientX: 300, buttons: 0 });
		expect(handle).toHaveAttribute("aria-valuenow", "460");
	});
});
