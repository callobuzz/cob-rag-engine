import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { localResetTime } from "@/lib/branding/demo";
import { BASE, BRANDING } from "@/test/msw/data";
import { server } from "@/test/msw/server";
import { DemoBanner } from "./DemoBanner";

function wrap(ui: React.ReactNode) {
	const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
	return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const DEMO = {
	logins: [{ role: "owner", email: "owner@nirmalclinic.example.com", password: "Nirmal-Clinic-Demo-2026" }],
	resets_at_utc: "21:30",
};

describe("DemoBanner", () => {
	it("is hidden on a normal install (no demo in the branding)", async () => {
		wrap(<DemoBanner />);
		await new Promise((r) => setTimeout(r, 50));
		expect(screen.queryByTestId("demo-banner")).toBeNull();
	});

	it("is hidden when demo is null", async () => {
		server.use(http.get(`${BASE}/api/v1/public/branding`, () => HttpResponse.json({ ...BRANDING, demo: null })));
		wrap(<DemoBanner />);
		await new Promise((r) => setTimeout(r, 50));
		expect(screen.queryByTestId("demo-banner")).toBeNull();
	});

	it("says why actions are refused and when the demo resets", async () => {
		server.use(http.get(`${BASE}/api/v1/public/branding`, () => HttpResponse.json({ ...BRANDING, demo: DEMO })));
		wrap(<DemoBanner />);
		const bar = await screen.findByTestId("demo-banner");
		await waitFor(() => expect(bar).toHaveTextContent("This is a live demo. Some actions are switched off"));
		expect(bar).toHaveTextContent("everything is reset every night at");
	});
});

describe("localResetTime", () => {
	it("shows 21:30 UTC as 3:00 am in India", () => {
		expect(localResetTime("21:30", "Asia/Kolkata")).toMatch(/^0?3:00\s?am IST$/i);
	});
	it("follows the visitor's zone", () => {
		expect(localResetTime("21:30", "UTC")).toMatch(/^0?9:30\s?pm UTC$/i);
	});
	it("returns an unreadable value as it is", () => {
		expect(localResetTime("soon")).toBe("soon");
	});
});

describe("DemoBanner: the answers left today", () => {
	const reply = (answers: unknown) =>
		server.use(
			http.get(`${BASE}/api/v1/public/branding`, () => HttpResponse.json({ ...BRANDING, demo: { ...DEMO, answers } })),
		);

	it("says how many demo answers are left", async () => {
		reply({ limit: 300, left: 212, per_visitor: 15, resets_at: "2026-10-07T21:30:00Z" });
		wrap(<DemoBanner />);
		const left = await screen.findByTestId("demo-answers");
		expect(left).toHaveTextContent("212 of 300 demo answers left today.");
		expect(left).toHaveAttribute("data-used-up", "false");
	});

	it("says the answers are used up, and when the demo resets", async () => {
		reply({ limit: 300, left: 0, per_visitor: 15, resets_at: "2026-10-07T21:30:00Z" });
		wrap(<DemoBanner />);
		const used = await screen.findByTestId("demo-answers");
		expect(used).toHaveTextContent("Today's demo answers are used up. They start again at");
		expect(used).toHaveAttribute("data-used-up", "true");
	});

	it("says nothing about answers when the engine could not read the counter", async () => {
		reply(null);
		wrap(<DemoBanner />);
		await screen.findByTestId("demo-banner");
		expect(screen.queryByTestId("demo-answers")).toBeNull();
	});
});
