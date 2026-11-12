import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, rt, SHREE, tokenResponse } from "@/test/msw/data";
import { createEvalsMock, RUN_PUBLISH } from "@/test/msw/handlers/evals";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";

const KB = CORPORA[0]?.id ?? "";

function open(publishRun: boolean) {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user: PRIYA, tenant: SHREE, tenants: [SHREE] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () =>
			HttpResponse.json({ user: PRIYA, tenant: SHREE, tenants: [SHREE], session_id: "s" }),
		),
		...createEvalsMock({ publishRun }).handlers,
	);
	return renderApp(`/kbs/${KB}?tab=assistant&section=tests`);
}

describe("assistant: tests summary", () => {
	it("reports the check that ran when the assistant was published, and links to it", async () => {
		open(true);
		expect(await screen.findByText("After publishing v7: 11 of 12 behaviour checks passed")).toBeInTheDocument();
		const link = screen.getByRole("link", { name: "Open in Quality" });
		expect(link.getAttribute("href")).toContain(`/quality?`);
		expect(link.getAttribute("href")).toContain(`run=${RUN_PUBLISH}`);
		expect(link.getAttribute("href")).toContain("tab=runs");
	});

	it("says what to do when nothing ran on publish yet", async () => {
		open(false);
		expect(await screen.findByText(/checked every time you publish/)).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Open in Quality" }).getAttribute("href")).toContain("tab=golden");
	});
});
