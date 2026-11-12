import { describe, expect, it } from "vitest";
import { parseRate, ratesPatch } from "@/features/branding/WhatsAppRatesCard";
import { REPLY_MAX, replyState, settingsOf, windowOpen } from "./api";
import { chatBadges } from "./ChatsList";
import { isSendable, MAX_SHAREABLE } from "./DocumentsCard";
import { manualProblems } from "./ManualConnectDialog";
import { settingsProblem } from "./SettingsCard";

describe("the reply box's rules", () => {
	it("counts characters (an emoji is one) and refuses past 4,096", () => {
		expect(replyState("")).toEqual({ count: 0, tooLong: false, empty: true });
		expect(replyState("   ")).toMatchObject({ empty: true });
		expect(replyState("Namaste 🙏")).toMatchObject({ count: 9, tooLong: false, empty: false });
		expect(replyState("x".repeat(REPLY_MAX)).tooLong).toBe(false);
		expect(replyState("x".repeat(REPLY_MAX + 1)).tooLong).toBe(true);
	});

	it("the 24-hour window mirrors the engine's", () => {
		const now = new Date("2026-10-05T12:00:00Z");
		expect(windowOpen("2026-10-05T11:00:00Z", now)).toBe(true);
		expect(windowOpen("2026-10-04T12:00:00Z", now)).toBe(true); // exactly 24 hours
		expect(windowOpen("2026-10-04T11:59:59Z", now)).toBe(false);
		expect(windowOpen(null, now)).toBe(false);
		expect(windowOpen("garbage", now)).toBe(false);
	});
});

describe("settings", () => {
	const ok = { pause: 12, followEnabled: false, template: "", language: "", after: 24 };
	it("pass when the follow-up is off, whatever is typed in its fields", () => {
		expect(settingsProblem(ok)).toEqual({});
	});
	it("pause and delay are whole hours from 1 to 72", () => {
		expect(settingsProblem({ ...ok, pause: 0 }).pause).toBeDefined();
		expect(settingsProblem({ ...ok, pause: 73 }).pause).toBeDefined();
		expect(settingsProblem({ ...ok, pause: 1.5 }).pause).toBeDefined();
		expect(settingsProblem({ ...ok, after: 100 }).after).toBeDefined();
		expect(settingsProblem({ ...ok, pause: 72, after: 1 })).toEqual({});
	});
	it("switching the follow-up on needs a template name and a language", () => {
		const on = { ...ok, followEnabled: true };
		expect(Object.keys(settingsProblem(on)).sort()).toEqual(["language", "template"]);
		expect(settingsProblem({ ...on, template: "Bad Name", language: "en" }).template).toBeDefined();
		expect(settingsProblem({ ...on, template: "callback_followup", language: "en_US" })).toEqual({});
		expect(settingsProblem({ ...on, template: "callback_followup", language: "english" }).language).toBeDefined();
	});
	it("defaults are filled in for a settings object the engine left partly out", () => {
		expect(settingsOf({ available: true } as never)).toEqual({
			human_pause_hours: 12,
			follow_up: { enabled: false, template_name: null, language: null, after_hours: 24 },
			shareable_document_ids: [],
		});
	});
});

describe("connecting by hand", () => {
	it("ids are digits, the token is pasted", () => {
		expect(manualProblems({ phone: "1234567890", waba: "5550001", token: "EAAG-token-123" })).toEqual({});
		expect(Object.keys(manualProblems({ phone: "../me", waba: "12", token: "x" })).sort()).toEqual([
			"phone",
			"token",
			"waba",
		]);
		expect(manualProblems({ phone: " 1234567890 ", waba: "5550001", token: "EAAG-token-123" })).toEqual({});
	});

	it("the PIN is optional, and when given it is six digits", () => {
		const base = { phone: "1234567890", waba: "5550001", token: "EAAG-token-123" };
		expect(manualProblems({ ...base, pin: "" })).toEqual({});
		expect(manualProblems({ ...base, pin: " 123456 " })).toEqual({});
		for (const pin of ["12345", "1234567", "abcdef"]) {
			expect(Object.keys(manualProblems({ ...base, pin }))).toEqual(["pin"]);
		}
	});
});

describe("files the assistant can send", () => {
	it("only PDF, JPG and PNG, by type or by name", () => {
		expect(isSendable({ file_name: "price-list.pdf", mime_type: "application/pdf" })).toBe(true);
		expect(isSendable({ file_name: "menu.JPEG", mime_type: null })).toBe(true);
		expect(isSendable({ file_name: null, mime_type: "image/png" })).toBe(true);
		expect(isSendable({ file_name: "terms.docx", mime_type: "application/msword" })).toBe(false);
		expect(isSendable({ file_name: null, mime_type: null })).toBe(false);
		expect(MAX_SHAREABLE).toBe(20);
	});
});

describe("chat rows", () => {
	it("says unread, quiet, STOP and closed window in words", () => {
		const labels = chatBadges({
			unread_count: 2,
			bot_paused_until: "2026-10-06T00:00:00Z",
			opted_out: true,
			window_open: false,
		}).map((b) => b.label);
		expect(labels).toEqual([
			"2 unread",
			"You're handling this",
			"Asked not to be messaged",
			"More than 24 hours since they wrote",
		]);
		expect(chatBadges({ unread_count: 0, bot_paused_until: null, opted_out: false, window_open: true })).toEqual([]);
	});
});

describe("the deployer's fee rates", () => {
	it("a rate is whole minor units from 0 to 1,000,000; empty is zero", () => {
		expect(parseRate("")).toBe(0);
		expect(parseRate("35")).toBe(35);
		expect(parseRate("1000000")).toBe(1_000_000);
		expect(parseRate("1000001")).toBeNull();
		expect(parseRate("0.35")).toBeNull();
		expect(parseRate("-1")).toBeNull();
		expect(parseRate("abc")).toBeNull();
	});
	it("builds the patch the engine takes, or nothing when a box is wrong", () => {
		const row = { service: "12", utility: "15", marketing: "", authentication: "0" };
		const values = { INR: row, USD: { service: "0", utility: "0", marketing: "0", authentication: "0" } };
		expect(ratesPatch(values, "1000")).toEqual({
			rates_minor: {
				INR: { service: 12, utility: 15, marketing: 0, authentication: 0 },
				USD: { service: 0, utility: 0, marketing: 0, authentication: 0 },
			},
			free_service_per_month: 1000,
		});
		expect(ratesPatch({ ...values, INR: { ...row, service: "1.5" } }, "0")).toBeNull();
		expect(ratesPatch(values, "x")).toBeNull();
	});
});
