import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import type { Home } from "@/features/home/api";
import { BASE, PRIYA, SHREE } from "../msw/data";
import { homeFor, PATIENT } from "../msw/handlers/home";
import { server } from "../msw/server";
import { renderApp } from "../render";
import { MEMBER_SHREE, signedInAs } from "./assistant-utils";

/** TV5: the Home nudges (brochures, a way to reach the business, lead value). */

type Kind = NonNullable<Home["nudges"]>[number]["kind"];

function homeWith(kinds: Kind[]) {
	server.use(
		http.get(`${BASE}/api/v1/home`, () =>
			HttpResponse.json({ ...homeFor(PATIENT), nudges: kinds.map((kind) => ({ kind })) }),
		),
	);
}

afterEach(() => window.localStorage.clear());

describe("Home: worth doing next", () => {
	it("shows each nudge with what to do, most important first", async () => {
		signedInAs(PRIYA, SHREE);
		homeWith(["no_human_path", "add_brochures", "set_values"]);
		renderApp("/");
		const list = await screen.findByTestId("home-nudges");
		const items = within(list).getAllByRole("listitem");
		expect(items.map((i) => i.getAttribute("data-testid"))).toEqual([
			"nudge-no_human_path",
			"nudge-add_brochures",
			"nudge-set_values",
		]);
		expect(
			within(items[0] as HTMLElement).getByText("Visitors who ask for a person can't reach you"),
		).toBeInTheDocument();
		expect(within(items[0] as HTMLElement).getByRole("link", { name: "Open your assistant" })).toHaveAttribute(
			"href",
			"/assistant",
		);
		expect(within(items[1] as HTMLElement).getByRole("link", { name: "Add files" })).toHaveAttribute(
			"href",
			"/knowledge?tab=articles",
		);
		expect(within(items[2] as HTMLElement).getByRole("link", { name: "Set my values" })).toHaveAttribute(
			"href",
			"/value?edit=1",
		);
	});

	it("shows nothing when the engine has nothing to suggest", async () => {
		signedInAs(PRIYA, SHREE);
		homeWith([]);
		renderApp("/");
		await screen.findByTestId("greeting");
		expect(screen.queryByTestId("home-nudges")).toBeNull();
	});

	it("a nudge can be hidden, and stays hidden on this browser", async () => {
		signedInAs(PRIYA, SHREE);
		homeWith(["add_brochures", "set_values"]);
		const app = renderApp("/");
		const first = await screen.findByTestId("nudge-add_brochures");
		await app.user.click(within(first).getByRole("button", { name: "Hide “Add your brochures and price lists”" }));
		expect(screen.queryByTestId("nudge-add_brochures")).toBeNull();
		expect(screen.getByTestId("nudge-set_values")).toBeInTheDocument();
		expect(window.localStorage.getItem("home-nudge-dismissed:add_brochures")).toBe("1");
	});

	it("a nudge hidden earlier is not shown again", async () => {
		signedInAs(PRIYA, SHREE);
		window.localStorage.setItem("home-nudge-dismissed:add_brochures", "1");
		homeWith(["add_brochures"]);
		renderApp("/");
		await screen.findByTestId("greeting");
		expect(screen.queryByTestId("home-nudges")).toBeNull();
	});

	it("a team member sees no nudges: they cannot change any of it", async () => {
		signedInAs(PRIYA, MEMBER_SHREE);
		homeWith(["add_brochures", "no_human_path"]);
		renderApp("/");
		await screen.findByTestId("greeting");
		expect(screen.queryByTestId("home-nudges")).toBeNull();
	});

	it("never says corpus, tenant or ledger", async () => {
		signedInAs(PRIYA, SHREE);
		homeWith(["no_human_path", "add_brochures", "set_values"]);
		renderApp("/");
		const list = await screen.findByTestId("home-nudges");
		expect(list.textContent ?? "").not.toMatch(/corpus|tenant|ledger|handoff/i);
	});
});
