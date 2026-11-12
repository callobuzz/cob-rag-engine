import { describe, expect, it } from "vitest";
import { noticeIsRecommended, RECOMMENDED_NOTICE } from "./consentNotice";

describe("recommended consent wording", () => {
	it("says a summary and a reference code are shared", () => {
		expect(RECOMMENDED_NOTICE.en).toContain("reference code");
		expect(RECOMMENDED_NOTICE["hi-Latn"]).toContain("reference code");
	});

	it("is recognised when every language matches", () => {
		expect(noticeIsRecommended({ ...RECOMMENDED_NOTICE })).toBe(true);
		expect(noticeIsRecommended({ en: RECOMMENDED_NOTICE.en })).toBe(true);
	});

	it("offers the replacement when the owner's text differs, including the first default", () => {
		expect(noticeIsRecommended({ en: "I agree to be called." })).toBe(false);
		expect(noticeIsRecommended({ ...RECOMMENDED_NOTICE, hi: "कुछ और" })).toBe(false);
		const v1 = RECOMMENDED_NOTICE.en.replace(/ To save you repeating yourself[^.]*\./, "");
		expect(noticeIsRecommended({ en: v1 })).toBe(false);
	});
});
