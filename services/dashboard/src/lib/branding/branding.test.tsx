import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { BASE, BRANDING } from "@/test/msw/data";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";
import { DEFAULT_BRAND_COLOR, POWERED_BY, resolveBranding, safeHttpUrl } from "./BrandingProvider";
import { documentTitle } from "./useDocumentTitle";

describe("resolveBranding", () => {
	it("falls back to built-in defaults when the call failed", () => {
		const b = resolveBranding(undefined, true);
		expect(b.productName).toBe("cob-rag-engine");
		expect(b.primaryColor).toBe(DEFAULT_BRAND_COLOR);
		expect(b.poweredBy).toEqual(POWERED_BY);
		expect(b.failed).toBe(true);
		expect(b.accountsEnabled).toBe(false);
		expect(b.passwordPolicy).toEqual({ min_length: 10, max_length: 128 });
	});

	it("keeps the constant powered-by line when the server's is unusable", () => {
		const b = resolveBranding({ ...BRANDING, powered_by: { text: "", url: null } }, false);
		expect(b.poweredBy).toEqual(POWERED_BY);
	});

	it("null from the engine means the operator hid the line; a failed call keeps it", () => {
		expect(resolveBranding({ ...BRANDING, powered_by: null }, false).poweredBy).toBeNull();
		expect(resolveBranding(undefined, true).poweredBy).toEqual(POWERED_BY);
	});

	it("takes the powered-by text from the engine and accepts a reply without a url", () => {
		const b = resolveBranding({ ...BRANDING, powered_by: { text: "cob-rag-engine by Callobuzz", url: null } }, false);
		expect(b.poweredBy).toEqual({ text: "cob-rag-engine by Callobuzz" });
	});

	it("rejects a non-http(s) logo or contact URL", () => {
		expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
		expect(safeHttpUrl("data:image/png;base64,AAAA")).toBeNull();
		expect(safeHttpUrl("ftp://example.in/logo.png")).toBeNull();
		expect(safeHttpUrl("http://sahayak.ai/logo.png")).toBe("http://sahayak.ai/logo.png");
		expect(safeHttpUrl("https://sahayak.ai/logo.png")).toBe("https://sahayak.ai/logo.png");
		const b = resolveBranding({ ...BRANDING, logo_url: "javascript:x", contact_url: "vbscript:x" }, false);
		expect(b.logoUrl).toBeNull();
		expect(b.contactUrl).toBeNull();
	});

	it("ignores an invalid colour", () => {
		expect(resolveBranding({ ...BRANDING, primary_color: "red" }, false).primaryColor).toBe(DEFAULT_BRAND_COLOR);
	});
});

describe("document title", () => {
	it("is '{page} · {product}'", () => {
		expect(documentTitle("Usage", "Sahayak AI")).toBe("Usage · Sahayak AI");
		expect(documentTitle(null, "Sahayak AI")).toBe("Sahayak AI");
	});
});

describe("branding on public pages", () => {
	it("shows the operator's name, sets the brand colour and title", async () => {
		renderApp("/login");
		expect(await screen.findByRole("heading", { name: "Log in to Sahayak AI" })).toBeInTheDocument();
		expect(document.documentElement.style.getPropertyValue("--brand")).toBe("#0b7a5f");
		expect(document.documentElement.style.getPropertyValue("--brand-foreground")).toBe("#ffffff");
		expect(document.title).toBe("Log in to Sahayak AI · Sahayak AI");
		expect(screen.getByTestId("powered-by")).toHaveTextContent("cob-rag-engine by Callobuzz");
	});

	it("shows defaults, a non-blocking banner and still the powered-by line when branding fails", async () => {
		server.use(http.get(`${BASE}/api/v1/public/branding`, () => HttpResponse.error()));
		renderApp("/login/key");
		expect(
			await screen.findByRole("heading", { name: "Sign in with an API key" }, { timeout: 5000 }),
		).toBeInTheDocument();
		expect(screen.getByText(/Couldn't load this engine's branding/)).toBeInTheDocument();
		expect(screen.getAllByText("cob-rag-engine").length).toBeGreaterThan(0);
		expect(screen.getByTestId("powered-by")).toHaveTextContent(POWERED_BY.text);
		expect(screen.queryByRole("link", { name: /cob-rag-engine by Callobuzz/ })).toBeNull();
	});
});
