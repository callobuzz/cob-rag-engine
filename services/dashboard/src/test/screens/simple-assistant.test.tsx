import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { BASE, PRIYA, ROHAN, SHREE } from "../msw/data";
import { assistantStateFor } from "../msw/handlers/assistant";
import { server } from "../msw/server";
import { renderApp } from "../render";
import { capture, MEMBER_SHREE, PATIENT, signedInAs } from "./assistant-utils";

async function openSimple() {
	const app = renderApp("/assistant");
	await screen.findByRole("heading", { level: 1, name: "Assistant" });
	return app;
}

describe("simple Assistant page", () => {
	it("the picture and four plain parts in order, the publish bar on top, All settings beside the title", async () => {
		signedInAs(PRIYA, SHREE);
		await openSimple();
		await screen.findByTestId("simple-handoff");
		const order = [...document.querySelectorAll('[data-testid^="simple-"]')].map(
			(el) => el.querySelector("h2")?.textContent,
		);
		// BIZ4: the assistant's picture comes first
		expect(order).toEqual([
			"Picture",
			"Name and business details",
			"How it talks",
			"Languages",
			"When your assistant isn't sure",
		]);
		expect(screen.getByRole("button", { name: "Publish" })).toBeInTheDocument();
		const all = screen.getByRole("link", { name: "All settings" });
		expect(all).toHaveAttribute("href", `/kbs/${PATIENT}?tab=assistant`);
		// each part says in one line what it is for
		expect(screen.getByText(/Example: Shree Dental Assistant for Shree Dental Clinic, Pune/)).toBeInTheDocument();
	});

	it("the existing forms work here: rename the assistant, then publish with the draft revision", async () => {
		signedInAs(PRIYA, SHREE);
		const saved = capture((r) => r.method === "PUT" && r.url.endsWith("/assistant/draft"));
		const published = capture((r) => r.method === "POST" && r.url.endsWith("/assistant/publish"));
		const app = await openSimple();
		const identity = await screen.findByTestId("simple-identity");
		const name = await within(identity).findByLabelText("Assistant name");
		await app.user.clear(name);
		await app.user.type(name, "Shree Dental Assistant");
		await app.user.click(within(identity).getByRole("button", { name: /Save/ }));
		await waitFor(() => expect(saved).toHaveLength(1));
		expect(JSON.stringify(saved[0])).toContain("Shree Dental Assistant");
		await app.user.click(await screen.findByRole("button", { name: "Publish" }));
		await app.user.click(await screen.findByRole("button", { name: "Publish now" }));
		await waitFor(() => expect(published).toHaveLength(1));
	});

	it("a knowledge base with no assistant yet shows the template picker, like the tab", async () => {
		signedInAs(PRIYA, SHREE);
		const s = assistantStateFor(PATIENT);
		s.published = null;
		s.draft = null;
		await openSimple();
		expect(await screen.findByRole("button", { name: "Choose a template" })).toBeInTheDocument();
		expect(screen.queryByTestId("simple-identity")).toBeNull();
	});

	it("members edit but cannot publish", async () => {
		signedInAs(ROHAN, MEMBER_SHREE);
		await openSimple();
		const publish = await screen.findByRole("button", { name: "Publish" });
		expect(publish).toHaveAttribute("aria-disabled", "true");
	});

	it("without a knowledge base it asks to create one", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.get(`${BASE}/api/v1/corpora`, () =>
				HttpResponse.json({ items: [], count: 0, total: 0, offset: 0, limit: 500 }),
			),
		);
		renderApp("/assistant");
		expect(await screen.findByRole("heading", { name: "Create a knowledge base first" })).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Create a knowledge base" })).toHaveAttribute("href", "/kbs?new=1");
	});

	it("a failed load says so and can retry", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.get(`${BASE}/api/v1/corpora/${PATIENT}/assistant`, () =>
				HttpResponse.json({ detail: "boom", code: "internal" }, { status: 500 }),
			),
		);
		await openSimple();
		expect(await screen.findByText("We couldn't load your assistant.")).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
	});

	it("never says corpus", async () => {
		signedInAs(PRIYA, SHREE);
		await openSimple();
		await screen.findByTestId("simple-identity");
		expect(document.querySelector("main")?.textContent ?? "").not.toMatch(/corpus|corpora/i);
	});

	it("a failed knowledge base list is not mistaken for having none", async () => {
		signedInAs(PRIYA, SHREE);
		server.use(
			http.get(`${BASE}/api/v1/corpora`, () =>
				HttpResponse.json({ detail: "boom", code: "internal" }, { status: 500 }),
			),
		);
		renderApp("/assistant");
		expect(await screen.findByText("We couldn't load your assistant.")).toBeInTheDocument();
		expect(screen.queryByRole("heading", { name: "Create a knowledge base first" })).toBeNull();
	});
});
