import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, PRIYA, ROHAN, rt, SHREE, tokenResponse } from "../msw/data";
import { coded } from "../msw/handlers/auth";
import { teamState } from "../msw/handlers/members";
import { server } from "../msw/server";
import { renderApp } from "../render";

const MEMBER_SHREE = { ...SHREE, role: "member" as const };

function signedInAs(user: typeof PRIYA, tenant: typeof SHREE) {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user, tenant, tenants: [tenant] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () => HttpResponse.json({ user, tenant, tenants: [tenant], session_id: "s" })),
	);
}

async function openTeam(who: [typeof PRIYA, typeof SHREE] = [PRIYA, SHREE], path = "/team") {
	signedInAs(...who);
	const app = renderApp(path);
	await screen.findByRole("heading", { level: 1, name: "Team" });
	await screen.findByRole("table", { name: "Members of Shree Labs" });
	return app;
}

const rowOf = (name: string) => screen.getByRole("rowheader", { name: new RegExp(name) }).closest("tr") as HTMLElement;

describe("team: owner", () => {
	it("members with roles, last active, the 'you' chip and the last-owner lock", async () => {
		await openTeam();
		const priya = rowOf("Priya Kulkarni");
		expect(within(priya).getByText("you")).toBeInTheDocument();
		expect(within(priya).getByText("Owner")).toBeInTheDocument();
		expect(within(priya).getByText("Last owner")).toBeInTheDocument();
		expect(within(priya).queryByRole("button", { name: /Leave/ })).toBeNull();
		expect(within(rowOf("Rohan Deshmukh")).getByText("Never logged in")).toBeInTheDocument();
		expect(screen.getByText(/Priya Kulkarni is the only owner/)).toBeInTheDocument();
		expect(screen.getByRole("table", { name: "What owners and members can do" })).toBeInTheDocument();
		const invites = screen.getByRole("list", { name: "Pending invitations" });
		expect(within(invites).getByText("dr.nikhil@shree-labs.in")).toBeInTheDocument();
		expect(within(invites).getByText(/Member · invited by Priya Kulkarni · expires 3 Oct/)).toBeInTheDocument();
	});

	it("invite as member → {email, role}; toast; invitation listed", async () => {
		const app = await openTeam();
		await app.user.click(screen.getByRole("button", { name: "Invite teammate" }));
		const dialog = await screen.findByRole("dialog", { name: "Invite a teammate to Shree Labs" });
		await app.user.type(within(dialog).getByLabelText("Email"), "asha@shree-labs.in");
		await app.user.click(within(dialog).getByRole("button", { name: "Send invitation" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(teamState.sent[0]).toEqual({
			method: "POST",
			path: "/api/v1/tenants/me/members",
			body: { email: "asha@shree-labs.in", role: "member" },
		});
		expect(await screen.findByText("Invitation sent")).toBeInTheDocument();
		expect(
			await within(screen.getByRole("list", { name: "Pending invitations" })).findByText("asha@shree-labs.in"),
		).toBeInTheDocument();
	});

	it("invite errors: already_member on the field, e-mail not configured, rate limited", async () => {
		// ?invite=1 (the palette's "Invite a teammate") opens the dialog on arrival.
		signedInAs(PRIYA, SHREE);
		const app = renderApp("/team?invite=1");
		const dialog = await screen.findByRole("dialog", { name: "Invite a teammate to Shree Labs" });
		await app.user.type(within(dialog).getByLabelText("Email"), "rohan@shree-labs.in");
		await app.user.click(within(dialog).getByRole("button", { name: "Send invitation" }));
		expect(await within(dialog).findByText("That person is already in this workspace.")).toBeInTheDocument();

		teamState.emailConfigured = false;
		await app.user.clear(within(dialog).getByLabelText("Email"));
		await app.user.type(within(dialog).getByLabelText("Email"), "asha@shree-labs.in");
		await app.user.click(within(dialog).getByRole("button", { name: "Send invitation" }));
		expect(await within(dialog).findByRole("alert")).toHaveTextContent(
			"Email isn't set up on this engine; ask the operator to add them for you (support@sahayak.ai).",
		);

		server.use(
			http.post(`${BASE}/api/v1/tenants/me/members`, () => coded(429, "rate_limited", {}, { "Retry-After": "90" })),
		);
		await app.user.click(within(dialog).getByRole("button", { name: "Send invitation" }));
		expect((await within(dialog).findAllByText(/Too many attempts. Try again in/)).length).toBeGreaterThan(0);
		expect(within(dialog).getByRole("button", { name: "Send invitation" })).toHaveAttribute("aria-disabled", "true");
	});

	it("invite: a field error and the form alert go away as soon as the input is edited", async () => {
		signedInAs(PRIYA, SHREE);
		const app = renderApp("/team?invite=1");
		const dialog = await screen.findByRole("dialog", { name: "Invite a teammate to Shree Labs" });
		const email = within(dialog).getByLabelText("Email");
		await app.user.type(email, "not-an-email");
		await app.user.tab();
		expect(await within(dialog).findByText(/Enter an email address like/)).toBeInTheDocument();
		await app.user.clear(email);
		await app.user.type(email, "rohan@shree-labs.in");
		await waitFor(() => expect(within(dialog).queryByText(/Enter an email address like/)).toBeNull());
		await app.user.click(within(dialog).getByRole("button", { name: "Send invitation" }));
		expect(await within(dialog).findByText("That person is already in this workspace.")).toBeInTheDocument();
		await app.user.type(email, "x");
		await waitFor(() => expect(within(dialog).queryByText("That person is already in this workspace.")).toBeNull());
	});

	it("change role announces the new role; last_owner from the server is shown", async () => {
		const app = await openTeam();
		await app.user.click(within(rowOf("Dr. Meera Shah")).getByRole("button", { name: "Make owner Dr. Meera Shah" }));
		expect(await screen.findByText("Dr. Meera Shah is now an owner")).toBeInTheDocument();
		// Two owners now: Priya can leave or become a member.
		await waitFor(() => expect(within(rowOf("Priya Kulkarni")).queryByText("Last owner")).toBeNull());
		server.use(http.patch(`${BASE}/api/v1/tenants/me/members/:uid`, () => coded(409, "last_owner")));
		await app.user.click(within(rowOf("Priya Kulkarni")).getByRole("button", { name: "Make member Priya Kulkarni" }));
		expect(
			await screen.findByText("A workspace always keeps at least one owner. Make someone else an owner first."),
		).toBeInTheDocument();
	});

	it("remove: plain confirm, then the row goes", async () => {
		const app = await openTeam();
		await app.user.click(within(rowOf("Rohan Deshmukh")).getByRole("button", { name: "Remove Rohan Deshmukh" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Remove Rohan Deshmukh from Shree Labs?" });
		await app.user.click(within(dialog).getByRole("button", { name: "Remove" }));
		await waitFor(() => expect(screen.queryByRole("rowheader", { name: /Rohan/ })).toBeNull());
		expect(teamState.sent.at(-1)).toMatchObject({ method: "DELETE", path: `/api/v1/tenants/me/members/${ROHAN.id}` });
	});

	it("resend and revoke invitations", async () => {
		const app = await openTeam();
		await app.user.click(screen.getByRole("button", { name: "Resend invitation for dr.nikhil@shree-labs.in" }));
		expect(await screen.findByText("Invitation sent again")).toBeInTheDocument();
		await app.user.click(screen.getByRole("button", { name: "Revoke invitation for dr.nikhil@shree-labs.in" }));
		const dialog = await screen.findByRole("alertdialog", {
			name: "Revoke the invitation for dr.nikhil@shree-labs.in?",
		});
		await app.user.click(within(dialog).getByRole("button", { name: "Revoke" }));
		expect(await screen.findByText("No open invitations.")).toBeInTheDocument();
	});

	it("server refusals: owner_required and email_unavailable become messages", async () => {
		server.use(
			http.delete(`${BASE}/api/v1/tenants/me/members/:uid`, () => coded(403, "owner_required")),
			http.post(`${BASE}/api/v1/tenants/me/invitations/:id/resend`, () => coded(503, "email_unavailable")),
		);
		const app = await openTeam();
		await app.user.click(within(rowOf("Rohan Deshmukh")).getByRole("button", { name: "Remove Rohan Deshmukh" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Remove Rohan Deshmukh from Shree Labs?" });
		await app.user.click(within(dialog).getByRole("button", { name: "Remove" }));
		expect(await within(dialog).findByText("Only workspace owners can do this. Ask an owner.")).toBeInTheDocument();
		await app.user.click(within(dialog).getByRole("button", { name: "Cancel" }));
		await app.user.click(screen.getByRole("button", { name: "Resend invitation for dr.nikhil@shree-labs.in" }));
		expect(await screen.findByText("We couldn't queue the email. Try again shortly.")).toBeInTheDocument();
	});

	it("resend without e-mail says to ask the operator", async () => {
		teamState.emailConfigured = false;
		const app = await openTeam();
		await app.user.click(screen.getByRole("button", { name: "Resend invitation for dr.nikhil@shree-labs.in" }));
		expect(await screen.findByText(/ask the operator to add them for you/)).toBeInTheDocument();
	});
});

describe("team: member", () => {
	it("read-only: note, locked controls with the owner toast, Leave on the own row", async () => {
		const app = await openTeam([ROHAN, MEMBER_SHREE]);
		expect(screen.getByText(/You're a Member. Only owners can invite/)).toHaveTextContent("Ask Priya Kulkarni");
		const invite = screen.getByRole("button", { name: "Invite teammate" });
		expect(invite).toHaveAttribute("aria-disabled", "true");
		await app.user.click(invite);
		expect(await screen.findAllByText("Only owners can invite or change teammates")).not.toHaveLength(0);
		expect(screen.queryByRole("dialog")).toBeNull();
		expect(within(rowOf("Dr. Meera Shah")).getByRole("button", { name: "Remove Dr. Meera Shah" })).toHaveAttribute(
			"aria-disabled",
			"true",
		);
		expect(screen.getByRole("button", { name: "Revoke invitation for dr.nikhil@shree-labs.in" })).toHaveAttribute(
			"aria-disabled",
			"true",
		);
		const me = rowOf("Rohan Deshmukh");
		expect(within(me).getByText("you")).toBeInTheDocument();
		expect(within(me).getByRole("button", { name: "Leave workspace" })).not.toHaveAttribute("aria-disabled");
	});

	it("leave: plain confirm → DELETE own id → signed out to /login", async () => {
		const app = await openTeam([ROHAN, MEMBER_SHREE]);
		await app.user.click(within(rowOf("Rohan Deshmukh")).getByRole("button", { name: "Leave workspace" }));
		const dialog = await screen.findByRole("alertdialog", { name: "Leave Shree Labs?" });
		await app.user.click(within(dialog).getByRole("button", { name: "Leave workspace" }));
		await waitFor(() => expect(app.pathname()).toBe("/login"));
		expect(teamState.sent.at(-1)).toMatchObject({ method: "DELETE", path: `/api/v1/tenants/me/members/${ROHAN.id}` });
		expect(sessionStorage.getItem(RT_KEY)).toBeNull();
	});
});
