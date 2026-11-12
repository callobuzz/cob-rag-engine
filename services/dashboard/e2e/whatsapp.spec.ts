import { expect, test } from "@playwright/test";
import { createWorld, installMockEngine } from "./fixtures/mock-engine";
import { expectNoAxeViolations } from "./utils/axe";
import { isPhone, logIn, roleOf } from "./utils/session";

/** The Facebook SDK, stubbed: its login calls back with a code, then Facebook's origin posts the report. */
const fakeSdk = (
	event = "FINISH",
	data: Record<string, string> = { phone_number_id: "1234567890", waba_id: "5550001" },
) => `
window.FB = {
	init: function () {},
	login: function (cb) {
		cb({ authResponse: { code: "AQBcode" } });
		window.dispatchEvent(new MessageEvent("message", {
			origin: "https://www.facebook.com",
			data: JSON.stringify({ type: "WA_EMBEDDED_SIGNUP", event: ${JSON.stringify(event)},
				data: ${JSON.stringify(data)} }),
		}));
	},
};
if (window.fbAsyncInit) window.fbAsyncInit();
`;

/**
 * TV6: a business connects its WhatsApp number through Meta's window (stubbed), sees the chat and
 * replies. Workspace views only; Meta's real window is a live check for the final test.
 */
test.describe("WhatsApp (TV6)", () => {
	test.beforeEach(async ({ browserName: _b }, info) => {
		test.skip(roleOf(info) !== "owner", "Connecting is for a workspace owner; the member lock is a unit test");
	});

	test("connect, read a chat and reply", async ({ page, context }, info) => {
		const world = createWorld();
		world.whatsapp.enabled = true;
		await installMockEngine(context, world);
		await context.route("https://connect.facebook.net/**", (route) =>
			route.fulfill({
				contentType: "application/javascript",
				headers: { "access-control-allow-origin": "*" },
				body: fakeSdk(),
			}),
		);
		await logIn(page, roleOf(info));
		await page.goto("/whatsapp");
		await expect(page.getByRole("heading", { level: 1, name: "WhatsApp" })).toBeVisible();
		await page.getByRole("button", { name: "Connect with Meta" }).click();
		await expect(page.getByTestId("wa-phone")).toContainText("+91 98200 11111");
		expect(world.whatsapp.sent[0]?.body).toMatchObject({ code: "AQBcode", phone_number_id: "1234567890" });
		await page.getByRole("button", { name: "Open chat with Asha K." }).click();
		const chat = page.getByTestId("wa-chat");
		await expect(chat).toContainText("Root canal ka kitna charge hai?");
		await chat.getByLabel("Your reply").fill("We open at 9.");
		await chat.getByRole("button", { name: "Send" }).click();
		await expect(page.getByTestId("wa-paused")).toBeVisible();
		if (isPhone(page)) {
			expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
		}
		await expectNoAxeViolations(page, "whatsapp");
	});

	test("a ticked coexistence box does not skip registering a normal number", async ({ page, context }, info) => {
		const world = createWorld();
		world.whatsapp.enabled = true;
		await installMockEngine(context, world);
		await context.route("https://connect.facebook.net/**", (route) =>
			route.fulfill({
				contentType: "application/javascript",
				headers: { "access-control-allow-origin": "*" },
				body: fakeSdk(),
			}),
		);
		await logIn(page, roleOf(info));
		await page.goto("/whatsapp");
		await page.getByRole("checkbox").first().check();
		await page.getByRole("button", { name: "Connect with Meta" }).click();
		await expect(page.getByTestId("wa-phone")).toContainText("+91 98200 11111");
		// Meta's event was a plain FINISH, so the engine must register the number.
		expect(world.whatsapp.sent[0]?.body).toMatchObject({ coexistence: false });
	});

	test("a finish with no phone number is an error, not a spinner", async ({ page, context }, info) => {
		const world = createWorld();
		world.whatsapp.enabled = true;
		await installMockEngine(context, world);
		await context.route("https://connect.facebook.net/**", (route) =>
			route.fulfill({
				contentType: "application/javascript",
				headers: { "access-control-allow-origin": "*" },
				body: fakeSdk("FINISH_ONLY_WABA", { waba_id: "5550001" }),
			}),
		);
		await logIn(page, roleOf(info));
		await page.goto("/whatsapp");
		const button = page.getByRole("button", { name: "Connect with Meta" });
		await button.click();
		await expect(page.getByTestId("embedded-signup").getByRole("alert")).toContainText(
			"without giving us a phone number",
		);
		await expect(button).toBeEnabled();
		expect(world.whatsapp.sent).toHaveLength(0);
	});
});
