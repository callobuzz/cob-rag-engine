import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, rt, SV, tokenResponse } from "@/test/msw/data";
import { type CobDocFixture, cobAdminState, resetCobAdmin } from "@/test/msw/handlers/cob-admin";
import { createEngineMock } from "@/test/msw/handlers/engine";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";

const BUILTIN: CobDocFixture = {
	id: "d1",
	name: "system:cob/screens/widget#",
	source: "builtin",
	audience: "customer",
	uploaded_by: null,
	ingested_at: "2026-10-04T08:00:00Z",
};
const HOURS: CobDocFixture = {
	id: "d2",
	name: "note:Support hours",
	source: "platform",
	audience: "customer",
	uploaded_by: null,
	ingested_at: "2026-10-04T09:00:00Z",
};

function open() {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user: SV, tenant: null, tenants: [] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () =>
			HttpResponse.json({ user: SV, tenant: null, tenants: [], session_id: "s" }),
		),
		http.delete(`${BASE}/api/v1/documents/:id`, ({ params }) => {
			cobAdminState.calls.push(`DELETE ${params.id}`);
			cobAdminState.docs = cobAdminState.docs.filter((d) => d.id !== params.id);
			return new HttpResponse(null, { status: 204 });
		}),
		...createEngineMock().handlers,
	);
	return renderApp("/admin/cob");
}

describe("COB admin page", () => {
	beforeEach(() => resetCobAdmin([BUILTIN, HOURS]));

	it("lists the built-in pages read-only and the platform pages on their own tab", async () => {
		open();
		const builtin = await screen.findByRole("region", { name: "Built-in docs" });
		expect(await within(builtin).findByText("system:cob/screens/widget#")).toBeInTheDocument();
		expect(within(builtin).queryByRole("button", { name: /delete/i })).toBeNull();
		await userEvent.click(screen.getByRole("tab", { name: "Platform docs" }));
		expect(await screen.findByRole("rowheader", { name: "Support hours" })).toBeInTheDocument();
		expect(screen.queryByText("system:cob/screens/widget#")).toBeNull();
	});

	it("changes who can see a platform page", async () => {
		open();
		await userEvent.click(await screen.findByRole("tab", { name: "Platform docs" }));
		const select = await screen.findByRole("combobox", { name: "Who can see Support hours" });
		await userEvent.selectOptions(select, "admin");
		await waitFor(() => expect(cobAdminState.calls).toContain("PATCH note:Support hours admin"));
	});

	it("deletes a platform page only after its name is typed", async () => {
		open();
		await userEvent.click(await screen.findByRole("tab", { name: "Platform docs" }));
		await userEvent.click(await screen.findByRole("button", { name: /Delete Support hours/ }));
		const dialog = await screen.findByRole("alertdialog");
		const confirm = within(dialog).getByRole("button", { name: "Delete page" });
		expect(confirm).toBeDisabled();
		await userEvent.type(within(dialog).getByRole("textbox"), "Support hours");
		await userEvent.click(confirm);
		await waitFor(() => expect(cobAdminState.calls).toContain("DELETE d2"));
	});
});
