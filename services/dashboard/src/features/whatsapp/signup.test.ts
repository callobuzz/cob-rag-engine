import { afterEach, describe, expect, it, vi } from "vitest";
import {
	createSignupCollector,
	FACEBOOK_ORIGIN,
	loadFacebookSdk,
	loginOptions,
	parseSignupMessage,
	SDK_SCRIPT_ID,
	SDK_URL,
	unloadFacebookSdk,
} from "./signup";

const finish = (over: Record<string, unknown> = {}, event = "FINISH") => ({
	type: "WA_EMBEDDED_SIGNUP",
	event,
	data: { phone_number_id: "1234567890", waba_id: "5550001", business_id: "77", ...over },
});

describe("Meta's sign-up report", () => {
	it("is read from https://www.facebook.com only", () => {
		expect(parseSignupMessage(FACEBOOK_ORIGIN, finish())).toEqual({
			kind: "finish",
			phoneNumberId: "1234567890",
			wabaId: "5550001",
			coexistence: false,
		});
		for (const origin of [
			"https://evil.example",
			"https://www.facebook.com.evil.example",
			"http://www.facebook.com",
			"null",
			"",
		]) {
			expect(parseSignupMessage(origin, finish())).toBeNull();
		}
	});

	it("accepts a JSON string as well as an object", () => {
		expect(parseSignupMessage(FACEBOOK_ORIGIN, JSON.stringify(finish()))).toMatchObject({ kind: "finish" });
		expect(parseSignupMessage(FACEBOOK_ORIGIN, "not json")).toBeNull();
	});

	it("knows every finish event, and the business-app one is coexistence", () => {
		for (const event of ["FINISH", "FINISH_ONLY_WABA", "FINISH_OBO_MIGRATION", "FINISH_GRANT_ONLY_API_ACCESS"]) {
			expect(parseSignupMessage(FACEBOOK_ORIGIN, finish({}, event))).toMatchObject({
				kind: "finish",
				coexistence: false,
			});
		}
		expect(parseSignupMessage(FACEBOOK_ORIGIN, finish({}, "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING"))).toMatchObject({
			coexistence: true,
		});
	});

	it("reads a cancel and an error", () => {
		expect(parseSignupMessage(FACEBOOK_ORIGIN, { type: "WA_EMBEDDED_SIGNUP", event: "CANCEL", data: {} })).toEqual({
			kind: "cancel",
		});
		expect(parseSignupMessage(FACEBOOK_ORIGIN, { type: "WA_EMBEDDED_SIGNUP", event: "ERROR", data: {} })).toEqual({
			kind: "error",
		});
	});

	it("ignores anything else: other types, unknown events, ids that are not digits", () => {
		expect(parseSignupMessage(FACEBOOK_ORIGIN, { type: "OTHER", event: "FINISH" })).toBeNull();
		expect(parseSignupMessage(FACEBOOK_ORIGIN, { type: "WA_EMBEDDED_SIGNUP", event: "SOMETHING" })).toBeNull();
		expect(parseSignupMessage(FACEBOOK_ORIGIN, null)).toBeNull();
		expect(parseSignupMessage(FACEBOOK_ORIGIN, null)).toBeNull();
		expect(parseSignupMessage(FACEBOOK_ORIGIN, 42)).toBeNull();
	});
});

describe("a finish that has no phone number to connect", () => {
	it("is reported as incomplete, never silently dropped (the button would spin forever)", () => {
		const onlyWaba = { type: "WA_EMBEDDED_SIGNUP", event: "FINISH_ONLY_WABA", data: { waba_id: "5550001" } };
		expect(parseSignupMessage(FACEBOOK_ORIGIN, onlyWaba)).toEqual({ kind: "incomplete" });
		expect(parseSignupMessage(FACEBOOK_ORIGIN, finish({ phone_number_id: "../me" }))).toEqual({
			kind: "incomplete",
		});
		expect(parseSignupMessage(FACEBOOK_ORIGIN, finish({ waba_id: "12" }))).toEqual({ kind: "incomplete" });
		expect(parseSignupMessage(FACEBOOK_ORIGIN, { type: "WA_EMBEDDED_SIGNUP", event: "FINISH" })).toEqual({
			kind: "incomplete",
		});
	});

	it("coexistence comes from Meta's event alone, never from the browser", () => {
		expect(parseSignupMessage(FACEBOOK_ORIGIN, finish({}, "FINISH"))).toMatchObject({ coexistence: false });
		expect(parseSignupMessage(FACEBOOK_ORIGIN, finish({}, "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING"))).toMatchObject({
			coexistence: true,
		});
	});
});

describe("joining the code and the session info", () => {
	const make = (stallMs?: number) => {
		const calls = { done: vi.fn(), cancel: vi.fn(), error: vi.fn(), incomplete: vi.fn() };
		const c = createSignupCollector({
			onComplete: calls.done,
			onCancel: calls.cancel,
			onError: calls.error,
			onIncomplete: calls.incomplete,
			stallMs,
		});
		return { c, ...calls };
	};
	const session = parseSignupMessage(FACEBOOK_ORIGIN, finish());
	if (!session) throw new Error("fixture");

	it("completes when the code comes after the session info", () => {
		const { c, done } = make();
		c.setSession(session);
		expect(done).not.toHaveBeenCalled();
		c.setCode("AQB");
		expect(done).toHaveBeenCalledWith({
			code: "AQB",
			phoneNumberId: "1234567890",
			wabaId: "5550001",
			coexistence: false,
		});
	});

	it("completes when the session info comes after the code, and only once", () => {
		const { c, done } = make();
		c.setCode("AQB");
		c.setSession(session);
		c.setSession(session);
		c.setCode("AQB2");
		expect(done).toHaveBeenCalledTimes(1);
	});

	it("a cancel ends the attempt: a code arriving afterwards is ignored", () => {
		const { c, done, cancel } = make();
		c.setSession({ kind: "cancel" });
		c.setCode("AQB");
		c.setSession(session);
		expect(cancel).toHaveBeenCalledTimes(1);
		expect(done).not.toHaveBeenCalled();
	});

	it("an error from the window is reported once", () => {
		const { c, error } = make();
		c.setSession({ kind: "error" });
		c.setSession({ kind: "error" });
		expect(error).toHaveBeenCalledTimes(1);
	});

	it("closing the window without a code is a cancel, unless a code already came", () => {
		const a = make();
		a.c.abandon();
		expect(a.cancel).toHaveBeenCalledTimes(1);
		const b = make();
		b.c.setCode("AQB");
		b.c.abandon();
		expect(b.cancel).not.toHaveBeenCalled();
	});

	it("an incomplete finish ends the attempt once", () => {
		const { c, done, incomplete } = make();
		c.setCode("AQB");
		c.setSession({ kind: "incomplete" });
		c.setSession(session);
		expect(incomplete).toHaveBeenCalledTimes(1);
		expect(done).not.toHaveBeenCalled();
	});

	it("gives up when only one half arrives", () => {
		vi.useFakeTimers();
		try {
			const a = make(1000);
			a.c.setCode("AQB");
			vi.advanceTimersByTime(999);
			expect(a.incomplete).not.toHaveBeenCalled();
			vi.advanceTimersByTime(2);
			expect(a.incomplete).toHaveBeenCalledTimes(1);
			a.c.setSession(session); // too late
			expect(a.done).not.toHaveBeenCalled();

			const b = make(1000);
			b.c.setSession(session);
			vi.advanceTimersByTime(1001);
			expect(b.incomplete).toHaveBeenCalledTimes(1);

			const c = make(1000); // both in time: no stall
			c.c.setCode("AQB");
			c.c.setSession(session);
			vi.advanceTimersByTime(5000);
			expect(c.done).toHaveBeenCalledTimes(1);
			expect(c.incomplete).not.toHaveBeenCalled();

			const d = make(1000); // disposed: no stall report after the page is left
			d.c.setCode("AQB");
			d.c.dispose();
			vi.advanceTimersByTime(5000);
			expect(d.incomplete).not.toHaveBeenCalled();
		} finally {
			vi.useRealTimers();
		}
	});

	it("an empty code is not a code", () => {
		const { c, done } = make();
		c.setCode("");
		c.setSession(session);
		expect(done).not.toHaveBeenCalled();
	});
});

describe("Meta's SDK", () => {
	afterEach(() => unloadFacebookSdk());

	it("is added as one script from connect.facebook.net and removed again", async () => {
		const loading = loadFacebookSdk("123456789", "v23.0");
		const script = document.getElementById(SDK_SCRIPT_ID) as HTMLScriptElement | null;
		expect(script?.src).toBe(SDK_URL);
		const init = vi.fn();
		window.FB = { init, login: vi.fn() };
		window.fbAsyncInit?.();
		await expect(loading).resolves.toBe(window.FB);
		expect(init).toHaveBeenCalledWith({ appId: "123456789", autoLogAppEvents: true, xfbml: false, version: "v23.0" });
		// asking again does not add a second script
		void loadFacebookSdk("123456789", "v23.0");
		expect(document.querySelectorAll(`#${SDK_SCRIPT_ID}`)).toHaveLength(1);
		unloadFacebookSdk();
		expect(document.getElementById(SDK_SCRIPT_ID)).toBeNull();
		expect(window.FB).toBeUndefined();
	});

	it("rejects when the script cannot load", async () => {
		const loading = loadFacebookSdk("1", "v23.0");
		(document.getElementById(SDK_SCRIPT_ID) as HTMLScriptElement).onerror?.(new Event("error"));
		await expect(loading).rejects.toThrow(/did not load/);
	});

	it("the login options carry the configuration id and ask for a code", () => {
		expect(loginOptions("99999", false)).toMatchObject({
			config_id: "99999",
			response_type: "code",
			override_default_response_type: true,
		});
		expect((loginOptions("99999", false).extras as Record<string, unknown>).featureType).toBeUndefined();
		expect((loginOptions("99999", true).extras as Record<string, unknown>).featureType).toBe(
			"whatsapp_business_app_onboarding",
		);
	});
});

describe("only the WhatsApp page talks to Facebook", () => {
	it("no source file outside features/whatsapp names Meta's SDK or its origins", () => {
		const files = import.meta.glob(
			["/src/**/*.{ts,tsx}", "!/src/features/whatsapp/**", "!/src/**/*.test.*", "!/src/test/**"],
			{
				query: "?raw",
				import: "default",
				eager: true,
			},
		) as Record<string, string>;
		const offenders = Object.entries(files)
			.filter(
				([, text]) =>
					text.includes("connect.facebook.net") || text.includes("FB.login") || text.includes("facebook-jssdk"),
			)
			.map(([path]) => path);
		expect(offenders).toEqual([]);
	});
});
