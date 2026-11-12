import { describe, expect, it } from "vitest";
import { EMAIL_MESSAGES, emailSchema, SPECIAL_USE_TLDS, specialUseTld } from "./email-rule";

const message = (allow: boolean, value: string): string | null => {
	const r = emailSchema(allow).safeParse(value);
	return r.success ? null : (r.error.issues[0]?.message ?? "?");
};

describe("emailSchema", () => {
	it.each([
		"priya@shree-labs.in",
		"  Priya.S@Shree-Labs.IN ",
		"priya+bills@gmail.com",
		"owner@example.com",
		"ops@sub.co.in",
	])("accepts a real address in both modes: %s", (value) => {
		expect(message(true, value)).toBeNull();
		expect(message(false, value)).toBeNull();
	});

	it.each(SPECIAL_USE_TLDS)("accepts .%s in development and refuses it in production", (tld) => {
		const address = `admin@shop.${tld}`;
		expect(message(true, address)).toBeNull();
		expect(message(false, address)).toBe(EMAIL_MESSAGES.special(tld));
		expect(message(false, address)).toContain("cannot receive email");
	});

	it("is case-insensitive about the reserved name", () => {
		expect(message(false, "Admin@Shop.LOCAL")).toBe(EMAIL_MESSAGES.special("local"));
		expect(message(true, "Admin@Shop.LOCAL")).toBeNull();
	});

	it.each(["", "   ", "priya", "priya@", "@shree.in", "priya@shree", "pri ya@shree.in", "a@@shree.in"])(
		"refuses a malformed address in both modes: %j",
		(value) => {
			expect(message(true, value)).not.toBeNull();
			expect(message(false, value)).not.toBeNull();
		},
	);

	it("asks for an address when empty", () => {
		expect(message(true, "")).toBe(EMAIL_MESSAGES.required);
	});

	it("treats a reserved name before the last label as a real domain", () => {
		expect(message(false, "a@test.shree.in")).toBeNull();
	});
});

describe("specialUseTld", () => {
	it("finds the reserved name or null", () => {
		expect(specialUseTld("a@b.test")).toBe("test");
		expect(specialUseTld("a@b.in")).toBeNull();
		expect(specialUseTld("a@localhost")).toBeNull();
	});
});
