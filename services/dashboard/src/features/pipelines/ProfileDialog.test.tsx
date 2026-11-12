import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, rt, SV, tokenResponse } from "@/test/msw/data";
import { pipelinesState } from "@/test/msw/handlers/pipelines";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";

function openAdmin() {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user: SV, tenant: null, tenants: [] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () =>
			HttpResponse.json({ user: SV, tenant: null, tenants: [], session_id: "s" }),
		),
	);
	return renderApp("/admin/pipelines");
}

async function openNew(user: ReturnType<typeof openAdmin>["user"]) {
	await user.click(await screen.findByRole("button", { name: "New profile" }));
	return within(await screen.findByRole("dialog"));
}

describe("Pipeline profile dialog", () => {
	it("explains empty fields by what they follow right now", async () => {
		const { user } = openAdmin();
		const d = await openNew(user);
		expect(d.getAllByText(/Follow the engine default \(now: gpt-oss-120b, low thinking\)/).length).toBeGreaterThan(0);
		expect(d.getByText(/Follow the engine default \(now: Hindi: gemma-4-31b-it\)/)).toBeInTheDocument();
	});

	it("checks name and label before it calls the engine", async () => {
		const { user } = openAdmin();
		const d = await openNew(user);
		await user.click(d.getByRole("button", { name: "Create profile" }));
		expect(await d.findByText("Give the profile a name.")).toBeInTheDocument();
		expect(d.getByText("Give owners a short word for it.")).toBeInTheDocument();
		expect(pipelinesState.sent).toEqual([]);
	});

	it("choosing the server's CPU ticks and locks 'use these settings instead'", async () => {
		const { user } = openAdmin();
		const d = await openNew(user);
		const box = d.getByRole("checkbox", { name: /Use these settings instead of each knowledge base's own/ });
		expect(box).not.toBeChecked();
		await user.selectOptions(d.getByLabelText("Where answers are written"), "local");
		expect(box).toBeChecked();
		expect(box).toBeDisabled();
		expect(d.getByText(/these settings always replace each knowledge base's own/)).toBeInTheDocument();
	});

	it("a ranking service without a model is refused here, with the rule beside the field", async () => {
		const { user } = openAdmin();
		const d = await openNew(user);
		await user.selectOptions(d.getByLabelText("Re-ranking"), "api");
		await user.type(d.getByLabelText("Name"), "Rank test");
		await user.type(d.getByLabelText("What owners see"), "Premium");
		await user.click(d.getByRole("button", { name: "Create profile" }));
		expect(await d.findByText(/needs a model name/)).toBeInTheDocument();
		expect(pipelinesState.sent).toEqual([]);
	});

	it("shows the engine's own sentence under the field it names", async () => {
		server.use(
			http.post(`${BASE}/api/v1/admin/pipelines`, () =>
				HttpResponse.json(
					{
						detail: "final_k (how many passages reach the answer) cannot be larger than top_k.",
						code: "invalid_profile",
					},
					{ status: 422 },
				),
			),
		);
		const { user } = openAdmin();
		const d = await openNew(user);
		await user.type(d.getByLabelText("Name"), "Deep");
		await user.type(d.getByLabelText("What owners see"), "Premium");
		await user.click(d.getByRole("button", { name: "Create profile" }));
		const msg = await d.findByText(/cannot be larger than top_k/);
		expect(msg.id).toMatch(/finalk-error$/);
	});

	it("creates a profile with the chosen priority and shows it in the list", async () => {
		const { user } = openAdmin();
		const d = await openNew(user);
		await user.type(d.getByLabelText("Name"), "Festive rush");
		await user.type(d.getByLabelText("What owners see"), "Premium+");
		await user.selectOptions(d.getByLabelText("Priority"), "high");
		await user.click(d.getByRole("button", { name: "Create profile" }));
		expect(await screen.findByRole("listitem", { name: "Festive rush" })).toBeInTheDocument();
		const sent = pipelinesState.sent.at(-1)?.body as { config: { priority: string; answer_model: unknown } };
		expect(sent.config.priority).toBe("high");
		expect(sent.config.answer_model).toBeNull();
	});

	it("edits a profile: a name already taken shows under the name", async () => {
		const { user } = openAdmin();
		const premium = await screen.findByRole("listitem", { name: "Premium" });
		await user.click(within(premium).getByRole("button", { name: "Edit Premium" }));
		const d = within(await screen.findByRole("dialog"));
		const name = d.getByLabelText("Name");
		await user.clear(name);
		await user.type(name, "Standard");
		await user.click(d.getByRole("button", { name: "Save changes" }));
		expect(await d.findByText("Another pipeline profile already has that name.")).toBeInTheDocument();
		await waitFor(() => expect(pipelinesState.profiles.find((p) => p.id === "pf-premium")?.name).toBe("Premium"));
	});
});
