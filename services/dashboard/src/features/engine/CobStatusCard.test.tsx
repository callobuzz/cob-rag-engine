import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, rt, SV, tokenResponse } from "@/test/msw/data";
import { type COB_STATUS_OK, createEngineMock } from "@/test/msw/handlers/engine";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";

function open(override: Partial<typeof COB_STATUS_OK> = {}) {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user: SV, tenant: null, tenants: [] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () =>
			HttpResponse.json({ user: SV, tenant: null, tenants: [], session_id: "s" }),
		),
	);
	const mock = createEngineMock();
	mock.state.cob = { ...mock.state.cob, ...override };
	server.use(...mock.handlers);
	return { app: renderApp("/admin/engine"), mock };
}

const card = () => screen.findByRole("region", { name: "COB docs" });

describe("Engine info: COB docs card", () => {
	it("shows the version, the documents and when it last synced when it is up to date", async () => {
		open();
		const c = await card();
		expect(await within(c).findByText("Up to date")).toBeInTheDocument();
		expect(within(c).getByText("0.1.0+6186e350818a")).toBeInTheDocument();
		expect(within(c).getByText("1,160")).toBeInTheDocument();
		expect(within(c).getByText("Idle")).toBeInTheDocument();
		expect(within(c).getByText("Not run yet")).toBeInTheDocument();
		expect(within(c).getByRole("button", { name: "Resync" })).toBeEnabled();
	});

	it("says an update is pending when the shipped docs are newer", async () => {
		open({ up_to_date: false, docs_version: "0.1.0+old", current_version: "0.1.0+new" });
		const c = await card();
		expect(await within(c).findByText("Update pending")).toBeInTheDocument();
	});

	it("shows the evaluation result when it passed", async () => {
		open({
			eval: {
				passed: true,
				hit_at_5: 0.93,
				hit_at_5_indic: 0.9,
				role_leaks: 0,
				negatives_failed: 0,
				items: 242,
			},
		});
		const c = await card();
		expect(await within(c).findByText("93% of test questions found their help page · passed")).toBeInTheDocument();
		expect(within(c).queryByText("Below the bar")).not.toBeInTheDocument();
	});

	it("warns with the failing counts and links to the review list when the evaluation is below the bar", async () => {
		open({ eval: { passed: false, hit_at_5: 0.7, hit_at_5_indic: 0.9, role_leaks: 1, negatives_failed: 2 } });
		const c = await card();
		expect((await within(c).findAllByText("Below the bar")).length).toBeGreaterThan(0);
		expect(within(c).getByText(/1 answer could reach pages above the person's role/)).toBeInTheDocument();
		expect(within(c).getByText(/2 administrator-only questions not held back/)).toBeInTheDocument();
		expect(within(c).getByText(/help pages found for 70% of questions/)).toBeInTheDocument();
		expect(within(c).getByRole("link", { name: "Review weak answers" })).toHaveAttribute("href", "#cob-review");
	});

	it("says so when the evaluation itself could not run", async () => {
		open({ eval: { passed: false, error: "The vector store went away." } });
		const c = await card();
		expect((await within(c).findAllByText("The evaluation could not run")).length).toBeGreaterThan(0);
	});

	it("shows a spinner and disables Resync while the docs are being read", async () => {
		open({ sync_state: "running" });
		const c = await card();
		expect(await within(c).findByRole("status")).toHaveTextContent("COB is reading the docs.");
		expect(within(c).getByRole("button", { name: "Resync" })).toBeDisabled();
	});

	it("shows the error of a failed sync", async () => {
		open({ sync_state: "failed", last_error: "The embedding service is down.", up_to_date: false });
		const c = await card();
		expect(await within(c).findByText("The embedding service is down.")).toBeInTheDocument();
		expect(within(c).getAllByText("Failed").length).toBeGreaterThan(0);
	});

	it("asks to confirm, then queues the resync once", async () => {
		const { app, mock } = open();
		const c = await card();
		await app.user.click(await within(c).findByRole("button", { name: "Resync" }));
		const dialog = await screen.findByRole("alertdialog");
		expect(dialog).toHaveTextContent("Re-read the documentation?");
		await app.user.click(within(dialog).getByRole("button", { name: "Resync" }));
		expect(await screen.findByText("Resync queued")).toBeInTheDocument();
		expect(mock.state.resyncs).toEqual([{ eval: true }]);
		expect(await within(c).findByRole("status")).toBeInTheDocument();
	});

	it("shows the failure when the queue cannot be reached", async () => {
		const { app, mock } = open();
		server.use(
			http.post(`${BASE}/api/v1/admin/cob/resync`, () => {
				// The engine records the failure before it answers 503.
				mock.state.cob = {
					...mock.state.cob,
					sync_state: "failed",
					last_error: "The background worker could not be reached.",
					up_to_date: false,
				};
				return HttpResponse.json(
					{ detail: "The background worker could not be reached.", code: "queue_unavailable" },
					{ status: 503 },
				);
			}),
		);
		const c = await card();
		await app.user.click(await within(c).findByRole("button", { name: "Resync" }));
		const dialog = await screen.findByRole("alertdialog");
		await app.user.click(within(dialog).getByRole("button", { name: "Resync" }));
		expect(await within(c).findByText("The background worker could not be reached.")).toBeInTheDocument();
		expect(within(c).getAllByText("Failed").length).toBeGreaterThan(0);
	});

	it("offers a retry when the status cannot be loaded", async () => {
		sessionStorage.setItem(RT_KEY, rt(1));
		server.use(
			http.post(`${BASE}/api/v1/auth/refresh`, () =>
				HttpResponse.json(tokenResponse({ user: SV, tenant: null, tenants: [] }, 2)),
			),
			http.get(`${BASE}/api/v1/auth/me`, () =>
				HttpResponse.json({ user: SV, tenant: null, tenants: [], session_id: "s" }),
			),
			http.get(`${BASE}/api/v1/admin/cob/status`, () => HttpResponse.json({ detail: "x" }, { status: 500 })),
			...createEngineMock().handlers,
		);
		renderApp("/admin/engine");
		const c = await card();
		expect(await within(c).findByText("Couldn't load the COB docs status.")).toBeInTheDocument();
		expect(within(c).getByRole("button", { name: "Try again" })).toBeInTheDocument();
	});
});
