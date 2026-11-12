import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PRIYA, SHREE } from "../msw/data";
import { DOC_FAQ, markSystemDocument } from "../msw/handlers/documents";
import { renderApp } from "../render";
import { PATIENT, signedInAs } from "./assistant-utils";

describe("The business card in Documents", () => {
	it("is badged in the list", async () => {
		markSystemDocument(DOC_FAQ);
		signedInAs(PRIYA, SHREE);
		renderApp(`/documents?kb=${PATIENT}`);
		const link = await screen.findByRole("link", { name: "Business card" });
		expect(
			within(link.closest("td") as HTMLElement).getByText("Business card", { selector: "span, div" }),
		).toBeInTheDocument();
	});

	it("has no delete or merge, and links to Assistant, Identity", async () => {
		markSystemDocument(DOC_FAQ);
		signedInAs(PRIYA, SHREE);
		renderApp(`/documents/${DOC_FAQ}?kb=${PATIENT}`);
		const edit = await screen.findByRole("link", { name: "Edit in Assistant, Identity" });
		expect(edit).toHaveAttribute("href", expect.stringContaining("tab=assistant"));
		expect(edit).toHaveAttribute("href", expect.stringContaining("section=identity"));
		expect(screen.queryByRole("button", { name: /More actions|Actions/i })).toBeNull();
		expect(screen.queryByText("Delete document")).toBeNull();
	});
});
