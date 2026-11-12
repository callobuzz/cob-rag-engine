import { screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BASE, PRIYA, SHREE } from "../msw/data";
import { INFO } from "../msw/handlers/identity";
import { createLeadsMock } from "../msw/handlers/leads";
import { server } from "../msw/server";
import { renderApp } from "../render";
import { MEMBER_SHREE, signedInAs } from "./assistant-utils";

/** TV5: every business can download its leads, also on a plan that shows the plain menu only. */

const SIMPLE_PLAN = {
	advanced: false,
	models: false,
	retrieval: false,
	keys_agents: false,
	webhooks: false,
	quality: false,
	api_docs: false,
	usage_details: false,
	widget_code_only: true,
};

function simplePlan() {
	server.use(
		http.get(`${BASE}/api/v1/info`, () =>
			HttpResponse.json({ ...INFO, features: { ...INFO.features, dashboard: SIMPLE_PLAN } }),
		),
	);
}

let exported: string[] = [];

beforeEach(() => {
	exported = [];
	URL.createObjectURL = () => "blob:csv";
	URL.revokeObjectURL = () => {};
	vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
	server.use(
		http.get(`${BASE}/api/v1/leads/export.csv`, ({ request }) => {
			exported.push(new URL(request.url).search);
			return HttpResponse.text("﻿created_at,reference,knowledge_base,name,phone\n", {
				headers: { "content-type": "text/csv; charset=utf-8" },
			});
		}),
		...createLeadsMock().handlers,
	);
});
afterEach(() => vi.restoreAllMocks());

describe("Leads: Export CSV on a plain-menu plan", () => {
	it("an owner sees Export CSV, enabled, and downloads the file", async () => {
		simplePlan();
		signedInAs(PRIYA, SHREE);
		const app = renderApp("/leads");
		const button = await screen.findByRole("button", { name: "Export CSV" });
		expect(button).not.toHaveAttribute("aria-disabled", "true");
		await app.user.click(button);
		await waitFor(() => expect(exported).toHaveLength(1));
	});

	it("a team member sees the button but is told only owners can export", async () => {
		simplePlan();
		signedInAs(PRIYA, MEMBER_SHREE);
		const app = renderApp("/leads");
		const button = await screen.findByRole("button", { name: "Export CSV" });
		expect(button).toHaveAttribute("aria-disabled", "true");
		await app.user.click(button);
		expect(await screen.findByText("Only owners can export leads")).toBeInTheDocument();
		expect(exported).toHaveLength(0);
	});
});
