import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { BASE, PRIYA, ROHAN, SHREE } from "../msw/data";
import { assistantOffHandler, assistantStateFor } from "../msw/handlers/assistant";
import { server } from "../msw/server";
import { renderApp } from "../render";
import { capture, MEMBER_SHREE, openAssistant, PATIENT, signedInAs } from "./assistant-utils";

describe("Assistant tab", () => {
	it("old ?tab=prompt links open the Assistant tab", async () => {
		signedInAs(PRIYA, SHREE);
		renderApp(`/kbs/${PATIENT}?tab=prompt`);
		expect(await screen.findByRole("tab", { name: "Assistant", selected: true })).toBeInTheDocument();
	});

	it("the assistant is on; owners opt out after confirming", async () => {
		signedInAs(PRIYA, SHREE);
		const sent = capture((r) => r.method === "POST" && r.url.endsWith("/assistant/enabled"));
		const { user } = await openAssistant();
		expect(await screen.findByText("Classic instructions are not used while the assistant is on.")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Turn the assistant off" }));
		await user.click(await screen.findByRole("button", { name: "Turn it off" }));
		await waitFor(() => expect(sent).toEqual([{ enabled: false }]));
		// The draft keeps its own copy of the switch: it is re-saved off so publishing cannot revive it.
		await waitFor(() => expect(assistantStateFor(PATIENT).draft?.config.enabled).toBe(false));
		expect(assistantStateFor(PATIENT).published?.version).toBe(4);
	});

	it("switched off: a banner, a way back on, and the classic instructions", async () => {
		server.use(assistantOffHandler(PATIENT));
		signedInAs(PRIYA, SHREE);
		const sent = capture((r) => r.method === "POST" && r.url.endsWith("/assistant/enabled"));
		const { user } = await openAssistant();
		expect(await screen.findByText(/The assistant is off\./)).toBeInTheDocument();
		expect(await screen.findByLabelText("Instructions")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Turn the assistant back on" }));
		await waitFor(() => expect(sent).toEqual([{ enabled: true }]));
	});

	it("the template picker replaces the draft after confirming, with base_revision", async () => {
		signedInAs(PRIYA, SHREE);
		const sent = capture((r) => r.method === "POST" && r.url.endsWith("/draft/from-template"));
		const { user } = await openAssistant();
		await user.click(await screen.findByRole("button", { name: "Start from a template" }));
		const dialog = await screen.findByRole("dialog", { name: "Choose a template" });
		await waitFor(() => expect(within(dialog).getAllByRole("radio")).toHaveLength(8));
		await user.click(within(dialog).getByRole("radio", { name: /Shop assistant/ }));
		await user.click(within(dialog).getByRole("button", { name: "Use this template" }));
		await user.click(await screen.findByRole("button", { name: "Replace the draft" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(sent).toEqual([{ template: "store", keep_identity: true, base_revision: 3 }]);
	});

	it("members: Publish is locked with the reason; owners publish with a note", async () => {
		signedInAs(ROHAN, MEMBER_SHREE);
		const { user } = await openAssistant();
		const publish = await screen.findByRole("button", { name: "Publish" });
		expect(publish).toHaveAttribute("aria-disabled", "true");
		await user.click(publish);
		expect(await screen.findAllByText("Only owners can publish the assistant")).not.toHaveLength(0);
	});

	it("owner: Publish sends the draft revision and shows the new version", async () => {
		signedInAs(PRIYA, SHREE);
		const sent = capture((r) => r.method === "POST" && r.url.endsWith("/assistant/publish"));
		const { user } = await openAssistant();
		await user.click(await screen.findByRole("button", { name: "Publish" }));
		await user.type(screen.getByLabelText("Note (optional)"), "Sunday hours");
		await user.click(screen.getByRole("button", { name: "Publish now" }));
		expect(await screen.findByText(/Published version 4/)).toBeInTheDocument();
		expect(sent).toEqual([{ note: "Sunday hours", base_revision: 3 }]);
	});

	it("a publish refused for a missing encryption key explains the fix", async () => {
		server.use(
			http.post(`${BASE}/api/v1/corpora/${PATIENT}/assistant/publish`, () =>
				HttpResponse.json({ detail: "x", code: "encryption_not_configured" }, { status: 503 }),
			),
		);
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant();
		await user.click(await screen.findByRole("button", { name: "Publish" }));
		await user.click(screen.getByRole("button", { name: "Publish now" }));
		expect(
			await screen.findByText("Leads need an encryption key on the engine before they can be switched on."),
		).toBeInTheDocument();
	});

	it("section changes are in the URL", async () => {
		signedInAs(PRIYA, SHREE);
		const app = await openAssistant();
		const nav = await screen.findByRole("navigation", { name: "Assistant settings" });
		await app.user.click(within(nav).getByRole("link", { name: "Identity" }));
		await waitFor(() =>
			expect(app.router.state.location.search).toMatchObject({ tab: "assistant", section: "identity" }),
		);
	});

	it("discarding the draft needs a confirmation and keeps the live version", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant();
		await user.click(await screen.findByRole("button", { name: "Discard draft" }));
		await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Discard draft" }));
		await waitFor(() => expect(assistantStateFor(PATIENT).draft).toBeNull());
		expect(assistantStateFor(PATIENT).published?.version).toBe(3);
	});
});
