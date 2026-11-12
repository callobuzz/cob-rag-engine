import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SDK_SCRIPT_ID, unloadFacebookSdk } from "@/features/whatsapp/signup";
import { PRIYA, SHREE } from "../msw/data";
import { infoWithWhatsapp, THREAD_OLD, THREAD_OPEN, whatsappState } from "../msw/handlers/whatsapp";
import { server } from "../msw/server";
import { renderApp } from "../render";
import { MEMBER_SHREE, signedInAs } from "./assistant-utils";

/** Meta's sign-up report, as the window posts it (from https://www.facebook.com). */
function metaPosts(event: string, data: Record<string, unknown> = {}, origin = "https://www.facebook.com") {
	act(() => {
		window.dispatchEvent(
			new MessageEvent("message", {
				origin,
				data: JSON.stringify({ type: "WA_EMBEDDED_SIGNUP", event, data }),
			}),
		);
	});
}

/** A stand-in for Facebook's SDK: the script never loads in jsdom, so the test starts it. */
function fakeSdk(code: string | null = "AQBcode") {
	const login = vi.fn(
		(cb: (r: { authResponse?: { code?: string } | null }) => void, _options?: Record<string, unknown>) => {
			cb({ authResponse: code ? { code } : null });
		},
	);
	return { init: vi.fn(), login };
}
async function sdkReady(sdk: ReturnType<typeof fakeSdk>) {
	await waitFor(() => expect(document.getElementById(SDK_SCRIPT_ID)).not.toBeNull());
	window.FB = sdk;
	act(() => window.fbAsyncInit?.());
}

function useWhatsapp(over: Parameters<typeof infoWithWhatsapp>[0] = {}) {
	server.use(infoWithWhatsapp(over));
}

async function openPage(user = signedInAs, who: [typeof PRIYA, typeof SHREE] = [PRIYA, SHREE]) {
	user(...who);
	server.use(infoWithWhatsapp());
	const app = renderApp("/whatsapp");
	await screen.findByRole("heading", { level: 1, name: "WhatsApp" });
	return app;
}

describe("the menu", () => {
	it("lists WhatsApp for a business only when the engine and the plan have it", async () => {
		signedInAs(PRIYA, SHREE);
		useWhatsapp();
		renderApp("/");
		await screen.findByRole("heading", { level: 1, name: "Home" });
		expect(await screen.findByRole("link", { name: "WhatsApp" })).toHaveAttribute("href", "/whatsapp");
	});

	it("hides it when the plan lacks it", async () => {
		signedInAs(PRIYA, SHREE);
		useWhatsapp({ available: false });
		renderApp("/");
		await screen.findByRole("heading", { level: 1, name: "Home" });
		await screen.findByRole("link", { name: "Knowledge" });
		expect(screen.queryByRole("link", { name: "WhatsApp" })).toBeNull();
	});

	it("and a page that does not exist on this engine is not found", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(infoWithWhatsapp({ enabled: false, available: false }));
		renderApp("/whatsapp");
		expect(await screen.findByRole("heading", { name: /page not found/i })).toBeInTheDocument();
	});
});

describe("Facebook's SDK", () => {
	it("is added when the WhatsApp page shows its sign-up button, and removed when it goes", async () => {
		signedInAs(PRIYA, SHREE);
		useWhatsapp();
		const app = renderApp("/");
		await screen.findByRole("heading", { level: 1, name: "Home" });
		expect(document.getElementById(SDK_SCRIPT_ID)).toBeNull(); // no other page talks to Facebook
		await app.router.navigate({ href: "/whatsapp" });
		await screen.findByRole("button", { name: "Connect with Meta" });
		expect(document.getElementById(SDK_SCRIPT_ID)?.getAttribute("src")).toBe(
			"https://connect.facebook.net/en_US/sdk.js",
		);
		await app.router.navigate({ href: "/" });
		await screen.findByRole("heading", { level: 1, name: "Home" });
		await waitFor(() => expect(document.getElementById(SDK_SCRIPT_ID)).toBeNull());
	});
});

describe("connecting a number", () => {
	it("by Meta's window: the code and the session info are joined and sent, then the number shows", async () => {
		await openPage();
		const sdk = fakeSdk();
		await sdkReady(sdk);
		const button = await screen.findByRole("button", { name: "Connect with Meta" });
		await waitFor(() => expect(button).toBeEnabled());
		await app_click(button);
		expect(sdk.login).toHaveBeenCalledTimes(1);
		const options = sdk.login.mock.calls[0]?.[1] as Record<string, unknown>;
		expect(options).toMatchObject({ config_id: "99999", response_type: "code", override_default_response_type: true });
		metaPosts("FINISH", { phone_number_id: "1234567890", waba_id: "5550001" });
		expect(await screen.findByTestId("wa-phone")).toHaveTextContent("+91 98200 11111");
		expect(whatsappState.sent[0]).toMatchObject({
			method: "POST",
			path: "/embedded-signup",
			body: { code: "AQBcode", phone_number_id: "1234567890", waba_id: "5550001", coexistence: false },
		});
		expect(screen.getByText(/You pay Meta for the messages/)).toBeInTheDocument();
	});

	it("a report from another origin is ignored: nothing is sent", async () => {
		await openPage();
		const sdk = fakeSdk();
		await sdkReady(sdk);
		await app_click(await screen.findByRole("button", { name: "Connect with Meta" }));
		metaPosts("FINISH", { phone_number_id: "1234567890", waba_id: "5550001" }, "https://evil.example");
		await new Promise((r) => setTimeout(r, 50));
		expect(whatsappState.sent).toEqual([]);
		expect(screen.queryByTestId("wa-number")).toBeNull();
	});

	it("a cancel in Meta's window changes nothing", async () => {
		await openPage();
		const sdk = fakeSdk(null);
		await sdkReady(sdk);
		await app_click(await screen.findByRole("button", { name: "Connect with Meta" }));
		expect(await screen.findByText("The sign-in was cancelled. Nothing was changed.")).toBeInTheDocument();
		expect(whatsappState.sent).toEqual([]);
	});

	it("a step Meta fails is named in plain words and nothing is connected", async () => {
		whatsappState.failStep = "register";
		await openPage();
		const sdk = fakeSdk();
		await sdkReady(sdk);
		await app_click(await screen.findByRole("button", { name: "Connect with Meta" }));
		metaPosts("FINISH", { phone_number_id: "1234567890", waba_id: "5550001" });
		expect(await screen.findByText(/couldn't switch the number on/)).toBeInTheDocument();
		expect(screen.queryByTestId("wa-number")).toBeNull();
	});

	it("the 'also used in the app' choice is sent as coexistence", async () => {
		await openPage();
		const sdk = fakeSdk();
		await sdkReady(sdk);
		await app_click(await screen.findByRole("checkbox", { name: /also use this number in the WhatsApp Business app/ }));
		await app_click(await screen.findByRole("button", { name: "Connect with Meta" }));
		// Coexistence comes from Meta's event alone (signup.ts); the checkbox only asks Meta for that flow.
		metaPosts("FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING", { phone_number_id: "1234567890", waba_id: "5550001" });
		await screen.findByTestId("wa-phone");
		expect(whatsappState.sent[0]?.body).toMatchObject({ coexistence: true });
		expect((sdk.login.mock.calls[0]?.[1] as { extras: { featureType?: string } } | undefined)?.extras.featureType).toBe(
			"whatsapp_business_app_onboarding",
		);
	});

	it("a member sees the button locked and cannot start it", async () => {
		await openPage(signedInAs, [PRIYA, MEMBER_SHREE]);
		const button = await screen.findByRole("button", { name: "Connect with Meta" });
		const locked = button.closest("[data-locked]");
		expect(locked).not.toBeNull();
		await app_click(button);
		expect(whatsappState.sent).toEqual([]);
	});

	it("without the sign-up window set up, the page says to ask the provider", async () => {
		whatsappState.embeddedSignup = false;
		await openPage();
		expect(await screen.findByTestId("wa-no-embedded")).toHaveTextContent(/Ask .* to connect your number for you/);
		unloadFacebookSdk();
	});
});

async function app_click(el: HTMLElement) {
	const { default: userEvent } = await import("@testing-library/user-event");
	await userEvent.setup().click(el);
}

describe("a connected number", () => {
	const connected = () => {
		whatsappState.connected = true;
		whatsappState.setup = "manual";
		whatsappState.meta_billing = "deployer";
	};

	it("shows its status, who pays Meta and checks the connection", async () => {
		connected();
		await openPage();
		expect(await screen.findByTestId("wa-phone")).toHaveTextContent("+91 98200 11111");
		expect(screen.getByText("Connected")).toBeInTheDocument();
		expect(screen.getByText(/Meta's message fees are handled by/)).toBeInTheDocument();
		await app_click(screen.getByRole("button", { name: "Check the connection" }));
		await waitFor(() => expect(whatsappState.sent.at(-1)?.path).toBe("/check"));
		expect(await screen.findByText("Meta can reach your number.")).toBeInTheDocument();
	});

	it("disconnecting asks first, then removes the number and keeps the chats", async () => {
		connected();
		await openPage();
		await app_click(await screen.findByRole("button", { name: "Disconnect" }));
		await app_click(
			await screen
				.findByRole("button", { name: "Disconnect", hidden: false })
				.then(() => screen.getAllByRole("button", { name: "Disconnect" }).at(-1) as HTMLElement),
		);
		await waitFor(() => expect(whatsappState.sent.at(-1)?.method).toBe("DELETE"));
		expect(await screen.findByTestId("embedded-signup")).toBeInTheDocument();
		expect(screen.getByTestId("wa-chats")).toBeInTheDocument();
	});

	it("saves settings and says what is wrong before sending", async () => {
		connected();
		await openPage();
		const settings = await screen.findByTestId("wa-settings");
		const pause = within(settings).getByLabelText(/After you reply, stay quiet for/);
		const { default: userEvent } = await import("@testing-library/user-event");
		const user = userEvent.setup();
		await user.clear(pause);
		await user.type(pause, "99");
		await user.click(within(settings).getByRole("button", { name: "Save" }));
		expect(await within(settings).findByText("1 to 72")).toBeInTheDocument();
		expect(whatsappState.sent).toEqual([]);
		await user.clear(pause);
		await user.type(pause, "6");
		await user.click(within(settings).getByRole("button", { name: "Save" }));
		await waitFor(() => expect(whatsappState.sent.at(-1)?.body).toMatchObject({ human_pause_hours: 6 }));
	});

	it("a follow-up cannot be switched on without its template", async () => {
		connected();
		await openPage();
		const settings = await screen.findByTestId("wa-settings");
		const { default: userEvent } = await import("@testing-library/user-event");
		const user = userEvent.setup();
		await user.click(within(settings).getByRole("switch", { name: "Send one follow-up" }));
		await user.click(within(settings).getByRole("button", { name: "Save" }));
		expect((await within(settings).findAllByText(/Add the template name and language/)).length).toBeGreaterThan(0);
		expect(whatsappState.sent).toEqual([]);
		expect(within(settings).getByText(/Meta must have approved the template first/)).toBeInTheDocument();
	});

	it("lets the owner choose the files the assistant may send", async () => {
		connected();
		await openPage();
		const card = await screen.findByTestId("wa-documents");
		const { default: userEvent } = await import("@testing-library/user-event");
		const user = userEvent.setup();
		const box = await within(card).findByRole("checkbox", { name: "test-price-list-oct.pdf" });
		await user.click(box);
		await user.click(within(card).getByRole("button", { name: "Save" }));
		await waitFor(() => expect(whatsappState.sent.at(-1)).toMatchObject({ method: "PUT", path: "/documents" }));
		expect((whatsappState.sent.at(-1)?.body as { document_ids: string[] } | undefined)?.document_ids).toHaveLength(1);
		expect(within(card).queryByRole("checkbox", { name: "Business card" })).toBeNull(); // not a PDF, JPG or PNG
	});
});

describe("the chats", () => {
	const connected = () => {
		whatsappState.connected = true;
	};

	it("lists chats with a masked name, unread and window badges, and filters unread", async () => {
		connected();
		await openPage();
		const chats = await screen.findByTestId("wa-chats");
		const first = await within(chats).findByRole("button", { name: "Open chat with Asha K." });
		expect(first).toHaveTextContent("2 unread");
		const old = within(chats).getByRole("button", { name: "Open chat with •••• 4411" });
		expect(old).toHaveTextContent("Asked not to be messaged");
		expect(old).toHaveTextContent("More than 24 hours since they wrote");
		expect(chats.textContent).not.toContain("919822012345");
		const { default: userEvent } = await import("@testing-library/user-event");
		await userEvent.setup().click(within(chats).getByRole("checkbox", { name: "Unread only" }));
		await waitFor(() => expect(within(chats).queryByRole("button", { name: "Open chat with •••• 4411" })).toBeNull());
	});

	it("opens a chat: labelled messages, the number, read; replies inside the window and pauses the assistant", async () => {
		connected();
		await openPage();
		const { default: userEvent } = await import("@testing-library/user-event");
		const user = userEvent.setup();
		await user.click(await screen.findByRole("button", { name: "Open chat with Asha K." }));
		const chat = await screen.findByTestId("wa-chat");
		expect(within(chat).getByText("+919822012345", { exact: false })).toBeInTheDocument();
		const items = within(chat).getAllByRole("listitem");
		expect(items.map((i) => i.getAttribute("data-role"))).toEqual(["user", "assistant", "user"]);
		expect(whatsappState.threads.find((t) => t.id === THREAD_OPEN)?.unread_count).toBe(0);

		const box = within(chat).getByLabelText("Your reply");
		await user.type(box, "We open at 9.");
		expect(within(chat).getByTestId("reply-count")).toHaveTextContent("13 of 4,096");
		await user.click(within(chat).getByRole("button", { name: "Send" }));
		await waitFor(() =>
			expect(whatsappState.sent.at(-1)).toMatchObject({
				method: "POST",
				path: "/reply",
				body: { text: "We open at 9." },
			}),
		);
		expect(await within(chat).findByTestId("wa-paused")).toHaveTextContent(/assistant is staying quiet/);
		expect(await within(chat).findByText("We open at 9.")).toBeInTheDocument();

		await user.click(within(chat).getByRole("button", { name: "Let the assistant answer again" }));
		await waitFor(() =>
			expect(whatsappState.sent.at(-1)).toMatchObject({ path: "/thread", body: { bot_paused: false } }),
		);
		await waitFor(() => expect(within(chat).queryByTestId("wa-paused")).toBeNull());
	});

	it("refuses to send past 4,096 characters and says so", async () => {
		connected();
		await openPage();
		const { default: userEvent } = await import("@testing-library/user-event");
		const user = userEvent.setup();
		await user.click(await screen.findByRole("button", { name: "Open chat with Asha K." }));
		const chat = await screen.findByTestId("wa-chat");
		const box = within(chat).getByLabelText("Your reply");
		await user.click(box);
		await user.paste("x".repeat(4097));
		expect(within(chat).getByTestId("reply-count")).toHaveTextContent("A reply can have up to 4,096 characters.");
		await user.click(within(chat).getByRole("button", { name: "Send" }));
		expect(whatsappState.sent.filter((s) => s.path === "/reply")).toEqual([]);
	});

	it("outside the 24-hour window the reply box is replaced by the reason", async () => {
		connected();
		await openPage();
		const { default: userEvent } = await import("@testing-library/user-event");
		await userEvent.setup().click(await screen.findByRole("button", { name: "Open chat with •••• 4411" }));
		const chat = await screen.findByTestId("wa-chat");
		expect(await within(chat).findByTestId("reply-closed")).toHaveTextContent("You can't reply here any more");
		expect(within(chat).queryByLabelText("Your reply")).toBeNull();
		expect(whatsappState.threads.find((t) => t.id === THREAD_OLD)).toBeDefined();
	});
});
