import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, rt, SV, tokenResponse } from "@/test/msw/data";
import { pipelinesState } from "@/test/msw/handlers/pipelines";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";

function openAdmin(path = "/admin/pipelines") {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user: SV, tenant: null, tenants: [] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () =>
			HttpResponse.json({ user: SV, tenant: null, tenants: [], session_id: "s" }),
		),
	);
	return renderApp(path);
}

const card = async (name: string) => within(await screen.findByRole("listitem", { name }));

describe("Pipeline profiles page", () => {
	it("lists Free, Standard (Default) and Premium with what each answers with and who uses it", async () => {
		openAdmin();
		const standard = await card("Standard");
		expect(standard.getByText("Default")).toBeInTheDocument();
		expect(standard.getByText("3 plans")).toBeInTheDocument();
		expect(standard.getByText("the engine's model (gpt-oss-120b)")).toBeInTheDocument();
		const free = await card("Free");
		expect(free.getByText("Owners see it as “Economy”")).toBeInTheDocument();
		expect(free.getByText("the free model on this server")).toBeInTheDocument();
		expect(free.getByText("Low")).toBeInTheDocument();
		const premium = await card("Premium");
		expect(premium.getByText("AI ranking")).toBeInTheDocument();
		expect(premium.getByText("1 plan and 1 business")).toBeInTheDocument();
	});

	it("says so when a local profile cannot answer because the local model is not ready", async () => {
		const free = pipelinesState.profiles.find((p) => p.id === "pf-free");
		if (free) free.available = false;
		openAdmin();
		const c = await card("Free");
		expect(c.getByText("Not available: the local model is not ready")).toBeInTheDocument();
	});

	it("uses the product's words: no corpus, tenant, lane, slot or LiteLLM on the page", async () => {
		const { container } = openAdmin();
		await card("Standard");
		expect(container.textContent ?? "").not.toMatch(/corpus|tenant|\blane\b|\bslot\b|litellm/i);
	});

	it("make default moves the Default badge", async () => {
		const { user } = openAdmin();
		const premium = await card("Premium");
		await user.click(premium.getByRole("button", { name: "Make default Premium" }));
		await waitFor(async () => expect((await card("Premium")).getByText("Default")).toBeInTheDocument());
		expect((await card("Standard")).queryByText("Default")).not.toBeInTheDocument();
	});

	it("disables deleting the default profile and says why", async () => {
		openAdmin();
		const c = await card("Standard");
		const del = c.getByRole("button", { name: "Delete Standard" });
		expect(del).toBeDisabled();
		expect(c.getByText(/Make another profile the default first/)).toBeInTheDocument();
	});

	it("tells in words who uses a profile when it can't be deleted", async () => {
		const { user } = openAdmin();
		const premium = await card("Premium");
		await user.click(premium.getByRole("button", { name: "Delete Premium" }));
		const dialog = await screen.findByRole("alertdialog");
		await user.click(within(dialog).getByRole("button", { name: "Delete profile" }));
		expect(await within(dialog).findByText(/Used by 1 plan and 1 business\./)).toBeInTheDocument();
		expect(within(dialog).getByText(/Move them to another profile first/)).toBeInTheDocument();
		expect(pipelinesState.profiles.some((p) => p.id === "pf-premium")).toBe(true);
	});

	it("deletes a profile nobody uses", async () => {
		const free = pipelinesState.profiles.find((p) => p.id === "pf-free");
		if (free) free.plan_count = 0;
		const { user } = openAdmin();
		const c = await card("Free");
		await user.click(c.getByRole("button", { name: "Delete Free" }));
		const dialog = await screen.findByRole("alertdialog");
		await user.click(within(dialog).getByRole("button", { name: "Delete profile" }));
		await waitFor(() => expect(screen.queryByRole("listitem", { name: "Free" })).not.toBeInTheDocument());
	});

	it("clones a profile under a new name", async () => {
		const { user } = openAdmin();
		const premium = await card("Premium");
		await user.click(premium.getByRole("button", { name: "Clone Premium" }));
		const dialog = await screen.findByRole("dialog");
		await user.type(within(dialog).getByLabelText("Name of the copy"), "Festive rush");
		await user.click(within(dialog).getByRole("button", { name: "Clone" }));
		expect(await screen.findByRole("listitem", { name: "Festive rush" })).toBeInTheDocument();
		expect(pipelinesState.sent.at(-1)).toMatchObject({ path: "pf-premium/clone", body: { name: "Festive rush" } });
	});
});
