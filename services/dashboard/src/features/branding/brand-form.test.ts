import { describe, expect, it } from "vitest";
import { type BrandValues, brandFieldOf, brandPatch, isHttpUrl, validateBrand, whiteContrast } from "./brand-form";

const base: BrandValues = {
	product_name: "Sahayak AI",
	logo_url: "https://sahayak.ai/mark.svg",
	primary_color: "#0b7a5f",
	support_email: "support@sahayak.ai",
	contact_url: "https://wa.me/919822012345",
	contact_text: "Message us and we'll set you up the same day.",
};

describe("brand form", () => {
	it("sends only changed fields; a cleared optional field is null", () => {
		expect(brandPatch(base, base)).toEqual({});
		expect(brandPatch(base, { ...base, product_name: " Sahayak Care " })).toEqual({ product_name: "Sahayak Care" });
		expect(brandPatch(base, { ...base, logo_url: "", support_email: "" })).toEqual({
			logo_url: null,
			support_email: null,
		});
		expect(brandPatch(base, { ...base, primary_color: "#0B7A60" })).toEqual({ primary_color: "#0b7a60" });
	});

	it("accepts only http(s) URLs (never javascript: or data:)", () => {
		expect(isHttpUrl("https://wa.me/919822012345")).toBe(true);
		expect(isHttpUrl("http://127.0.0.1:8090/logo.png")).toBe(true);
		expect(isHttpUrl("javascript:alert(1)")).toBe(false);
		expect(isHttpUrl("data:image/svg+xml;base64,AAAA")).toBe(false);
		expect(isHttpUrl("wa.me/91")).toBe(false);
	});

	it("validates every field with plain messages", () => {
		expect(validateBrand(base)).toEqual({});
		const e = validateBrand({
			product_name: "",
			logo_url: "javascript:alert(1)",
			primary_color: "green",
			support_email: "support",
			contact_url: `https://x.in/${"a".repeat(2100)}`,
			contact_text: "",
		});
		expect(Object.keys(e).sort()).toEqual(
			["contact_text", "contact_url", "logo_url", "primary_color", "product_name", "support_email"].sort(),
		);
		expect(e.contact_url).toBe("Use at most 2,048 characters.");
		expect(validateBrand({ ...base, product_name: "x".repeat(81) }).product_name).toBe("Use 1 to 80 characters.");
	});

	it("white-text contrast and server 422 paths", () => {
		expect(whiteContrast("#0b7a5f")).toBeCloseTo(5.3, 1);
		expect(whiteContrast("#ffe066")).toBeLessThan(3);
		expect(whiteContrast("nope")).toBeNull();
		expect(brandFieldOf("branding.logo_url")).toBe("logo_url");
		expect(brandFieldOf("signup_mode")).toBeNull();
	});
});
