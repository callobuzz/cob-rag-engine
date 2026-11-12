import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { POWERED_BY } from "@/lib/branding/BrandingProvider";
import { BASE, BRANDING } from "@/test/msw/data";
import { server } from "@/test/msw/server";
import { PoweredBy } from "./PoweredBy";

function wrap(ui: React.ReactNode) {
	const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
	return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("PoweredBy", () => {
	it("renders the constant when branding failed", async () => {
		server.use(http.get(`${BASE}/api/v1/public/branding`, () => HttpResponse.error()));
		wrap(<PoweredBy />);
		const line = await screen.findByTestId("powered-by");
		expect(line).toHaveTextContent(POWERED_BY.text);
		expect(screen.queryByRole("link")).toBeNull();
	});

	it("renders nothing when the operator hid the line in Branding (branding powered_by is null)", async () => {
		server.use(http.get(`${BASE}/api/v1/public/branding`, () => HttpResponse.json({ ...BRANDING, powered_by: null })));
		wrap(<PoweredBy />);
		await waitFor(() => expect(screen.queryByTestId("powered-by")).toBeNull());
		await new Promise((r) => setTimeout(r, 50));
		expect(screen.queryByTestId("powered-by")).toBeNull();
	});

	it("has no prop that can hide it (type test)", () => {
		// @ts-expect-error `hidden` is not a PoweredBy prop
		const a = <PoweredBy hidden />;
		// @ts-expect-error `className` is not a PoweredBy prop
		const b = <PoweredBy className="hidden" />;
		// @ts-expect-error `style` is not a PoweredBy prop
		const c = <PoweredBy style={{ display: "none" }} />;
		expect([a, b, c]).toHaveLength(3);
	});
});
