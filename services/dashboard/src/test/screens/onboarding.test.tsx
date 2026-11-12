import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { uploadQueue } from "@/features/add-content/upload-queue"; // TV5
import { developerMailto, developerWhatsapp } from "@/features/onboarding/install-guides";
import { onboardingStrings } from "@/lib/strings/onboarding";
import { BASE, PRIYA, SHREE } from "../msw/data";
import { homeFor } from "../msw/handlers/home";
import { ingestState } from "../msw/handlers/ingest"; // TV5
import { FRESH, KB, onboardingMock, SNIPPET, startedState } from "../msw/handlers/onboarding";
import { WK_LIVE } from "../msw/handlers/widget-keys";
import { server } from "../msw/server";
import { renderApp } from "../render";
import { MEMBER_SHREE, signedInAs } from "./assistant-utils";

/**
 * jsdom's XMLHttpRequest cannot send a FormData holding a File through MSW ("reading _buffer"), so
 * the picture goes as the same field over fetch (the file as "file:<name>"). upload.ts is D1's.
 */
vi.mock("@/lib/api/upload", async () => {
	const { errorFromBody, toApiError } = await import("@/lib/api/errors");
	const { currentRagUrl } = await import("@/lib/auth/store");
	return {
		upload: async (opts: { path: string; buildForm: () => FormData }) => {
			const params = new URLSearchParams();
			for (const [k, v] of opts.buildForm().entries()) params.append(k, typeof v === "string" ? v : `file:${v.name}`);
			let res: Response;
			try {
				res = await fetch(`${currentRagUrl()}${opts.path}`, { method: "POST", body: params });
			} catch (e) {
				throw toApiError(e);
			}
			const text = await res.text();
			const body: unknown = text ? JSON.parse(text) : null;
			if (res.ok) return body;
			throw errorFromBody(res.status, body, res.headers);
		},
	};
});

// jsdom has no object URLs; the picture preview needs one
beforeEach(() => {
	URL.createObjectURL = () => "blob:preview";
	URL.revokeObjectURL = () => {};
});

async function open(path: string) {
	const app = renderApp(path);
	await screen.findByRole("heading", { level: 1, name: "Welcome to Sahayak AI" });
	return app;
}

async function fillWebsite(app: Awaited<ReturnType<typeof open>>, value = "shreedental.in") {
	await app.user.type(await screen.findByLabelText("Your website"), value);
}

describe("first-run steps: your website", () => {
	it("greets with the deployer's product name and never says corpus or the engine's name", async () => {
		signedInAs(PRIYA, SHREE);
		await open("/welcome");
		// the "Powered by" footer is the one place the engine's own name stays; it is not checked here
		const page = [
			screen.getByRole("heading", { level: 1 }).textContent,
			(await screen.findByRole("region", { name: "Step 1 of 4" })).textContent,
		].join(" ");
		expect(page).toContain("Welcome to Sahayak AI");
		expect(page).not.toMatch(/corpus/i);
		expect(page).not.toContain("cob-rag-engine");
		expect(JSON.stringify(onboardingStrings)).not.toMatch(/corpus|cob-rag-engine/i);
		expect(screen.getByText("Step 1 of 4")).toBeInTheDocument();
		expect(screen.getByLabelText("What should your assistant be called?")).toHaveAttribute(
			"placeholder",
			"Shree Labs Assistant",
		);
	});

	it("asks for a website like shreedental.in before anything is sent", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await open("/welcome");
		await app.user.click(await screen.findByRole("button", { name: "Continue" }));
		expect(await screen.findByText("Enter your website like shreedental.in.")).toBeInTheDocument();
		expect(onboardingMock.started).toEqual([]);
	});

	it("sets everything up from the address and the details, then moves to the install step", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await open("/welcome");
		await fillWebsite(app, "www.shreedental.in");
		await app.user.type(screen.getByLabelText("What should your assistant be called?"), "Shree Dental Assistant");
		await app.user.selectOptions(screen.getByLabelText("What kind of business is it?"), "clinic");
		await app.user.type(screen.getByLabelText("WhatsApp number (optional)"), "+91 98220 12345");
		await app.user.click(screen.getByRole("button", { name: "Continue" }));
		await waitFor(() => expect(onboardingMock.started).toHaveLength(1));
		expect(onboardingMock.started[0]).toEqual({
			website: "www.shreedental.in",
			assistant_name: "Shree Dental Assistant",
			business_type: "clinic",
			whatsapp: "+91 98220 12345",
		});
		await screen.findByRole("heading", { name: "Add your brochures, price lists and menus" });
		expect(app.router.state.location.search).toMatchObject({ step: 2 });
		expect(screen.getByText("Step 2 of 4")).toBeInTheDocument();
	});

	it("sends opening hours the way the engine wants them and checks them first", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await open("/welcome");
		await fillWebsite(app);
		await app.user.click(screen.getByRole("button", { name: /Add hours/ }));
		await app.user.clear(screen.getByLabelText("Opens"));
		await app.user.type(screen.getByLabelText("Opens"), "10:00");
		await app.user.clear(screen.getByLabelText("Closes"));
		await app.user.type(screen.getByLabelText("Closes"), "10:00");
		await app.user.click(screen.getByRole("button", { name: "Continue" }));
		expect(onboardingMock.started).toEqual([]); // equal times: refused here, not by the engine
		await app.user.clear(screen.getByLabelText("Closes"));
		await app.user.type(screen.getByLabelText("Closes"), "19:00");
		await app.user.click(screen.getByRole("button", { name: "Continue" }));
		await waitFor(() => expect(onboardingMock.started).toHaveLength(1));
		expect(onboardingMock.started[0]?.hours).toEqual([{ day: "mon", open: "10:00", close: "19:00" }]);
	});

	it("shows the engine's own sentence for an address it refuses", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.post(`${BASE}/api/v1/onboarding/website`, () =>
				HttpResponse.json(
					{ detail: "We cannot reach that address from here.", code: "website_blocked" },
					{ status: 422 },
				),
			),
		);
		const app = await open("/welcome");
		await fillWebsite(app, "internal.example");
		await app.user.click(screen.getByRole("button", { name: "Continue" }));
		const alert = await screen.findByRole("alert");
		expect(alert).toHaveTextContent("We can't reach that website from here.");
		expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
	});

	it("a failing step shows that step's message and Try again retries the same request", async () => {
		signedInAs(PRIYA, SHREE);
		onboardingMock.failStart = {
			status: 409,
			body: { detail: "Widget key limit reached (1)", code: "widget_key_limit_reached", limit: 1 },
		};
		const app = await open("/welcome");
		await fillWebsite(app);
		await app.user.click(screen.getByRole("button", { name: "Continue" }));
		const alert = await screen.findByRole("alert");
		expect(alert).toHaveTextContent(/widget/i);
		onboardingMock.failStart = null;
		await app.user.click(within(alert).getByRole("button", { name: "Try again" }));
		await screen.findByRole("heading", { name: "Add your brochures, price lists and menus" });
		expect(onboardingMock.started).toHaveLength(2);
	});

	it("uploads the chosen picture once the knowledge base exists, then puts it in the assistant", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await open("/welcome");
		await fillWebsite(app);
		await app.user.upload(
			screen.getByLabelText("Its picture"),
			new File([new Uint8Array(200)], "logo.png", { type: "image/png" }),
		);
		expect(await screen.findByTestId("avatar-preview")).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Continue" }));
		await screen.findByRole("heading", { name: "Add your brochures, price lists and menus" });
		expect(onboardingMock.avatars).toEqual(["logo.png"]);
		expect(onboardingMock.started).toHaveLength(2);
		expect(onboardingMock.started[1]?.avatar_url).toBe(
			"/api/v1/public/avatars/6f0a1b2c-3d4e-4f50-8a6b-7c8d9e0f1a2b.png",
		);
	});

	it("a refused picture keeps the owner on step 1 with the reason, the assistant is already set up", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await open("/welcome");
		await fillWebsite(app);
		// a file the browser's own filter would hide (SVG) can still be dropped in: the engine says no
		fireEvent.change(screen.getByLabelText("Its picture"), {
			target: { files: [new File(["<svg/>"], "logo.svg", { type: "image/svg+xml" })] },
		});
		await app.user.click(screen.getByRole("button", { name: "Continue" }));
		expect(await screen.findByText(/not a usable picture/)).toBeInTheDocument();
		expect(screen.getByText(/add it from the Assistant page/i)).toBeInTheDocument();
		expect(onboardingMock.started).toHaveLength(1);
	});

	it("skip for now dismisses the steps and goes Home", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await open("/welcome");
		await app.user.click(screen.getByRole("button", { name: "Skip for now" }));
		await waitFor(() => expect(onboardingMock.patches).toEqual([{ status: "dismissed" }]));
		await waitFor(() => expect(app.pathname()).toBe("/"));
	});

	it("a member sees that the owner is setting things up", async () => {
		signedInAs(PRIYA, MEMBER_SHREE);
		renderApp("/welcome");
		expect(await screen.findByText("Your owner is setting things up")).toBeInTheDocument();
		expect(screen.queryByLabelText("Your website")).toBeNull();
		expect(screen.queryByRole("button", { name: "Skip for now" })).toBeNull();
	});
});

// TV5: step 2, brochures and price lists
describe("first-run steps: brochures and price lists", () => {
	const at2 = async (over = {}) => {
		signedInAs(PRIYA, SHREE);
		onboardingMock.state = startedState({ step: 2, ...over });
		const app = renderApp("/welcome?step=2");
		await screen.findByRole("heading", { name: "Add your brochures, price lists and menus" });
		return app;
	};
	afterEach(() => uploadQueue.reset());

	it("tells the owner that PDFs and pictures of a price list or menu work", async () => {
		await at2();
		expect(screen.getByText("PDF or pictures of a price list, a menu or a brochure all work.")).toBeInTheDocument();
	});

	it("offers an upload zone, and Skip this step goes to the install step", async () => {
		const app = await at2();
		expect(screen.getByTestId("dropzone")).toBeInTheDocument();
		expect(screen.queryByRole("button", { name: "Next" })).toBeNull();
		await app.user.click(screen.getByRole("button", { name: "Skip this step" }));
		await screen.findByRole("heading", { name: "Add your assistant to your website" });
		expect(app.router.state.location.search).toMatchObject({ step: 3 });
		expect(onboardingMock.patches).toContainEqual({ step: 3 });
	});

	it("uploads a price list to the business's own knowledge base, then offers Next", async () => {
		const app = await at2();
		const file = new File([new Uint8Array(2048)], "price-list-oct.pdf", { type: "application/pdf" });
		fireEvent.change(await screen.findByTestId("file-input"), { target: { files: [file] } });
		await app.user.click(await screen.findByRole("button", { name: "Upload 1 file" }));
		await waitFor(() => expect(ingestState.uploads).toHaveLength(1));
		expect(ingestState.uploads[0]).toMatchObject({ corpus_id: KB, file: "file:price-list-oct.pdf" });
		await app.user.click(await screen.findByRole("button", { name: "Next" }));
		await screen.findByRole("heading", { name: "Add your assistant to your website" });
	});

	it("says it is still reading, then that it has read everything", async () => {
		await at2({ files: { count: 2, learned: 1 } });
		const status = await screen.findByTestId("brochure-status");
		expect(status).toHaveTextContent("Your assistant is reading them… Your assistant has read 1 of 2 files.");
		onboardingMock.state = startedState({ step: 2, files: { count: 2, learned: 2 } });
		await waitFor(() => expect(screen.getByTestId("brochure-status")).toHaveTextContent("has read all your files"), {
			timeout: 8_000,
		});
	}, 15_000);

	it("an owner who was on install before the update resumes on install", async () => {
		signedInAs(PRIYA, SHREE);
		onboardingMock.state = startedState({ step: 3 });
		renderApp("/welcome");
		await screen.findByRole("heading", { name: "Add your assistant to your website" });
		expect(screen.getByText("Step 3 of 4")).toBeInTheDocument();
	});

	it("never says corpus or the engine's name", async () => {
		await at2();
		expect(JSON.stringify(onboardingStrings.brochures)).not.toMatch(/corpus|cob-rag-engine/i);
	});
});
// /TV5

describe("first-run steps: add it to your website", () => {
	const at2 = async (over = {}) => {
		signedInAs(PRIYA, SHREE);
		onboardingMock.state = startedState({ step: 3, ...over });
		const app = renderApp("/welcome?step=3");
		await screen.findByRole("heading", { name: "Add your assistant to your website" });
		return app;
	};

	it("shows the code with one Copy button and steps for every platform", async () => {
		const app = await at2();
		expect(screen.getByTestId("install-snippet")).toHaveTextContent(SNIPPET);
		expect(screen.getByRole("button", { name: "Copy code" })).toBeInTheDocument();
		expect(screen.getByRole("tab", { name: "Any website" })).toHaveAttribute("aria-selected", "true");
		const wanted: [string, RegExp][] = [
			["WordPress", /WPCode/],
			["Wix", /Custom code/],
			["Shopify", /theme\.liquid/],
			["Squarespace", /paid Squarespace plan/],
			["Google Sites", /Embed code/],
		];
		for (const [tab, words] of wanted) {
			await app.user.click(screen.getByRole("tab", { name: tab }));
			expect(await screen.findByText(words)).toBeInTheDocument();
		}
	});

	it("the developer tab builds an email and a WhatsApp message with the code, in the browser", async () => {
		const app = await at2();
		await app.user.click(screen.getByRole("tab", { name: "Send to my developer" }));
		const mail = await screen.findByRole("link", { name: "Send by email" });
		const wa = screen.getByRole("link", { name: "Send on WhatsApp" });
		expect(mail.getAttribute("href")).toBe(developerMailto(SNIPPET, "shreedental.in"));
		expect(wa.getAttribute("href")).toBe(developerWhatsapp(SNIPPET, "shreedental.in"));
		expect(mail.getAttribute("href")).toContain(encodeURIComponent(SNIPPET));
		expect(wa.getAttribute("href")).toMatch(/^https:\/\/wa\.me\/\?text=/);
		expect(wa.getAttribute("href")).toContain(encodeURIComponent(SNIPPET));
	});

	it("waits, then turns green when the engine has seen the assistant on the site", async () => {
		await at2();
		const light = screen.getByTestId("connection-light");
		expect(light).toHaveAttribute("data-state", "waiting");
		expect(within(light).getByText("Waiting for your website…")).toBeInTheDocument();
		onboardingMock.state = startedState({
			connection: {
				status: "connected",
				origin: "https://shreedental.in",
				first_seen_at: "2026-10-05T09:00:00Z",
				refused: [],
			},
		});
		// the light looks again on its own, every few seconds
		await waitFor(() => expect(screen.getByTestId("connection-light")).toHaveAttribute("data-state", "connected"), {
			timeout: 12_000,
		});
		expect(screen.getByText("Connected. Your assistant is live on shreedental.in.")).toBeInTheDocument();
	}, 20_000);

	it("a load from an address the key does not allow offers to allow it", async () => {
		const app = await at2({
			widget_key_id: WK_LIVE,
			connection: {
				status: "waiting",
				origin: null,
				first_seen_at: null,
				refused: [{ origin: "https://www.shreedental.in", last_seen_at: "2026-10-05T09:05:00Z" }],
			},
		});
		const alert = await screen.findByText(/We see your assistant on www\.shreedental\.in/);
		expect(alert).toBeInTheDocument();
		const patches: unknown[] = [];
		server.events.on("request:start", async ({ request }) => {
			if (request.method === "PATCH" && request.url.includes("/widget-keys/")) {
				patches.push(await request.clone().json());
			}
		});
		await app.user.click(screen.getByRole("button", { name: "Allow https://www.shreedental.in" }));
		await waitFor(() =>
			expect(patches).toEqual([
				{ allowed_origins: ["https://shree-labs.in", "https://www.shree-labs.in", "https://www.shreedental.in"] },
			]),
		);
	});

	it("Next is always open, even while waiting", async () => {
		const app = await at2();
		await app.user.click(screen.getByRole("button", { name: "I'll do it later" }));
		await screen.findByRole("heading", { name: "Try your assistant" });
		expect(app.router.state.location.search).toMatchObject({ step: 4 });
	});

	it("with no code yet it goes back to step 1 instead of showing an empty box", async () => {
		signedInAs(PRIYA, SHREE);
		onboardingMock.state = { ...FRESH };
		renderApp("/welcome?step=2");
		expect(await screen.findByLabelText("Your website")).toBeInTheDocument();
	});
});

describe("first-run steps: try your assistant", () => {
	const at3 = async (over = {}) => {
		signedInAs(PRIYA, SHREE);
		onboardingMock.state = startedState({ step: 4, ...over });
		const app = renderApp("/welcome?step=4");
		await screen.findByRole("heading", { name: "Try your assistant" });
		return app;
	};

	it("counts the pages read and suggests questions from the owner's own pages", async () => {
		await at3();
		expect(await screen.findByText("Learning your website: 23 pages read")).toBeInTheDocument();
		const chips = await screen.findByTestId("suggestions");
		expect(within(chips).getByRole("button", { name: "What are your prices?" })).toBeInTheDocument();
		expect(within(chips).getByRole("button", { name: "Tell me about Root Canal Treatment" })).toBeInTheDocument();
	});

	it("suggestions switch to Hinglish", async () => {
		const app = await at3();
		await app.user.click(await screen.findByRole("radio", { name: "Hinglish" }));
		const chips = await screen.findByTestId("suggestions");
		await within(chips).findByRole("button", { name: "Aapki fees kitni hai?" });
		expect(within(chips).queryByRole("button", { name: "What are your prices?" })).toBeNull();
	});

	it("says when the website is learned", async () => {
		await at3({ learning: { status: "ready", pages_learned: 60, pages_found: 60, first_pass_done: true } });
		expect(await screen.findByText("Your website is learned: 60 pages read")).toBeInTheDocument();
	});

	it("a question goes to the published assistant and its answer streams into the chat", async () => {
		const app = await at3();
		const chips = await screen.findByTestId("suggestions");
		await app.user.click(within(chips).getByRole("button", { name: "What are your prices?" }));
		const log = screen.getByTestId("try-chat-log");
		await within(log).findByText("What are your prices?");
		await waitFor(() => expect(within(log).getAllByText(/fast|lipid|water/i).length).toBeGreaterThan(0));
		expect(screen.getByTestId("try-chat-name")).toHaveTextContent("Shree Dental Assistant");
	});

	it("Go to my dashboard finishes the steps and opens Home", async () => {
		const app = await at3();
		await app.user.click(screen.getByRole("button", { name: "Go to my dashboard" }));
		await waitFor(() => expect(onboardingMock.patches).toContainEqual({ status: "done" }));
		await waitFor(() => expect(app.pathname()).toBe("/"));
	});
});

describe("Home and the first-run steps", () => {
	const noKbs = () =>
		http.get(`${BASE}/api/v1/corpora`, () =>
			HttpResponse.json({ items: [], count: 0, total: 0, offset: 0, limit: 500 }),
		);
	const emptyHome = (onboarding: { status: string; step: number }) =>
		http.get(`${BASE}/api/v1/home`, () => HttpResponse.json({ ...homeFor(null), onboarding }));

	it("a new owner with nothing set up lands on the first-run steps, once", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(noKbs(), emptyHome({ status: "new", step: 1 }));
		const app = renderApp("/");
		await screen.findByRole("heading", { level: 1, name: "Welcome to Sahayak AI" });
		expect(app.pathname()).toBe("/welcome");
		expect(window.sessionStorage.getItem("onboarding-redirected")).toBe("1");
	});

	it("after skipping, Home shows the plain empty state and does not bounce back", async () => {
		signedInAs(PRIYA, SHREE);
		window.sessionStorage.setItem("onboarding-redirected", "1");
		server.use(noKbs(), emptyHome({ status: "new", step: 1 }));
		const app = renderApp("/");
		await screen.findByText("Set up your first assistant");
		expect(app.pathname()).toBe("/");
	});

	it("a member is never sent to the steps", async () => {
		signedInAs(PRIYA, MEMBER_SHREE);
		server.use(noKbs(), emptyHome({ status: "new", step: 1 }));
		const app = renderApp("/");
		await screen.findByText("Set up your first assistant");
		expect(app.pathname()).toBe("/");
	});

	it("unfinished steps show a card to carry on from the step the owner stopped at", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.get(`${BASE}/api/v1/home`, () =>
				HttpResponse.json({ ...homeFor(KB), onboarding: { status: "in_progress", step: 2 } }),
			),
		);
		renderApp("/");
		const card = await screen.findByTestId("setup-resume");
		expect(card).toHaveTextContent("Step 2 of 4: Brochures and price lists.");
		expect(within(card).getByRole("link", { name: "Continue" })).toHaveAttribute("href", "/welcome?step=2");
	});

	it("a member sees that the owner is setting things up", async () => {
		signedInAs(PRIYA, MEMBER_SHREE);
		server.use(
			http.get(`${BASE}/api/v1/home`, () =>
				HttpResponse.json({ ...homeFor(KB), onboarding: { status: "in_progress", step: 1 } }),
			),
		);
		renderApp("/");
		expect(await screen.findByTestId("setup-member")).toBeInTheDocument();
		expect(screen.queryByTestId("setup-resume")).toBeNull();
	});

	it("finished or skipped steps can be opened again", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.get(`${BASE}/api/v1/home`, () =>
				HttpResponse.json({ ...homeFor(KB), onboarding: { status: "done", step: 4 } }),
			),
		);
		renderApp("/");
		expect(await screen.findByTestId("setup-reopen")).toHaveAttribute("href", "/welcome?step=3");
	});

	it("says whether the assistant has loaded on the website yet", async () => {
		signedInAs(PRIYA, SHREE);
		const base = homeFor(KB);
		server.use(
			http.get(`${BASE}/api/v1/home`, () =>
				HttpResponse.json({ ...base, website: { ...base.website, connected: true } }),
			),
		);
		renderApp("/");
		const line = await screen.findByTestId("site-connected");
		expect(line).toHaveAttribute("data-state", "connected");
		expect(line).toHaveTextContent("Your assistant is live on your website.");
	});

	it("Try your assistant opens the chat in a dialog", async () => {
		signedInAs(PRIYA, SHREE);
		const app = renderApp("/");
		await app.user.click(await screen.findByRole("button", { name: "Try your assistant" }));
		const dialog = await screen.findByRole("dialog", { name: "Try your assistant" });
		expect(within(dialog).getByTestId("try-chat")).toBeInTheDocument();
		expect(within(dialog).getByTestId("try-chat-name")).toHaveTextContent("Shree Dental Assistant");
	});
});

describe("the assistant's picture on the Assistant page", () => {
	it("uploads the picture and puts its path in the draft, to be published with the page's Publish bar", async () => {
		signedInAs(PRIYA, SHREE);
		const patches: unknown[] = [];
		server.use(
			http.patch(`${BASE}/api/v1/corpora/:id/assistant/draft`, async ({ request }) => {
				patches.push(await request.json());
				return HttpResponse.json({});
			}),
		);
		const app = renderApp("/assistant");
		await screen.findByRole("heading", { level: 1, name: "Assistant" });
		const picture = await screen.findByTestId("simple-avatar");
		await app.user.upload(
			within(picture).getByLabelText("Its picture"),
			new File([new Uint8Array(100)], "face.png", { type: "image/png" }),
		);
		await waitFor(() => expect(onboardingMock.avatars).toEqual(["face.png"]));
		await waitFor(() =>
			expect(patches).toEqual([
				{
					patch: { identity: { avatar_url: "/api/v1/public/avatars/6f0a1b2c-3d4e-4f50-8a6b-7c8d9e0f1a2b.png" } },
					base_revision: expect.any(Number),
				},
			]),
		);
		expect(await screen.findByText(/Picture added to your draft/)).toBeInTheDocument();
	});

	it("an uploaded picture passes the identity form's own check, so saving other fields keeps it", async () => {
		const { avatarField } = await import("@/features/assistant/schemas");
		const ok = (avatar_url: string) => avatarField.safeParse(avatar_url).success;
		expect(ok("/api/v1/public/avatars/6f0a1b2c-3d4e-4f50-8a6b-7c8d9e0f1a2b.png")).toBe(true);
		expect(ok("https://shreedental.in/bot.png")).toBe(true);
		expect(ok("/api/v1/public/avatars/../x")).toBe(false);
		expect(ok("javascript:alert(1)")).toBe(false);
	});
});
