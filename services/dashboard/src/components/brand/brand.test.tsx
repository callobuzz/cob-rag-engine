import { act, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BASE, BRANDING } from "@/test/msw/data";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";

/** The white-label logo rule on the public pages (see e2e/auth-brand.spec.ts for the real browser). */

function brandWith(logo_url: string | null) {
	server.use(http.get(`${BASE}/api/v1/public/branding`, () => HttpResponse.json({ ...BRANDING, logo_url })));
}

afterEach(() => {
	vi.unstubAllGlobals();
	vi.useRealTimers();
});

describe("default brand (no logo_url)", () => {
	it("shows the cob-rag-engine marks and the animated logo, not the old letter badge", async () => {
		renderApp("/login");
		await screen.findByRole("heading", { name: "Log in to Sahayak AI" });
		// Small mark beside the name (brand panel) and a static one above the form.
		expect(screen.getAllByTestId("cob-logo-mark")).toHaveLength(2);
		expect(screen.getByTestId("cob-logo-animated")).toHaveAttribute("aria-hidden", "true");
		expect(screen.queryByTestId("operator-logo")).toBeNull();
		expect(screen.queryByTestId("brand-backdrop")).toBeNull();
		expect(screen.getByRole("img", { name: "Sahayak AI" })).toBeInTheDocument(); // standalone mark is named
		expect(screen.getByTestId("powered-by")).toHaveTextContent("cob-rag-engine by Callobuzz");
	});

	it("runs armed, assemble, live once on screen, and pauses when hidden", async () => {
		let notify: (visible: boolean) => void = () => {};
		class FakeIO {
			constructor(cb: IntersectionObserverCallback) {
				notify = (isIntersecting) => cb([{ isIntersecting } as IntersectionObserverEntry], this as never);
			}
			observe() {}
			disconnect() {}
			unobserve() {}
		}
		vi.stubGlobal("IntersectionObserver", FakeIO);
		renderApp("/login");
		await screen.findByRole("heading", { name: "Log in to Sahayak AI" });
		const logo = screen.getByTestId("cob-logo-animated");
		expect(logo).toHaveAttribute("data-state", "armed");

		vi.useFakeTimers();
		act(() => notify(true));
		expect(logo).toHaveAttribute("data-state", "assemble");
		act(() => notify(false));
		expect(logo).toHaveClass("is-paused");
		act(() => notify(true));
		expect(logo).not.toHaveClass("is-paused");
		act(() => {
			vi.advanceTimersByTime(3200);
		});
		expect(logo).toHaveAttribute("data-state", "live");
	});

	it("stays the static logo under prefers-reduced-motion", async () => {
		vi.stubGlobal(
			"IntersectionObserver",
			class {
				observe() {}
				disconnect() {}
			},
		);
		vi.stubGlobal("matchMedia", (query: string) => ({
			matches: query.includes("prefers-reduced-motion"),
			media: query,
			addEventListener() {},
			removeEventListener() {},
		}));
		renderApp("/login");
		await screen.findByRole("heading", { name: "Log in to Sahayak AI" });
		expect(screen.getByTestId("cob-logo-animated")).not.toHaveAttribute("data-state");
	});
});

describe("operator logo (logo_url set)", () => {
	it("shows the operator's image in both places and no cob-rag-engine logo or animation", async () => {
		brandWith("https://sahayak.ai/logo.png");
		renderApp("/login");
		await screen.findByRole("heading", { name: "Log in to Sahayak AI" });
		const logos = screen.getAllByTestId("operator-logo");
		expect(logos).toHaveLength(2);
		for (const img of logos) expect(img).toHaveAttribute("src", "https://sahayak.ai/logo.png");
		// Beside the product name the image is decorative; above the form its alt is the product name.
		expect(screen.getAllByRole("img", { name: "Sahayak AI" })).toHaveLength(1);
		expect(screen.queryByTestId("cob-logo-mark")).toBeNull();
		expect(screen.queryByTestId("cob-logo-animated")).toBeNull();
		expect(screen.getByTestId("brand-backdrop")).toBeInTheDocument();
		expect(screen.getByTestId("powered-by")).toHaveTextContent("cob-rag-engine by Callobuzz");
	});

	it("ignores an unsafe logo_url and falls back to the default brand", async () => {
		brandWith("javascript:alert(1)");
		renderApp("/login");
		await screen.findByRole("heading", { name: "Log in to Sahayak AI" });
		expect(screen.queryByTestId("operator-logo")).toBeNull();
		expect(screen.getByTestId("cob-logo-animated")).toBeInTheDocument();
	});
});
